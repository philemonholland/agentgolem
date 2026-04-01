"""Typed models for AgentGolem self-improvement experiments."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator, model_validator

if TYPE_CHECKING:
    from collections.abc import Iterable


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_experiment_id() -> str:
    return f"exp_{uuid.uuid4().hex[:12]}"


def normalize_repo_relative_path(rel_path: str) -> str:
    """Normalize a repo-relative path and reject traversal."""
    clean = rel_path.replace("\\", "/").strip("/")
    if not clean:
        raise ValueError("Path must not be empty.")
    parts = clean.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Path must stay within the repository root.")
    return clean


def _normalize_unique_paths(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        clean = normalize_repo_relative_path(str(value))
        if clean not in normalized:
            normalized.append(clean)
    return normalized


def _normalize_resource_tags(values: Iterable[str]) -> list[str]:
    tags: list[str] = []
    for value in values:
        clean = str(value).strip()
        if not clean:
            raise ValueError("Resource tags must not be empty.")
        if clean not in tags:
            tags.append(clean)
    return tags


class ExperimentStatus(StrEnum):
    """Lifecycle states for a self-improvement experiment."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    RUNNING = "running"
    EVALUATED = "evaluated"
    KEPT = "kept"
    DISCARDED = "discarded"
    CRASHED = "crashed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ExperimentApprovalStatus(StrEnum):
    """Operator-approval state for an experiment run."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class ExperimentMetricGoal(StrEnum):
    """Optimization direction for an experiment metric."""

    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    MATCH = "match"


class ExperimentScopePolicy(BaseModel):
    """Allowlisted mutation scope for an experiment."""

    allowed_paths: list[str] = Field(default_factory=list)
    allowed_prefixes: list[str] = Field(default_factory=list)

    @field_validator("allowed_paths", "allowed_prefixes", mode="before")
    @classmethod
    def _normalize_paths(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return _normalize_unique_paths([value])
        return _normalize_unique_paths(value)

    @model_validator(mode="after")
    def _validate_scope(self) -> ExperimentScopePolicy:
        if not self.allowed_paths and not self.allowed_prefixes:
            raise ValueError("Experiment scope must allow at least one path or prefix.")
        return self

    def allows(self, rel_path: str, *, protected_paths: Iterable[str] = ()) -> bool:
        """Return True when a repo-relative path is inside the allowed scope."""
        try:
            clean = normalize_repo_relative_path(rel_path)
            blocked = _normalize_unique_paths(protected_paths)
        except ValueError:
            return False

        for protected in blocked:
            if clean == protected or clean.startswith(f"{protected}/"):
                return False

        if clean in self.allowed_paths:
            return True

        return any(
            clean == prefix or clean.startswith(f"{prefix}/")
            for prefix in self.allowed_prefixes
        )


class ExperimentMetric(BaseModel):
    """A metric tracked when deciding whether to keep a candidate change."""

    name: str = Field(min_length=1)
    goal: ExperimentMetricGoal = ExperimentMetricGoal.MINIMIZE
    unit: str = ""
    primary: bool = False
    notes: str = ""

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Metric name must not be empty.")
        return clean


class ExperimentCommand(BaseModel):
    """A command the experiment runner will execute to evaluate a candidate."""

    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    timeout_seconds: float = Field(default=600.0, gt=0.0)
    required: bool = True
    working_directory: str = ""

    @field_validator("name", "command")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Command fields must not be empty.")
        return clean

    @field_validator("working_directory")
    @classmethod
    def _normalize_working_directory(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            return ""
        return normalize_repo_relative_path(clean)


class ExperimentChange(BaseModel):
    """One candidate repo edit that may later be forwarded for council review."""

    file_path: str = Field(min_length=1)
    old_content: str = ""
    new_content: str = Field(min_length=1)
    description: str = ""

    @field_validator("file_path")
    @classmethod
    def _normalize_file_path(cls, value: str) -> str:
        return normalize_repo_relative_path(value)

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str) -> str:
        return value.strip()


class ExperimentBudget(BaseModel):
    """Time and resource constraints for a self-improvement run."""

    time_budget_seconds: float = Field(default=300.0, gt=0.0)
    command_timeout_seconds: float = Field(default=600.0, gt=0.0)
    requires_operator_approval: bool = True
    exclusive_resources: list[str] = Field(default_factory=list)

    @field_validator("exclusive_resources", mode="before")
    @classmethod
    def _normalize_resources(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return _normalize_resource_tags([value])
        return _normalize_resource_tags(value)


class ImprovementExperiment(BaseModel):
    """A proposed or executed self-improvement experiment."""

    id: str = Field(default_factory=_new_experiment_id)
    title: str = Field(min_length=1)
    description: str = ""
    proposed_by: str = Field(min_length=1)
    baseline_ref: str = Field(min_length=1)
    candidate_ref: str = ""
    rationale: str = ""
    decision_reason: str = ""
    status: ExperimentStatus = ExperimentStatus.PROPOSED
    scope: ExperimentScopePolicy
    candidate_changes: list[ExperimentChange] = Field(default_factory=list)
    metrics: list[ExperimentMetric] = Field(min_length=1)
    evaluation_commands: list[ExperimentCommand] = Field(min_length=1)
    budget: ExperimentBudget = Field(default_factory=ExperimentBudget)
    approval_request_id: str = ""
    approval_status: ExperimentApprovalStatus = ExperimentApprovalStatus.NOT_REQUIRED
    review_proposal_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now_iso)
    started_at: str | None = None
    completed_at: str | None = None

    @field_validator(
        "title",
        "description",
        "proposed_by",
        "baseline_ref",
        "candidate_ref",
        "rationale",
        "decision_reason",
        "approval_request_id",
    )
    @classmethod
    def _strip_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("review_proposal_ids", mode="before")
    @classmethod
    def _normalize_review_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        values = [value] if isinstance(value, str) else [str(item) for item in value]
        normalized: list[str] = []
        for item in values:
            clean = item.strip()
            if clean and clean not in normalized:
                normalized.append(clean)
        return normalized

    @model_validator(mode="after")
    def _validate_metrics(self) -> ImprovementExperiment:
        primary_count = sum(1 for metric in self.metrics if metric.primary)
        if primary_count > 1:
            raise ValueError("Experiments may define at most one primary metric.")
        for change in self.candidate_changes:
            if not self.scope.allows(change.file_path):
                raise ValueError(
                    f"Candidate change path '{change.file_path}' is outside the experiment scope."
                )
        return self

    @property
    def primary_metric(self) -> ExperimentMetric:
        """Return the primary metric, or the first metric when none is flagged."""
        for metric in self.metrics:
            if metric.primary:
                return metric
        return self.metrics[0]

    @property
    def is_terminal(self) -> bool:
        """Return True when the experiment has reached a terminal state."""
        return self.status in {
            ExperimentStatus.KEPT,
            ExperimentStatus.DISCARDED,
            ExperimentStatus.CRASHED,
            ExperimentStatus.BLOCKED,
            ExperimentStatus.CANCELLED,
        }
