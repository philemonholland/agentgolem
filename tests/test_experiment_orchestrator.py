"""Tests for guarded experiment orchestration and council forwarding."""
from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from agentgolem.experiments.guardrails import ExperimentPolicy, ExperimentResourceManager
from agentgolem.experiments.models import (
    ExperimentBudget,
    ExperimentChange,
    ExperimentCommand,
    ExperimentMetric,
    ExperimentMetricGoal,
    ExperimentScopePolicy,
    ExperimentStatus,
    ImprovementExperiment,
)
from agentgolem.experiments.orchestrator import ExperimentOrchestrator
from agentgolem.tools.base import ApprovalGate

if TYPE_CHECKING:
    from pathlib import Path


def _python_command(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


def _make_experiment(
    *,
    command: str,
    budget: ExperimentBudget | None = None,
    candidate_changes: list[ExperimentChange] | None = None,
    status: ExperimentStatus = ExperimentStatus.PROPOSED,
) -> ImprovementExperiment:
    return ImprovementExperiment(
        title="Guarded runtime tweak",
        description="Compare one narrow runtime change against the baseline.",
        proposed_by="Council-1",
        baseline_ref="HEAD",
        candidate_ref="candidate-guarded",
        status=status,
        scope=ExperimentScopePolicy(allowed_prefixes=["src/agentgolem/runtime"]),
        candidate_changes=candidate_changes or [],
        metrics=[
            ExperimentMetric(
                name="health_score_delta",
                goal=ExperimentMetricGoal.MAXIMIZE,
                primary=True,
            )
        ],
        evaluation_commands=[ExperimentCommand(name="eval", command=command, timeout_seconds=2.0)],
        budget=budget or ExperimentBudget(
            time_budget_seconds=2.0,
            command_timeout_seconds=2.0,
            requires_operator_approval=False,
        ),
    )


async def test_prepare_blocks_disallowed_commands(tmp_path: Path) -> None:
    orchestrator = ExperimentOrchestrator(
        tmp_path,
        tmp_path,
        policy=ExperimentPolicy(
            allowed_command_prefixes=["pytest"],
            default_exclusive_resources=[],
        ),
    )
    experiment = _make_experiment(command=_python_command("print('nope')"))

    prepared = orchestrator.prepare(experiment)

    assert prepared.status == ExperimentStatus.BLOCKED
    assert "not allowlisted" in prepared.decision_reason


async def test_run_requests_approval_then_executes_once_approved(tmp_path: Path) -> None:
    gate = ApprovalGate(tmp_path / "approvals", ["experiment_run"])
    orchestrator = ExperimentOrchestrator(
        tmp_path,
        tmp_path,
        approval_gate=gate,
        policy=ExperimentPolicy(
            allowed_command_prefixes=[f'"{sys.executable}" -c'],
            default_exclusive_resources=[],
        ),
    )
    experiment = _make_experiment(
        command=_python_command("from pathlib import Path; Path('marker.txt').write_text('ok')"),
        budget=ExperimentBudget(
            time_budget_seconds=2.0,
            command_timeout_seconds=2.0,
            requires_operator_approval=True,
        ),
    )

    first = await orchestrator.run(experiment)

    assert first.record is None
    assert first.experiment.approval_request_id
    assert first.experiment.status == ExperimentStatus.PROPOSED

    gate.approve(first.experiment.approval_request_id, "Looks safe.")
    second = await orchestrator.run(first.experiment)

    assert second.record is not None
    assert second.experiment.status == ExperimentStatus.EVALUATED
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "ok"


async def test_run_blocks_when_required_resource_is_busy(tmp_path: Path) -> None:
    resource_manager = ExperimentResourceManager(tmp_path)
    lease = resource_manager.acquire("other-exp", ["experiment-runner"])
    orchestrator = ExperimentOrchestrator(
        tmp_path,
        tmp_path,
        policy=ExperimentPolicy(
            allowed_command_prefixes=[f'"{sys.executable}" -c'],
            default_exclusive_resources=["experiment-runner"],
        ),
    )
    experiment = _make_experiment(command=_python_command("print('ready')"))

    try:
        result = await orchestrator.run(experiment)
    finally:
        resource_manager.release(lease)

    assert result.record is not None
    assert result.experiment.status == ExperimentStatus.BLOCKED
    assert "resources are currently busy" in result.experiment.decision_reason.lower()


def test_forward_to_council_review_creates_evolution_proposals(tmp_path: Path) -> None:
    target = tmp_path / "src" / "agentgolem" / "runtime" / "loop.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old branch\n", encoding="utf-8")

    orchestrator = ExperimentOrchestrator(tmp_path, tmp_path)
    experiment = _make_experiment(
        command="pytest -q",
        status=ExperimentStatus.EVALUATED,
        candidate_changes=[
            ExperimentChange(
                file_path="src/agentgolem/runtime/loop.py",
                old_content="old branch",
                new_content="new branch",
                description="Replace one runtime branch after successful evaluation.",
            )
        ],
    )

    kept = orchestrator.forward_to_council_review(experiment)

    assert kept.status == ExperimentStatus.KEPT
    assert len(kept.review_proposal_ids) == 1

    proposal_path = tmp_path / "evolution_proposals" / f"{kept.review_proposal_ids[0]}.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert proposal["status"] == "pending"
    assert proposal["experiment_id"] == experiment.id
    assert proposal["file_path"] == "src/agentgolem/runtime/loop.py"
