"""Lightweight self-evaluation — an agent benchmarks its own memory graph.

Meta-Harness Phase 3: closed-loop self-benchmark that feeds back into
calibration.  Runs periodically (every Nth calibration) and measures:

1. **Retrieval precision** — can the agent find its own recent high-value memories?
2. **Trust calibration** — are confidence signals correlated with real trustworthiness?
3. **Context efficiency** — how many tokens spent vs information gain?
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SelfBenchmarkResult:
    """Result of one self-benchmark run."""

    agent_name: str
    timestamp: str = ""

    # Retrieval precision: fraction of recent high-value nodes re-found via query
    retrieval_precision: float = 0.0
    retrieval_queries_tested: int = 0
    retrieval_hits: int = 0
    retrieval_misses: int = 0

    # Trust calibration: correlation between trust score and source reliability
    trust_coherence: float = 0.0
    trust_nodes_sampled: int = 0

    # Context efficiency: from recent traces
    avg_context_tokens: float = 0.0
    avg_retrieval_hit_rate: float = 0.0
    productive_action_rate: float = 0.0

    # Overall health score 0-1
    health_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelfBenchmarkResult:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


def compute_health_score(result: SelfBenchmarkResult) -> float:
    """Weighted health score from sub-metrics."""
    weights = {
        "retrieval": 0.35,
        "trust": 0.25,
        "efficiency": 0.20,
        "productivity": 0.20,
    }
    # Normalize efficiency (lower tokens = better, capped at 8k)
    efficiency_score = max(0.0, 1.0 - result.avg_context_tokens / 8000.0)
    return (
        weights["retrieval"] * result.retrieval_precision
        + weights["trust"] * result.trust_coherence
        + weights["efficiency"] * efficiency_score
        + weights["productivity"] * result.productive_action_rate
    )


def append_self_benchmark(result: SelfBenchmarkResult, data_dir: Path) -> None:
    """Append a self-benchmark result to the agent's benchmark history."""
    path = data_dir / "self_benchmarks.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result.to_dict()) + "\n")


def load_self_benchmarks(
    data_dir: Path, limit: int = 20,
) -> list[SelfBenchmarkResult]:
    """Load recent self-benchmark results."""
    path = data_dir / "self_benchmarks.jsonl"
    if not path.exists():
        return []
    lines: list[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
    lines = lines[-limit:]
    results: list[SelfBenchmarkResult] = []
    for line in lines:
        try:
            results.append(SelfBenchmarkResult.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue
    return results


def format_self_benchmark_for_calibration(
    result: SelfBenchmarkResult,
    history: list[SelfBenchmarkResult] | None = None,
) -> str:
    """Format self-benchmark results for injection into calibration prompt."""
    lines = [
        "--- SELF-BENCHMARK RESULTS ---",
        f"Health score: {result.health_score:.2f}/1.00",
        f"Retrieval precision: {result.retrieval_precision:.0%}"
        f" ({result.retrieval_hits}/{result.retrieval_queries_tested} queries hit)",
        f"Trust coherence: {result.trust_coherence:.2f}",
        f"Avg context tokens: {result.avg_context_tokens:.0f}",
        f"Avg retrieval hit rate: {result.avg_retrieval_hit_rate:.0%}",
        f"Productive action rate: {result.productive_action_rate:.0%}",
    ]

    if history and len(history) >= 2:
        prev = history[-2]
        delta = result.health_score - prev.health_score
        direction = "📈" if delta > 0 else "📉" if delta < 0 else "➡️"
        lines.append(f"Trend: {direction} {delta:+.2f} since last benchmark")

    lines.append("---")
    return "\n".join(lines)
