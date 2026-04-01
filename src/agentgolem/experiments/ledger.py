"""Persistence helpers for self-improvement experiments."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from agentgolem.experiments.models import ExperimentStatus, ImprovementExperiment

if TYPE_CHECKING:
    from pathlib import Path

    from agentgolem.logging.audit import AuditLogger


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ExperimentCommandStatus(StrEnum):
    """Normalized command-level outcomes for experiment evaluation."""

    PASSED = "passed"
    FAILED = "failed"
    CRASHED = "crashed"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"


class ExperimentMetricObservation(BaseModel):
    """One observed metric value for a candidate experiment run."""

    name: str = Field(min_length=1)
    value: float
    baseline_value: float | None = None
    unit: str = ""

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Metric observation names must not be empty.")
        return clean

    @property
    def delta(self) -> float | None:
        """Return actual-baseline delta when a baseline is available."""
        if self.baseline_value is None:
            return None
        return self.value - self.baseline_value


class ExperimentCommandOutcome(BaseModel):
    """One evaluation-command outcome captured by the experiment runner."""

    name: str = Field(min_length=1)
    status: ExperimentCommandStatus
    duration_seconds: float = Field(ge=0.0)
    exit_code: int | None = None
    summary: str = ""

    @field_validator("name", "summary")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()


class ExperimentRunRecord(BaseModel):
    """Append-only record of one experiment evaluation run."""

    experiment_id: str = Field(min_length=1)
    recorded_at: str = Field(default_factory=_now_iso)
    recorded_by: str = "system"
    status: ExperimentStatus
    baseline_ref: str = Field(min_length=1)
    candidate_ref: str = ""
    metrics: list[ExperimentMetricObservation] = Field(default_factory=list)
    command_outcomes: list[ExperimentCommandOutcome] = Field(default_factory=list)
    notes: str = ""

    @field_validator("experiment_id", "recorded_by", "baseline_ref", "candidate_ref", "notes")
    @classmethod
    def _strip_fields(cls, value: str) -> str:
        return value.strip()


def experiments_dir(data_dir: Path) -> Path:
    """Return the canonical root directory for experiment data."""
    root = data_dir / "experiments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def active_experiments_dir(data_dir: Path) -> Path:
    """Return the directory for non-terminal experiment snapshots."""
    path = experiments_dir(data_dir) / "active"
    path.mkdir(parents=True, exist_ok=True)
    return path


def completed_experiments_dir(data_dir: Path) -> Path:
    """Return the directory for terminal experiment snapshots."""
    path = experiments_dir(data_dir) / "completed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def experiment_history_path(data_dir: Path) -> Path:
    """Return the append-only JSONL path for experiment run records."""
    return experiments_dir(data_dir) / "history.jsonl"


class ExperimentLedger:
    """Store experiment snapshots and append-only run records."""

    def __init__(self, data_dir: Path, audit_logger: AuditLogger | None = None) -> None:
        self._data_dir = data_dir
        self._audit_logger = audit_logger

    def _active_path(self, experiment_id: str) -> Path:
        return active_experiments_dir(self._data_dir) / f"{experiment_id}.json"

    def _completed_path(self, experiment_id: str) -> Path:
        return completed_experiments_dir(self._data_dir) / f"{experiment_id}.json"

    def save_experiment(self, experiment: ImprovementExperiment) -> Path:
        """Persist the latest snapshot for an experiment."""
        active_path = self._active_path(experiment.id)
        completed_path = self._completed_path(experiment.id)
        target = completed_path if experiment.is_terminal else active_path
        stale = active_path if target == completed_path else completed_path
        if stale.exists():
            stale.unlink()
        payload = json.dumps(experiment.model_dump(mode="json"), indent=2)
        target.write_text(payload, encoding="utf-8")
        self._log_snapshot_save(experiment)
        return target

    def load_experiment(self, experiment_id: str) -> ImprovementExperiment | None:
        """Load one experiment snapshot from active or completed storage."""
        for path in (self._active_path(experiment_id), self._completed_path(experiment_id)):
            if not path.exists():
                continue
            try:
                return ImprovementExperiment.model_validate_json(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                return None
        return None

    def list_experiments(self, *, include_completed: bool = True) -> list[ImprovementExperiment]:
        """List persisted experiments ordered by creation time."""
        paths = sorted(active_experiments_dir(self._data_dir).glob("*.json"))
        if include_completed:
            paths.extend(sorted(completed_experiments_dir(self._data_dir).glob("*.json")))

        experiments: list[ImprovementExperiment] = []
        for path in paths:
            try:
                experiments.append(
                    ImprovementExperiment.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, ValueError):
                continue
        return sorted(experiments, key=lambda experiment: experiment.created_at)

    def append_record(self, record: ExperimentRunRecord) -> None:
        """Append one immutable run record to the experiment history JSONL."""
        path = experiment_history_path(self._data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")
        self._log_run_record(record)

    def load_records(
        self,
        *,
        limit: int = 50,
        experiment_id: str | None = None,
    ) -> list[ExperimentRunRecord]:
        """Load recent run records, optionally filtered to one experiment."""
        path = experiment_history_path(self._data_dir)
        if not path.exists():
            return []

        records: list[ExperimentRunRecord] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = ExperimentRunRecord.model_validate_json(stripped)
                except (json.JSONDecodeError, ValueError):
                    continue
                if experiment_id and record.experiment_id != experiment_id:
                    continue
                records.append(record)
        return records[-limit:]

    def _log_snapshot_save(self, experiment: ImprovementExperiment) -> None:
        if self._audit_logger is None:
            return
        self._audit_logger.log(
            "experiment_saved",
            experiment.id,
            {
                "title": experiment.title,
                "status": experiment.status.value,
                "baseline_ref": experiment.baseline_ref,
                "candidate_ref": experiment.candidate_ref,
            },
            actor=experiment.proposed_by or "agent",
        )

    def _log_run_record(self, record: ExperimentRunRecord) -> None:
        if self._audit_logger is None:
            return
        self._audit_logger.log(
            "experiment_recorded",
            record.experiment_id,
            {
                "status": record.status.value,
                "baseline_ref": record.baseline_ref,
                "candidate_ref": record.candidate_ref,
                "metric_count": len(record.metrics),
                "command_count": len(record.command_outcomes),
            },
            actor=record.recorded_by or "system",
        )
