"""Tests for self-improvement experiment ledger persistence."""
from __future__ import annotations

from typing import TYPE_CHECKING

from agentgolem.experiments.ledger import (
    ExperimentCommandOutcome,
    ExperimentCommandStatus,
    ExperimentLedger,
    ExperimentMetricObservation,
    ExperimentRunRecord,
    active_experiments_dir,
    completed_experiments_dir,
)
from agentgolem.experiments.models import (
    ExperimentCommand,
    ExperimentMetric,
    ExperimentScopePolicy,
    ExperimentStatus,
    ImprovementExperiment,
)
from agentgolem.logging.audit import AuditLogger

if TYPE_CHECKING:
    from pathlib import Path


def _make_experiment(
    *,
    status: ExperimentStatus = ExperimentStatus.PROPOSED,
) -> ImprovementExperiment:
    return ImprovementExperiment(
        title="Tune browse follow-on heuristic",
        description="Compare a small runtime tweak against the current baseline.",
        proposed_by="Council-1",
        baseline_ref="HEAD",
        candidate_ref="candidate-123",
        status=status,
        scope=ExperimentScopePolicy(allowed_prefixes=["src/agentgolem/runtime"]),
        metrics=[ExperimentMetric(name="health_score_delta", primary=True)],
        evaluation_commands=[
            ExperimentCommand(
                name="focused-runtime-tests",
                command=".venv\\Scripts\\python.exe -m pytest tests\\test_runtime_loop.py -q",
            )
        ],
    )


def test_ledger_saves_and_lists_active_experiments(tmp_path: Path) -> None:
    ledger = ExperimentLedger(tmp_path)
    experiment = _make_experiment()

    saved_path = ledger.save_experiment(experiment)
    listed = ledger.list_experiments()
    loaded = ledger.load_experiment(experiment.id)

    assert saved_path == active_experiments_dir(tmp_path) / f"{experiment.id}.json"
    assert [item.id for item in listed] == [experiment.id]
    assert loaded is not None
    assert loaded.id == experiment.id
    assert loaded.status == ExperimentStatus.PROPOSED


def test_ledger_moves_terminal_experiments_to_completed_storage(tmp_path: Path) -> None:
    ledger = ExperimentLedger(tmp_path)
    active = _make_experiment()
    terminal = _make_experiment(status=ExperimentStatus.KEPT)

    ledger.save_experiment(active)
    saved_path = ledger.save_experiment(terminal)

    assert not (active_experiments_dir(tmp_path) / f"{terminal.id}.json").exists()
    assert saved_path == completed_experiments_dir(tmp_path) / f"{terminal.id}.json"
    assert ledger.load_experiment(terminal.id) is not None
    assert ledger.load_experiment(terminal.id).status == ExperimentStatus.KEPT


def test_ledger_appends_and_filters_run_records(tmp_path: Path) -> None:
    ledger = ExperimentLedger(tmp_path)
    first = ExperimentRunRecord(
        experiment_id="exp_one",
        recorded_by="Council-2",
        status=ExperimentStatus.DISCARDED,
        baseline_ref="HEAD~1",
        candidate_ref="exp-one-candidate",
        metrics=[
            ExperimentMetricObservation(
                name="health_score_delta",
                value=-0.02,
                baseline_value=0.0,
            )
        ],
        command_outcomes=[
            ExperimentCommandOutcome(
                name="focused-tests",
                status=ExperimentCommandStatus.FAILED,
                duration_seconds=12.5,
                exit_code=1,
                summary="Regression in runtime loop tests.",
            )
        ],
    )
    second = ExperimentRunRecord(
        experiment_id="exp_two",
        recorded_by="Council-3",
        status=ExperimentStatus.KEPT,
        baseline_ref="HEAD",
        candidate_ref="exp-two-candidate",
        metrics=[
            ExperimentMetricObservation(
                name="health_score_delta",
                value=0.08,
                baseline_value=0.01,
            )
        ],
        command_outcomes=[
            ExperimentCommandOutcome(
                name="focused-tests",
                status=ExperimentCommandStatus.PASSED,
                duration_seconds=9.2,
                exit_code=0,
                summary="Focused tests passed cleanly.",
            )
        ],
    )

    ledger.append_record(first)
    ledger.append_record(second)

    all_records = ledger.load_records(limit=10)
    filtered = ledger.load_records(limit=10, experiment_id="exp_two")

    assert [record.experiment_id for record in all_records] == ["exp_one", "exp_two"]
    assert [record.experiment_id for record in filtered] == ["exp_two"]
    assert filtered[0].metrics[0].delta == 0.07


def test_ledger_emits_audit_entries_for_snapshots_and_run_records(tmp_path: Path) -> None:
    audit = AuditLogger(tmp_path)
    ledger = ExperimentLedger(tmp_path, audit_logger=audit)
    experiment = _make_experiment()
    record = ExperimentRunRecord(
        experiment_id=experiment.id,
        recorded_by="Council-1",
        status=ExperimentStatus.KEPT,
        baseline_ref="HEAD",
        candidate_ref="candidate-123",
        metrics=[
            ExperimentMetricObservation(
                name="health_score_delta",
                value=0.1,
                baseline_value=0.0,
            )
        ],
        command_outcomes=[
            ExperimentCommandOutcome(
                name="focused-tests",
                status=ExperimentCommandStatus.PASSED,
                duration_seconds=8.0,
                exit_code=0,
                summary="Focused validation passed.",
            )
        ],
    )

    ledger.save_experiment(experiment)
    ledger.append_record(record)
    entries = audit.read(limit=10)

    assert entries[0]["mutation_type"] == "experiment_recorded"
    assert entries[0]["target_id"] == experiment.id
    assert entries[1]["mutation_type"] == "experiment_saved"
    assert entries[1]["target_id"] == experiment.id
