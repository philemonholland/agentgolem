"""Audit replay: read, filter, and trace causal chains across activity and audit logs."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class AuditReplay:
    """Read, filter, and trace causal chains across activity and audit logs."""

    def __init__(self, data_dir: Path) -> None:
        self._activity_path = data_dir / "logs" / "activity.jsonl"
        self._audit_path = data_dir / "logs" / "audit.jsonl"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        """Read a JSONL file and return a list of parsed dicts."""
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        return [json.loads(line) for line in lines if line.strip()]

    @staticmethod
    def _parse_ts(value: str) -> datetime:
        """Parse an ISO-8601 timestamp string to a datetime."""
        return datetime.fromisoformat(value)

    @staticmethod
    def _in_time_range(
        ts_str: str,
        from_time: datetime | None,
        to_time: datetime | None,
    ) -> bool:
        ts = AuditReplay._parse_ts(ts_str)
        if from_time is not None and ts < from_time:
            return False
        if to_time is not None and ts > to_time:
            return False
        return True

    @staticmethod
    def _paginate(
        entries: list[dict[str, Any]],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        return entries[offset : offset + limit]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_activity(
        self,
        limit: int = 100,
        offset: int = 0,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        event_type: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read activity log entries with filters."""
        entries = self._read_jsonl(self._activity_path)

        filtered: list[dict[str, Any]] = []
        for entry in entries:
            ts = entry.get("timestamp", "")
            if not self._in_time_range(ts, from_time, to_time):
                continue
            if event_type is not None:
                entry_event = entry.get("event") or entry.get("log_level") or ""
                if entry_event != event_type:
                    continue
            if search is not None:
                haystack = " ".join(
                    str(v) for v in entry.values() if isinstance(v, str)
                )
                if search.lower() not in haystack.lower():
                    continue
            filtered.append(entry)

        # Most recent first
        filtered.reverse()
        return self._paginate(filtered, limit, offset)

    def read_audit(
        self,
        limit: int = 100,
        offset: int = 0,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        mutation_type: str | None = None,
        target_id: str | None = None,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read audit log entries with filters."""
        entries = self._read_jsonl(self._audit_path)

        filtered: list[dict[str, Any]] = []
        for entry in entries:
            ts = entry.get("timestamp", "")
            if not self._in_time_range(ts, from_time, to_time):
                continue
            if mutation_type is not None and entry.get("mutation_type") != mutation_type:
                continue
            if target_id is not None and entry.get("target_id") != target_id:
                continue
            if actor is not None and entry.get("actor") != actor:
                continue
            filtered.append(entry)

        # Most recent first
        filtered.reverse()
        return self._paginate(filtered, limit, offset)

    def get_timeline(
        self,
        limit: int = 100,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Merge activity + audit into unified chronological timeline."""
        activity = self._read_jsonl(self._activity_path)
        audit = self._read_jsonl(self._audit_path)

        merged: list[dict[str, Any]] = []
        for entry in activity:
            tagged = {**entry, "log_source": "activity"}
            ts = tagged.get("timestamp", "")
            if self._in_time_range(ts, from_time, to_time):
                merged.append(tagged)
        for entry in audit:
            tagged = {**entry, "log_source": "audit"}
            ts = tagged.get("timestamp", "")
            if self._in_time_range(ts, from_time, to_time):
                merged.append(tagged)

        # Most recent first
        merged.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return merged[:limit]

    def trace_causal_chain(self, target_id: str) -> list[dict[str, Any]]:
        """Trace all events related to a given target_id across both logs."""
        chain: list[dict[str, Any]] = []

        # Audit entries where target_id matches directly
        for entry in self._read_jsonl(self._audit_path):
            if entry.get("target_id") == target_id:
                chain.append({**entry, "log_source": "audit"})

        # Activity entries that mention the target_id in any string field
        for entry in self._read_jsonl(self._activity_path):
            if any(
                isinstance(v, str) and target_id in v
                for v in entry.values()
            ):
                chain.append({**entry, "log_source": "activity"})

        # Oldest first (cause → effect)
        chain.sort(key=lambda e: e.get("timestamp", ""))
        return chain
