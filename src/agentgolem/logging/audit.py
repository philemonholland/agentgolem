"""Append-only audit trail for all memory mutations and sensitive actions."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    def __init__(self, data_dir: Path) -> None:
        self._log_path = data_dir / "logs" / "audit.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        mutation_type: str,
        target_id: str,
        evidence: dict[str, Any],
        *,
        actor: str = "agent",
        diff: str | None = None,
    ) -> None:
        """Append an audit entry."""
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mutation_type": mutation_type,
            "target_id": target_id,
            "actor": actor,
            "evidence": evidence,
        }
        if diff is not None:
            entry["diff"] = diff
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def read(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Read audit entries (most recent first)."""
        if not self._log_path.exists():
            return []
        with open(self._log_path, encoding="utf-8") as f:
            lines = f.readlines()
        entries = [json.loads(line) for line in lines if line.strip()]
        entries.reverse()  # most recent first
        return entries[offset : offset + limit]
