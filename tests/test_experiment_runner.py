"""Tests for the fixed-budget experiment runner."""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from agentgolem.experiments.ledger import ExperimentLedger, ExperimentMetricObservation
from agentgolem.experiments.models import (
    ExperimentBudget,
    ExperimentCommand,
    ExperimentMetric,
    ExperimentMetricGoal,
    ExperimentScopePolicy,
    ExperimentStatus,
    ImprovementExperiment,
)
from agentgolem.experiments.runner import ExperimentRunner

if TYPE_CHECKING:
    from pathlib import Path


def _python_command(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


def _make_experiment(
    tmp_path: Path,
    *commands: ExperimentCommand,
    budget: ExperimentBudget | None = None,
) -> ImprovementExperiment:
    return ImprovementExperiment(
        title="Run experiment commands",
        proposed_by="Council-4",
        baseline_ref="HEAD",
        candidate_ref="candidate-456",
        scope=ExperimentScopePolicy(allowed_prefixes=["src/agentgolem/runtime"]),
        metrics=[
            ExperimentMetric(
                name="health_score_delta",
                goal=ExperimentMetricGoal.MAXIMIZE,
                primary=True,
            )
        ],
        evaluation_commands=list(commands),
        budget=budget or ExperimentBudget(time_budget_seconds=5.0, command_timeout_seconds=5.0),
    )


async def test_experiment_runner_records_successful_evaluation(tmp_path: Path) -> None:
    ledger = ExperimentLedger(tmp_path)
    runner = ExperimentRunner(tmp_path, ledger=ledger)
    experiment = _make_experiment(
        tmp_path,
        ExperimentCommand(
            name="write-marker",
            command=_python_command(
                "from pathlib import Path; Path('marker.txt').write_text('ok', encoding='utf-8')"
            ),
        ),
    )

    async def collect_metrics(
        exp: ImprovementExperiment,
        outcomes: list,
        repo_root: Path,
    ) -> list[ExperimentMetricObservation]:
        assert exp.id == experiment.id
        assert len(outcomes) == 1
        assert (repo_root / "marker.txt").read_text(encoding="utf-8") == "ok"
        return [
            ExperimentMetricObservation(
                name="health_score_delta",
                value=0.08,
                baseline_value=0.01,
            )
        ]

    result = await runner.run(experiment, metric_collector=collect_metrics)

    assert result.experiment.status == ExperimentStatus.EVALUATED
    assert result.record.status == ExperimentStatus.EVALUATED
    assert result.record.command_outcomes[0].exit_code == 0
    assert result.record.metrics[0].delta == 0.07
    assert ledger.load_experiment(experiment.id).status == ExperimentStatus.EVALUATED
    assert ledger.load_records(experiment_id=experiment.id)[0].status == ExperimentStatus.EVALUATED


async def test_experiment_runner_discards_on_required_command_failure(tmp_path: Path) -> None:
    ledger = ExperimentLedger(tmp_path)
    runner = ExperimentRunner(tmp_path, ledger=ledger)
    marker = tmp_path / "should_not_exist.txt"
    experiment = _make_experiment(
        tmp_path,
        ExperimentCommand(
            name="fail",
            command=_python_command("import sys; sys.exit(2)"),
        ),
        ExperimentCommand(
            name="skipped-after-failure",
            command=_python_command(
                "from pathlib import Path; "
                "Path('should_not_exist.txt').write_text('ran', encoding='utf-8')"
            ),
        ),
    )

    result = await runner.run(experiment)

    assert result.experiment.status == ExperimentStatus.DISCARDED
    assert result.record.command_outcomes[0].status.value == "failed"
    assert len(result.record.command_outcomes) == 1
    assert not marker.exists()


async def test_experiment_runner_blocks_on_timeout(tmp_path: Path) -> None:
    ledger = ExperimentLedger(tmp_path)
    runner = ExperimentRunner(tmp_path, ledger=ledger)
    experiment = _make_experiment(
        tmp_path,
        ExperimentCommand(
            name="sleep-too-long",
            command=_python_command("import time; time.sleep(0.5)"),
            timeout_seconds=0.1,
        ),
        budget=ExperimentBudget(time_budget_seconds=1.0, command_timeout_seconds=1.0),
    )

    result = await runner.run(experiment)

    assert result.experiment.status == ExperimentStatus.BLOCKED
    assert result.record.command_outcomes[0].status.value == "timed_out"
