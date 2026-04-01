"""Forward successful experiments into the existing council evolution queue."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from agentgolem.experiments.models import ExperimentStatus, ImprovementExperiment


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def submit_experiment_for_council_review(
    experiment: ImprovementExperiment,
    *,
    repo_root: Path,
    proposals_dir: Path,
) -> list[str]:
    """Create evolution proposal JSON files from an evaluated experiment."""
    if experiment.status != ExperimentStatus.EVALUATED:
        raise ValueError("Only evaluated experiments can be forwarded for council review.")
    if not experiment.candidate_changes:
        raise ValueError("Experiment has no candidate changes to review.")

    proposals_dir.mkdir(parents=True, exist_ok=True)
    proposal_ids: list[str] = []

    for change in experiment.candidate_changes:
        resolved = repo_root / Path(change.file_path)
        if not resolved.exists():
            raise FileNotFoundError(f"Candidate change file not found: {change.file_path}")

        current = resolved.read_text(encoding="utf-8")
        if change.old_content and change.old_content not in current:
            raise ValueError(
                f"Candidate change for '{change.file_path}' no longer matches the repository."
            )

        proposal_id = f"evo_{uuid.uuid4().hex[:8]}"
        summary = change.description or experiment.description or experiment.title
        proposal = {
            "id": proposal_id,
            "proposer": experiment.proposed_by,
            "timestamp": _now_iso(),
            "file_path": change.file_path,
            "description": f"[Experiment {experiment.id}] {summary}",
            "old_content": change.old_content,
            "new_content": change.new_content,
            "votes": {
                experiment.proposed_by: {
                    "approve": True,
                    "reason": (
                        f"Experiment {experiment.id} met its evaluation criteria and "
                        "was forwarded for council review."
                    ),
                }
            },
            "status": "pending",
            "experiment_id": experiment.id,
            "candidate_ref": experiment.candidate_ref,
        }
        path = proposals_dir / f"{proposal_id}.json"
        path.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
        proposal_ids.append(proposal_id)

    return proposal_ids
