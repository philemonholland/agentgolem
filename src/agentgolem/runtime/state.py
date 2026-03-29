"""Agent runtime state machine."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class AgentMode(Enum):
    AWAKE = "awake"
    ASLEEP = "asleep"
    PAUSED = "paused"


# Legal transitions
_TRANSITIONS: dict[AgentMode, set[AgentMode]] = {
    AgentMode.AWAKE: {AgentMode.ASLEEP, AgentMode.PAUSED},
    AgentMode.ASLEEP: {AgentMode.AWAKE, AgentMode.PAUSED},
    AgentMode.PAUSED: {AgentMode.AWAKE, AgentMode.ASLEEP},
}


class RuntimeState:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._state_file = data_dir / "state" / "runtime_state.json"
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self.mode: AgentMode = AgentMode.PAUSED
        self.current_task: str | None = None
        self.pending_tasks: list[str] = []
        self.started_at: datetime = datetime.now(timezone.utc)
        self._load()

    def _load(self) -> None:
        """Load persisted state if available."""
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                self.mode = AgentMode(data.get("mode", "paused"))
                self.current_task = data.get("current_task")
                self.pending_tasks = data.get("pending_tasks", [])
            except (json.JSONDecodeError, ValueError):
                pass  # use defaults

    def _persist(self) -> None:
        """Save state to disk."""
        data = {
            "mode": self.mode.value,
            "current_task": self.current_task,
            "pending_tasks": self.pending_tasks,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._state_file.write_text(json.dumps(data, indent=2))

    async def transition(self, target: AgentMode) -> None:
        """Transition to a new mode. Raises ValueError for illegal transitions."""
        if target == self.mode:
            return
        if target not in _TRANSITIONS[self.mode]:
            raise ValueError(f"Cannot transition from {self.mode.value} to {target.value}")
        old = self.mode
        self.mode = target
        self._persist()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "current_task": self.current_task,
            "pending_tasks": self.pending_tasks,
            "started_at": self.started_at.isoformat(),
        }
