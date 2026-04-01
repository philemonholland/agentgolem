"""High-level orchestration for guarded self-improvement experiments."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentgolem.experiments.guardrails import (
    ExperimentPolicy,
    ExperimentResourceManager,
)
from agentgolem.experiments.ledger import ExperimentLedger, ExperimentRunRecord
from agentgolem.experiments.models import (
    ExperimentApprovalStatus,
    ExperimentStatus,
    ImprovementExperiment,
)
from agentgolem.experiments.review import submit_experiment_for_council_review
from agentgolem.experiments.runner import ExperimentRunner, ExperimentRunResult

if TYPE_CHECKING:
    from pathlib import Path

    from agentgolem.experiments.runner import MetricCollector
    from agentgolem.tools.base import ApprovalGate


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ExperimentOrchestrator:
    """Guard, approve, run, and forward experiments for council review."""

    def __init__(
        self,
        repo_root: Path,
        shared_data_dir: Path,
        *,
        ledger: ExperimentLedger | None = None,
        approval_gate: ApprovalGate | None = None,
        policy: ExperimentPolicy | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._shared_data_dir = shared_data_dir
        self._ledger = ledger or ExperimentLedger(shared_data_dir)
        self._approval_gate = approval_gate
        self._policy = policy or ExperimentPolicy()
        self._resources = ExperimentResourceManager(shared_data_dir)
        self._runner = ExperimentRunner(repo_root, ledger=self._ledger)
        self._proposals_dir = shared_data_dir / "evolution_proposals"

    def prepare(self, experiment: ImprovementExperiment) -> ImprovementExperiment:
        """Apply defaults, enforce guardrails, and synchronise approval state."""
        prepared = self._policy.apply_defaults(experiment)
        violations = self._policy.validate(prepared)
        if violations:
            blocked = prepared.model_copy(
                update={
                    "status": ExperimentStatus.BLOCKED,
                    "decision_reason": "\n".join(item.message for item in violations),
                    "completed_at": _now_iso(),
                }
            )
            self._ledger.save_experiment(blocked)
            return blocked

        if prepared.budget.requires_operator_approval:
            approved = self._sync_operator_approval(prepared)
            self._ledger.save_experiment(approved)
            return approved

        approved = prepared.model_copy(
            update={
                "status": ExperimentStatus.APPROVED,
                "approval_status": ExperimentApprovalStatus.NOT_REQUIRED,
            }
        )
        self._ledger.save_experiment(approved)
        return approved

    async def run(
        self,
        experiment: ImprovementExperiment,
        *,
        metric_collector: MetricCollector | None = None,
    ) -> ExperimentRunResult:
        """Prepare and, when ready, execute an experiment under exclusive locks."""
        prepared = self.prepare(experiment)
        if prepared.status != ExperimentStatus.APPROVED:
            return ExperimentRunResult(experiment=prepared, record=None)

        lease = self._resources.acquire(prepared.id, prepared.budget.exclusive_resources)
        if lease is None:
            blocked = prepared.model_copy(
                update={
                    "status": ExperimentStatus.BLOCKED,
                    "decision_reason": (
                        "Experiment resources are currently busy: "
                        + ", ".join(prepared.budget.exclusive_resources)
                    ),
                    "completed_at": _now_iso(),
                }
            )
            record = ExperimentRunRecord(
                experiment_id=blocked.id,
                recorded_by=blocked.proposed_by,
                status=blocked.status,
                baseline_ref=blocked.baseline_ref,
                candidate_ref=blocked.candidate_ref,
                notes=blocked.decision_reason,
            )
            self._ledger.save_experiment(blocked)
            self._ledger.append_record(record)
            return ExperimentRunResult(experiment=blocked, record=record)

        try:
            return await self._runner.run(prepared, metric_collector=metric_collector)
        finally:
            self._resources.release(lease)

    def forward_to_council_review(self, experiment: ImprovementExperiment) -> ImprovementExperiment:
        """Translate an evaluated experiment into the existing evolution proposal queue."""
        proposal_ids = submit_experiment_for_council_review(
            experiment,
            repo_root=self._repo_root,
            proposals_dir=self._proposals_dir,
        )
        kept = experiment.model_copy(
            update={
                "status": ExperimentStatus.KEPT,
                "review_proposal_ids": proposal_ids,
                "decision_reason": (
                    "Forwarded to council review as " + ", ".join(proposal_ids) + "."
                ),
            }
        )
        self._ledger.save_experiment(kept)
        return kept

    def _sync_operator_approval(self, experiment: ImprovementExperiment) -> ImprovementExperiment:
        if self._approval_gate is None:
            return experiment.model_copy(
                update={
                    "status": ExperimentStatus.BLOCKED,
                    "decision_reason": (
                        "Experiment requires operator approval, but no approval gate is available."
                    ),
                    "completed_at": _now_iso(),
                }
            )

        if not experiment.approval_request_id:
            request_id = self._approval_gate.request_approval(
                "experiment_run",
                {
                    "experiment_id": experiment.id,
                    "title": experiment.title,
                    "proposed_by": experiment.proposed_by,
                    "metrics": [metric.name for metric in experiment.metrics],
                    "commands": [command.command for command in experiment.evaluation_commands],
                    "candidate_change_paths": [
                        change.file_path for change in experiment.candidate_changes
                    ],
                    "resources": experiment.budget.exclusive_resources,
                },
            )
            return experiment.model_copy(
                update={
                    "approval_request_id": request_id,
                    "approval_status": ExperimentApprovalStatus.PENDING,
                    "decision_reason": "Awaiting operator approval for experiment run.",
                }
            )

        gate_status = self._approval_gate.check_approval(experiment.approval_request_id)
        if gate_status == "approved":
            return experiment.model_copy(
                update={
                    "status": ExperimentStatus.APPROVED,
                    "approval_status": ExperimentApprovalStatus.APPROVED,
                    "decision_reason": "",
                }
            )
        if gate_status == "denied":
            return experiment.model_copy(
                update={
                    "status": ExperimentStatus.CANCELLED,
                    "approval_status": ExperimentApprovalStatus.DENIED,
                    "decision_reason": "Operator denied the experiment run.",
                    "completed_at": _now_iso(),
                }
            )
        return experiment.model_copy(
            update={
                "approval_status": ExperimentApprovalStatus.PENDING,
                "decision_reason": "Awaiting operator approval for experiment run.",
            }
        )
