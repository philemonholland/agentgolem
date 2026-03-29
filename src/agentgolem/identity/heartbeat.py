"""Heartbeat manager — periodic self-summary and maintenance trigger."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class HeartbeatSummary(BaseModel):
    """Data for a heartbeat update."""

    recent_actions: list[str] = []
    changing_priorities: list[str] = []
    unresolved_questions: list[str] = []
    memory_mutations: list[str] = []
    contradictions_and_supersessions: list[str] = []


class HeartbeatEntry(BaseModel):
    """A historical heartbeat record."""

    timestamp: str
    path: Path


class HeartbeatManager:
    def __init__(
        self,
        heartbeat_path: Path,
        data_dir: Path,
        interval_minutes: float = 15.0,
        audit_logger: Any | None = None,
    ) -> None:
        self._heartbeat_path = heartbeat_path
        self._history_dir = data_dir / "heartbeat_history"
        self._history_dir.mkdir(parents=True, exist_ok=True)
        self._interval = timedelta(minutes=interval_minutes)
        self._last_update: datetime | None = None
        self._audit = audit_logger

    async def read(self) -> str:
        """Read current heartbeat.md."""
        if self._heartbeat_path.exists():
            return self._heartbeat_path.read_text(encoding="utf-8")
        return ""

    async def update(self, summary: HeartbeatSummary) -> None:
        """Write new heartbeat, archive previous."""
        # Archive current
        old_content = await self.read()
        if old_content:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            archive_path = self._history_dir / f"{timestamp}.md"
            archive_path.write_text(old_content, encoding="utf-8")

        # Build new heartbeat content
        now = datetime.now(timezone.utc)
        content = self._render(summary, now)
        self._heartbeat_path.write_text(content, encoding="utf-8")
        self._last_update = now

        # Audit
        if self._audit:
            self._audit.log(
                mutation_type="heartbeat_update",
                target_id="heartbeat.md",
                evidence=summary.model_dump(),
            )

    def _render(self, summary: HeartbeatSummary, timestamp: datetime) -> str:
        """Render heartbeat markdown."""
        lines = [
            "# Heartbeat",
            "",
            "## Latest Update",
            "",
            f"**Timestamp**: {timestamp.isoformat()}",
            "",
        ]

        sections = [
            ("Recent Actions", summary.recent_actions),
            ("Changing Priorities", summary.changing_priorities),
            ("Unresolved Questions", summary.unresolved_questions),
            ("Memory Mutations", summary.memory_mutations),
            ("Contradictions & Supersessions", summary.contradictions_and_supersessions),
        ]

        for title, items in sections:
            lines.append(f"## {title}")
            lines.append("")
            if items:
                for item in items:
                    lines.append(f"- {item}")
            else:
                lines.append("None.")
            lines.append("")

        return "\n".join(lines)

    def get_next_heartbeat_time(self) -> datetime:
        """When the next heartbeat should fire."""
        if self._last_update is None:
            return datetime.now(timezone.utc)  # overdue
        return self._last_update + self._interval

    def is_due(self) -> bool:
        """Whether a heartbeat is due now."""
        return datetime.now(timezone.utc) >= self.get_next_heartbeat_time()

    async def get_history(self, limit: int = 20) -> list[HeartbeatEntry]:
        """List historical heartbeats, most recent first."""
        entries = []
        for path in sorted(self._history_dir.glob("*.md"), reverse=True):
            entries.append(HeartbeatEntry(timestamp=path.stem, path=path))
            if len(entries) >= limit:
                break
        return entries
