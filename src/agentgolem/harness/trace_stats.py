"""Aggregate statistics computed over a window of execution traces.

``compute_trace_stats`` takes a list of :class:`ExecutionTrace` and returns a
:class:`TraceStatsSummary` with retrieval hit rate, context budget usage,
action distribution, peer engagement rate, and call breakdown by purpose.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agentgolem.harness.trace import ExecutionTrace


@dataclass(slots=True)
class TraceStatsSummary:
    """Aggregated diagnostics over a window of execution traces."""

    retrieval_hit_rate: float = 0.0
    avg_context_tokens: float = 0.0
    avg_completion_tokens: float = 0.0
    avg_response_length: float = 0.0
    action_distribution: dict[str, float] = field(default_factory=dict)
    peer_engagement_rate: float = 0.0
    total_calls: int = 0
    calls_by_purpose: dict[str, int] = field(default_factory=dict)

    # Raw counts for transparency
    total_nodes_retrieved: int = 0
    total_nodes_referenced: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "retrieval_hit_rate": round(self.retrieval_hit_rate, 3),
            "avg_context_tokens": round(self.avg_context_tokens, 1),
            "avg_completion_tokens": round(self.avg_completion_tokens, 1),
            "avg_response_length": round(self.avg_response_length, 1),
            "action_distribution": {
                k: round(v, 3) for k, v in self.action_distribution.items()
            },
            "peer_engagement_rate": round(self.peer_engagement_rate, 3),
            "total_calls": self.total_calls,
            "calls_by_purpose": dict(self.calls_by_purpose),
            "total_nodes_retrieved": self.total_nodes_retrieved,
            "total_nodes_referenced": self.total_nodes_referenced,
        }

    def format_diagnostic_block(self) -> str:
        """Render a human-readable diagnostic block for the calibration prompt."""
        lines = [
            f"--- OBJECTIVE DIAGNOSTICS (last {self.total_calls} LLM calls) ---",
        ]
        pct = f"{self.retrieval_hit_rate:.0%}"
        lines.append(
            f"Retrieval hit rate: {pct}"
            f" ({self.total_nodes_referenced}/{self.total_nodes_retrieved}"
            " retrieved memories were referenced in responses)"
        )
        lines.append(
            f"Average context tokens: {self.avg_context_tokens:,.0f}"
        )
        lines.append(
            f"Average response length: {self.avg_response_length:,.0f} chars"
        )
        if self.action_distribution:
            parts = ", ".join(
                f"{k} {v:.0%}"
                for k, v in sorted(
                    self.action_distribution.items(),
                    key=lambda x: -x[1],
                )
            )
            lines.append(f"Action distribution: {parts}")
        eng = f"{self.peer_engagement_rate:.0%}"
        lines.append(
            f"Peer engagement rate: {eng}"
            " (fraction of your messages echoed by peers)"
        )
        if self.calls_by_purpose:
            parts = ", ".join(
                f"{k}: {v}" for k, v in sorted(self.calls_by_purpose.items())
            )
            lines.append(f"Calls by purpose: {parts}")
        lines.append("---")
        return "\n".join(lines)


def compute_trace_stats(traces: list[ExecutionTrace]) -> TraceStatsSummary:
    """Compute aggregate statistics over a list of execution traces."""
    n = len(traces)
    if n == 0:
        return TraceStatsSummary()

    total_retrieved = sum(len(t.memory_node_ids_retrieved) for t in traces)
    total_referenced = sum(len(t.memory_node_ids_referenced) for t in traces)
    hit_rate = total_referenced / total_retrieved if total_retrieved > 0 else 0.0

    avg_ctx = sum(t.context_tokens for t in traces) / n
    avg_comp = sum(t.completion_tokens for t in traces) / n
    avg_resp = sum(t.response_length for t in traces) / n

    # Action distribution (only count traces that have an action)
    action_counts: Counter[str] = Counter()
    for t in traces:
        if t.action_taken:
            action_counts[t.action_taken] += 1
    action_total = sum(action_counts.values())
    action_dist = (
        {k: v / action_total for k, v in action_counts.items()}
        if action_total > 0
        else {}
    )

    # Peer engagement (only count traces where the signal was recorded)
    engaged_traces = [t for t in traces if t.peer_engagement_signal is not None]
    engagement_rate = (
        sum(1 for t in engaged_traces if t.peer_engagement_signal) / len(engaged_traces)
        if engaged_traces
        else 0.0
    )

    purpose_counts: dict[str, int] = dict(Counter(t.purpose for t in traces))

    return TraceStatsSummary(
        retrieval_hit_rate=hit_rate,
        avg_context_tokens=avg_ctx,
        avg_completion_tokens=avg_comp,
        avg_response_length=avg_resp,
        action_distribution=action_dist,
        peer_engagement_rate=engagement_rate,
        total_calls=n,
        calls_by_purpose=purpose_counts,
        total_nodes_retrieved=total_retrieved,
        total_nodes_referenced=total_referenced,
    )
