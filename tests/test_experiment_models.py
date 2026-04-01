"""Tests for self-improvement experiment models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentgolem.experiments.models import (
    ExperimentApprovalStatus,
    ExperimentBudget,
    ExperimentChange,
    ExperimentCommand,
    ExperimentMetric,
    ExperimentMetricGoal,
    ExperimentScopePolicy,
    ExperimentStatus,
    ImprovementExperiment,
    normalize_repo_relative_path,
)

# ── 1. Path normalization and scope ──────────────────────────────────────────


def test_normalize_repo_relative_path_rejects_empty_and_traversal() -> None:
    assert normalize_repo_relative_path(r"src\agentgolem\runtime\loop.py") == (
        "src/agentgolem/runtime/loop.py"
    )

    with pytest.raises(ValueError):
        normalize_repo_relative_path("")

    with pytest.raises(ValueError):
        normalize_repo_relative_path("../secrets.txt")


def test_experiment_scope_policy_allows_exact_paths_and_prefixes() -> None:
    scope = ExperimentScopePolicy(
        allowed_paths=[r"config\skills\alignment_research.yaml"],
        allowed_prefixes=[r"src\agentgolem\runtime"],
    )

    assert scope.allowed_paths == ["config/skills/alignment_research.yaml"]
    assert scope.allowed_prefixes == ["src/agentgolem/runtime"]
    assert scope.allows(r"config\skills\alignment_research.yaml")
    assert scope.allows(r"src\agentgolem\runtime\loop.py")
    assert not scope.allows("src/agentgolem/dashboard/app.py")
    assert not scope.allows(
        r"src\agentgolem\runtime\private\secret.py",
        protected_paths=["src/agentgolem/runtime/private"],
    )


# ── 2. Validation and serialization ──────────────────────────────────────────


def test_improvement_experiment_round_trips_nested_models() -> None:
    experiment = ImprovementExperiment(
        title="Tune runtime browse scoring",
        description="Compare one scoring tweak against the current baseline.",
        proposed_by="Council-1",
        baseline_ref="HEAD",
        scope=ExperimentScopePolicy(allowed_prefixes=["src/agentgolem/runtime"]),
        candidate_changes=[
            ExperimentChange(
                file_path="src/agentgolem/runtime/loop.py",
                old_content="old snippet",
                new_content="new snippet",
                description="Tune one runtime branch.",
            )
        ],
        metrics=[
            ExperimentMetric(
                name="health_score_delta",
                goal=ExperimentMetricGoal.MAXIMIZE,
                primary=True,
                unit="score",
            ),
            ExperimentMetric(
                name="pytest_pass_rate",
                goal=ExperimentMetricGoal.MAXIMIZE,
                unit="ratio",
            ),
        ],
        evaluation_commands=[
            ExperimentCommand(
                name="focused-runtime-tests",
                command=".venv\\Scripts\\python.exe -m pytest tests\\test_runtime_loop.py -q",
                timeout_seconds=180.0,
            )
        ],
        budget=ExperimentBudget(
            time_budget_seconds=300.0,
            command_timeout_seconds=600.0,
            exclusive_resources=["repo-write-lock"],
        ),
    )

    payload = experiment.model_dump(mode="json")
    restored = ImprovementExperiment.model_validate(payload)

    assert restored.id.startswith("exp_")
    assert restored.primary_metric.name == "health_score_delta"
    assert restored.status == ExperimentStatus.PROPOSED
    assert restored.approval_status == ExperimentApprovalStatus.NOT_REQUIRED
    assert restored.is_terminal is False
    assert restored.scope.allowed_prefixes == ["src/agentgolem/runtime"]
    assert restored.budget.exclusive_resources == ["repo-write-lock"]
    assert restored.candidate_changes[0].file_path == "src/agentgolem/runtime/loop.py"


def test_improvement_experiment_rejects_multiple_primary_metrics() -> None:
    with pytest.raises(ValidationError):
        ImprovementExperiment(
            title="Invalid experiment",
            proposed_by="Council-2",
            baseline_ref="HEAD",
            scope=ExperimentScopePolicy(allowed_paths=["config/settings.yaml"]),
            metrics=[
                ExperimentMetric(name="a", primary=True),
                ExperimentMetric(name="b", primary=True),
            ],
            evaluation_commands=[
                ExperimentCommand(
                    name="tests",
                    command=".venv\\Scripts\\python.exe -m pytest tests -q",
                )
            ],
        )


def test_experiment_budget_requires_positive_limits() -> None:
    with pytest.raises(ValidationError):
        ExperimentBudget(time_budget_seconds=0.0)

    with pytest.raises(ValidationError):
        ExperimentCommand(name="tests", command="pytest", timeout_seconds=-1.0)


def test_candidate_changes_must_stay_within_scope() -> None:
    with pytest.raises(ValidationError):
        ImprovementExperiment(
            title="Scope violation",
            proposed_by="Council-4",
            baseline_ref="HEAD",
            scope=ExperimentScopePolicy(allowed_prefixes=["src/agentgolem/runtime"]),
            candidate_changes=[
                ExperimentChange(
                    file_path="src/agentgolem/dashboard/app.py",
                    old_content="old",
                    new_content="new",
                )
            ],
            metrics=[ExperimentMetric(name="health_score_delta")],
            evaluation_commands=[ExperimentCommand(name="tests", command="pytest -q")],
        )


def test_terminal_status_property_covers_keep_discard_crash_states() -> None:
    base = dict(
        title="Status check",
        proposed_by="Council-3",
        baseline_ref="HEAD",
        scope=ExperimentScopePolicy(allowed_paths=["config/settings.yaml"]),
        metrics=[ExperimentMetric(name="health_score_delta")],
        evaluation_commands=[ExperimentCommand(name="tests", command="pytest -q")],
    )

    assert ImprovementExperiment(status=ExperimentStatus.KEPT, **base).is_terminal is True
    assert ImprovementExperiment(status=ExperimentStatus.DISCARDED, **base).is_terminal is True
    assert ImprovementExperiment(status=ExperimentStatus.CRASHED, **base).is_terminal is True
    assert ImprovementExperiment(status=ExperimentStatus.RUNNING, **base).is_terminal is False
