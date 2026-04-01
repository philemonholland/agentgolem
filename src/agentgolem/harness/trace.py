"""Execution trace data model and JSONL persistence helpers.

Every LLM call emits a structured ``ExecutionTrace`` so calibration can
diagnose *what actually happened* rather than relying on lossy summaries.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ExecutionTrace:
    """One structured record per LLM call."""

    # Which method originated the call and why
    call_site: str
    purpose: str
    agent_name: str

    # Prompt / response metrics
    prompt_summary: str = ""
    context_tokens: int = 0
    completion_tokens: int = 0
    response_length: int = 0

    # Memory retrieval tracking
    memory_node_ids_retrieved: list[str] = field(default_factory=list)
    memory_node_ids_referenced: list[str] = field(default_factory=list)

    # Action taken (THINK / SHARE / SEARCH / BROWSE / etc.)
    action_taken: str = ""

    # Peer engagement: was our prior output echoed in an incoming message?
    peer_engagement_signal: bool | None = None

    # Outcome tracking (Phase C)
    outcome_type: str = ""   # search_results, browse_insight, goal_progress, etc.
    outcome_value: str = ""  # compact description of what happened
    goal_id: str = ""        # which goal this action served (if any)

    # Template versioning (Meta-Harness Phase 2)
    template_version: str = ""  # "name:version" of prompt template used

    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    # ── Derived helpers ───────────────────────────────────────────────

    @property
    def retrieval_hit_rate(self) -> float | None:
        """Fraction of retrieved nodes that appeared in the response."""
        if not self.memory_node_ids_retrieved:
            return None
        hits = len(self.memory_node_ids_referenced)
        return hits / len(self.memory_node_ids_retrieved)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionTrace:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── JSONL persistence ─────────────────────────────────────────────────


def traces_path(agent_data_dir: Path) -> Path:
    """Return the canonical JSONL path for an agent's execution traces."""
    return agent_data_dir / "traces" / "execution.jsonl"


def append_trace(trace: ExecutionTrace, agent_data_dir: Path) -> None:
    """Append one trace record to the agent's JSONL file (thread-safe append)."""
    p = traces_path(agent_data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")


def load_traces(
    agent_data_dir: Path,
    limit: int = 50,
) -> list[ExecutionTrace]:
    """Load the most recent *limit* traces (newest last).

    Reads the full file tail-efficiently for moderate sizes.  For very large
    files a seek-based approach could be added later.
    """
    p = traces_path(agent_data_dir)
    if not p.exists():
        return []
    lines: list[str] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
    # Keep only the newest `limit` entries
    lines = lines[-limit:]
    traces: list[ExecutionTrace] = []
    for line in lines:
        try:
            traces.append(ExecutionTrace.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue  # skip malformed lines
    return traces
