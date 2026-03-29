"""Soul identity manager — constrained evolution of agent identity."""
from __future__ import annotations

import difflib
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from agentgolem.logging.audit import AuditLogger


class SoulUpdate(BaseModel):
    """A proposed change to soul.md."""

    reason: str
    source_evidence: list[str]
    diff: str = ""  # populated by propose_update
    confidence: float = Field(ge=0.0, le=1.0)
    change_type: Literal["additive", "revisive", "deprecating"]


class SoulVersion(BaseModel):
    """A historical version of soul.md."""

    timestamp: str
    path: Path


class SoulManager:
    """Manages the soul.md lifecycle: read, propose, apply, and version."""

    # Non-soul-worthy patterns (transient/unverified)
    _REJECT_PATTERNS = [
        "transient mood",
        "random web",
        "one-off disagreement",
        "unverified external claim",
        "temporary feeling",
    ]

    def __init__(
        self,
        soul_path: Path,
        data_dir: Path,
        min_confidence: float = 0.7,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._soul_path = soul_path
        self._versions_dir = data_dir / "soul_versions"
        self._versions_dir.mkdir(parents=True, exist_ok=True)
        self._min_confidence = min_confidence
        self._audit = audit_logger

    async def read(self) -> str:
        """Read current soul.md content."""
        if self._soul_path.exists():
            return self._soul_path.read_text(encoding="utf-8")
        return ""

    async def propose_update(self, update: SoulUpdate) -> str:
        """Validate and generate diff preview. Returns a summary. Raises ValueError if rejected."""
        if update.confidence < self._min_confidence:
            raise ValueError(
                f"Confidence {update.confidence} below threshold {self._min_confidence}"
            )

        reason_lower = update.reason.lower()
        for pattern in self._REJECT_PATTERNS:
            if pattern in reason_lower:
                raise ValueError(f"Non-soul-worthy update: matches '{pattern}'")

        if not update.source_evidence:
            raise ValueError("Soul update requires source evidence")

        return f"Proposed {update.change_type} update: {update.reason}"

    async def apply_update(self, update: SoulUpdate, new_content: str) -> None:
        """Apply a validated soul update. Archives current version first."""
        await self.propose_update(update)

        current_content = await self.read()
        if current_content:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            archive_path = self._versions_dir / f"{timestamp}.md"
            archive_path.write_text(current_content, encoding="utf-8")

        old_lines = current_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = "".join(
            difflib.unified_diff(old_lines, new_lines, fromfile="soul.md.old", tofile="soul.md")
        )

        self._soul_path.write_text(new_content, encoding="utf-8")

        if self._audit:
            self._audit.log(
                mutation_type=f"soul_{update.change_type}",
                target_id="soul.md",
                evidence={
                    "reason": update.reason,
                    "source_evidence": update.source_evidence,
                    "confidence": update.confidence,
                    "change_type": update.change_type,
                },
                diff=diff,
            )

    async def get_version_history(self) -> list[SoulVersion]:
        """List all archived soul versions, most recent first."""
        versions = []
        for path in sorted(self._versions_dir.glob("*.md"), reverse=True):
            versions.append(SoulVersion(timestamp=path.stem, path=path))
        return versions

    async def get_diff(self, version_path: Path) -> str:
        """Compute diff between a historical version and current soul."""
        if not version_path.exists():
            raise FileNotFoundError(f"Version not found: {version_path}")
        old_content = version_path.read_text(encoding="utf-8")
        current = await self.read()
        old_lines = old_content.splitlines(keepends=True)
        new_lines = current.splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(old_lines, new_lines, fromfile="old", tofile="current")
        )
