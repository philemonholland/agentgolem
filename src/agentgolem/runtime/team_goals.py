"""Team goal system: shared goals with per-agent subtasks."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class TeamGoal:
    """A shared goal the entire council works on together."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    description: str = ""
    why_it_matters: str = ""
    success_criteria: str = ""
    proposed_by: str = ""
    status: str = "proposed"  # proposed | voting | active | completed | abandoned
    votes: dict[str, str] = field(default_factory=dict)  # agent → accept|reject
    subtasks: dict[str, dict] = field(default_factory=dict)  # agent → subtask dict
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    completed_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TeamGoal:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _goals_dir(data_dir: Path) -> Path:
    d = data_dir / "team_goals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_team_goal(goal: TeamGoal, data_dir: Path) -> Path:
    """Persist the team goal to disk."""
    path = _goals_dir(data_dir) / "active.json"
    path.write_text(json.dumps(goal.to_dict(), indent=2), encoding="utf-8")
    return path


def load_active_team_goal(data_dir: Path) -> TeamGoal | None:
    """Load the active team goal, if any."""
    path = _goals_dir(data_dir) / "active.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return TeamGoal.from_dict(data)
    except Exception:
        return None


def archive_team_goal(data_dir: Path) -> None:
    """Move the active goal to the archive."""
    path = _goals_dir(data_dir) / "active.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        goal_id = data.get("id", "unknown")
    except Exception:
        goal_id = "unknown"
    archive_path = _goals_dir(data_dir) / f"completed_{goal_id}.json"
    path.rename(archive_path)
