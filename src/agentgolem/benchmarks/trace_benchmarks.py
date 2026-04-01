"""Trace-based benchmarks — autonomy, cost/latency, multi-agent, vow adherence.

Reads real execution traces from agent data directories and computes
quality metrics across four dimensions.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import structlog

from agentgolem.benchmarks.metrics import bootstrap_mean
from agentgolem.benchmarks.models import (
    AutonomyMetrics,
    BenchmarkStatus,
    CostLatencyMetrics,
    MetricSummary,
    MultiAgentMetrics,
    TraceAgentReport,
    TraceBenchmarkRunReport,
    VowAdherenceMetrics,
)
from agentgolem.harness.trace import ExecutionTrace, load_traces

logger = structlog.get_logger(__name__)

_BOOTSTRAP_RESAMPLES = 300
_BOOTSTRAP_SEED = 42
_CONFIDENCE_LEVEL = 0.95

# Purposes that count as "productive" (not idle/unknown)
_PRODUCTIVE_PURPOSES = {
    "peer_response",
    "browse_reflection",
    "search",
    "autonomous_action",
    "autonomous_think",
    "calibration",
    "foundation_reflection",
    "vow_subconscious_refresh",
    "consciousness_tick",
    "metacognition",
    "heartbeat",
    "narrative_synthesis",
    "self_model_rebuild",
}

_IDLE_ACTIONS = {"idle", ""}

# Call sites related to ethical/vow work
_VOW_CALL_SITES = {
    "_absorb_common_foundation",
    "_discuss_niscalajyoti_chapter",
    "_discuss_council7_foundation_source",
    "_vow_ethics_discussion",
    "_run_calibration_protocol",
    "_background_vow_review",
}


def _metric(value: float) -> MetricSummary:
    return MetricSummary(value=value)


def _boot_metric(values: list[float], *, seed_offset: int = 0) -> MetricSummary:
    if not values:
        return MetricSummary(value=0.0)
    s = bootstrap_mean(
        values,
        resamples=_BOOTSTRAP_RESAMPLES,
        seed=_BOOTSTRAP_SEED + seed_offset,
        confidence_level=_CONFIDENCE_LEVEL,
    )
    return MetricSummary(
        value=s.value,
        ci_lower=s.ci_lower,
        ci_upper=s.ci_upper,
        confidence_level=s.confidence_level,
    )


# ── Per-agent metric computation ─────────────────────────────────────


def _compute_autonomy(traces: list[ExecutionTrace]) -> AutonomyMetrics:
    """Compute autonomy usefulness from traces."""
    if not traces:
        return AutonomyMetrics()

    total = len(traces)
    productive = sum(1 for t in traces if t.purpose in _PRODUCTIVE_PURPOSES)
    goal_directed = sum(1 for t in traces if t.goal_id)
    idle = sum(1 for t in traces if t.action_taken in _IDLE_ACTIONS and not t.purpose)
    tool_failures = sum(
        1 for t in traces if t.outcome_type == "tool_failure"
    )

    searches = [t for t in traces if t.call_site == "_autonomous_search"]
    browses = [t for t in traces if t.call_site == "_autonomous_browse"]
    shares = [t for t in traces if t.purpose == "peer_response"]

    s2b = len(browses) / max(len(searches), 1) if searches else 0.0
    b2s = len(shares) / max(len(browses), 1) if browses else 0.0

    return AutonomyMetrics(
        total_actions=_metric(float(total)),
        productive_rate=_metric(productive / total if total else 0.0),
        goal_directed_rate=_metric(goal_directed / total if total else 0.0),
        search_to_browse_rate=_metric(min(s2b, 1.0)),
        browse_to_share_rate=_metric(min(b2s, 1.0)),
        tool_failure_rate=_metric(tool_failures / total if total else 0.0),
        idle_rate=_metric(idle / total if total else 0.0),
    )


def _compute_cost_latency(traces: list[ExecutionTrace]) -> CostLatencyMetrics:
    """Compute token cost and efficiency metrics."""
    if not traces:
        return CostLatencyMetrics()

    ctx_tokens = [float(t.context_tokens) for t in traces if t.context_tokens > 0]
    comp_tokens = [float(t.completion_tokens) for t in traces if t.completion_tokens > 0]

    total_ctx = sum(ctx_tokens)
    total_comp = sum(comp_tokens)

    # Retrieval hit rate: fraction of retrieved nodes that were referenced
    hit_rates: list[float] = []
    for t in traces:
        retrieved = t.memory_node_ids_retrieved
        if retrieved:
            referenced = set(t.memory_node_ids_referenced)
            hit_rates.append(len(referenced & set(retrieved)) / len(retrieved))

    # Context efficiency: response_length / context_tokens (output per input)
    efficiencies: list[float] = []
    for t in traces:
        if t.context_tokens > 0 and t.response_length > 0:
            efficiencies.append(t.response_length / t.context_tokens)

    return CostLatencyMetrics(
        total_context_tokens=_metric(total_ctx),
        total_completion_tokens=_metric(total_comp),
        avg_context_tokens=_boot_metric(ctx_tokens, seed_offset=1),
        avg_completion_tokens=_boot_metric(comp_tokens, seed_offset=2),
        retrieval_hit_rate=_boot_metric(hit_rates, seed_offset=3),
        context_efficiency=_boot_metric(efficiencies, seed_offset=4),
    )


def _compute_vow_adherence(traces: list[ExecutionTrace]) -> VowAdherenceMetrics:
    """Compute vow/ethical adherence metrics."""
    if not traces:
        return VowAdherenceMetrics()

    total = len(traces)
    calibrations = sum(
        1 for t in traces if t.call_site == "_run_calibration_protocol"
    )
    vow_refreshes = sum(
        1 for t in traces if t.call_site == "_background_vow_review"
    )
    foundation = sum(
        1 for t in traces if t.call_site in _VOW_CALL_SITES
    )

    return VowAdherenceMetrics(
        calibration_frequency=_metric(calibrations / total if total else 0.0),
        vow_refresh_count=_metric(float(vow_refreshes)),
        foundation_trace_fraction=_metric(foundation / total if total else 0.0),
    )


# ── Multi-agent metrics ──────────────────────────────────────────────


def _compute_multi_agent(
    agent_traces: dict[str, list[ExecutionTrace]],
) -> MultiAgentMetrics:
    """Compute multi-agent quality metrics across all agents."""
    if not agent_traces:
        return MultiAgentMetrics()

    agent_count = len(agent_traces)

    # Peer engagement rate (per-agent, then bootstrapped)
    engagement_rates: list[float] = []
    for traces in agent_traces.values():
        engaged = sum(1 for t in traces if t.peer_engagement_signal is True)
        total_with_signal = sum(1 for t in traces if t.peer_engagement_signal is not None)
        if total_with_signal > 0:
            engagement_rates.append(engaged / total_with_signal)

    # Speaker fairness: how evenly distributed are trace counts?
    # Use 1 - Gini coefficient (1.0 = perfectly fair)
    counts = [float(len(ts)) for ts in agent_traces.values()]
    fairness = 1.0 - _gini(counts) if len(counts) > 1 else 1.0

    # Action diversity per agent (Shannon entropy of action_taken / max entropy)
    diversity_scores: list[float] = []
    for traces in agent_traces.values():
        actions = [t.action_taken or t.purpose for t in traces if t.action_taken or t.purpose]
        if actions:
            diversity_scores.append(_normalized_entropy(actions))

    # Purpose distribution variance: how different are agents from each other?
    purpose_vectors: list[dict[str, float]] = []
    all_purposes: set[str] = set()
    for traces in agent_traces.values():
        counter: Counter[str] = Counter()
        for t in traces:
            p = t.purpose or "unknown"
            counter[p] += 1
            all_purposes.add(p)
        total = sum(counter.values())
        vec = {p: counter.get(p, 0) / total for p in all_purposes} if total else {}
        purpose_vectors.append(vec)

    # Average pairwise cosine distance
    pdv = _avg_pairwise_distance(purpose_vectors, all_purposes) if len(purpose_vectors) > 1 else 0.0

    return MultiAgentMetrics(
        agent_count=agent_count,
        peer_engagement_rate=_boot_metric(engagement_rates, seed_offset=10),
        speaker_fairness=_metric(fairness),
        action_diversity=_boot_metric(diversity_scores, seed_offset=11),
        purpose_distribution_variance=_metric(pdv),
    )


def _gini(values: list[float]) -> float:
    """Compute the Gini coefficient for a list of non-negative values."""
    if not values or all(v == 0 for v in values):
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    total = sum(sorted_vals)
    cum = sum((i + 1) * v for i, v in enumerate(sorted_vals))
    return (2 * cum) / (n * total) - (n + 1) / n


def _normalized_entropy(items: list[str]) -> float:
    """Shannon entropy normalized to [0, 1]."""
    counter = Counter(items)
    total = len(items)
    n_classes = len(counter)
    if n_classes <= 1:
        return 0.0
    max_ent = math.log2(n_classes)
    if max_ent == 0:
        return 0.0
    ent = -sum((c / total) * math.log2(c / total) for c in counter.values())
    return ent / max_ent


def _avg_pairwise_distance(
    vectors: list[dict[str, float]], keys: set[str],
) -> float:
    """Average pairwise cosine distance between purpose distribution vectors."""
    if len(vectors) < 2:
        return 0.0
    keys_list = sorted(keys)
    n = len(vectors)
    distances: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            a = [vectors[i].get(k, 0.0) for k in keys_list]
            b = [vectors[j].get(k, 0.0) for k in keys_list]
            dot = sum(x * y for x, y in zip(a, b))
            mag_a = math.sqrt(sum(x * x for x in a))
            mag_b = math.sqrt(sum(x * x for x in b))
            if mag_a > 0 and mag_b > 0:
                cos_sim = dot / (mag_a * mag_b)
                distances.append(1.0 - cos_sim)
            else:
                distances.append(1.0)
    return sum(distances) / len(distances) if distances else 0.0


# ── Discovery and runner ─────────────────────────────────────────────


def _discover_trace_dirs(target: Path) -> list[tuple[str, Path]]:
    """Find agent data directories containing execution traces."""
    results: list[tuple[str, Path]] = []

    # If target itself has traces/
    if (target / "traces" / "execution.jsonl").exists():
        name = target.name or "agent"
        results.append((name, target))
        return results

    # Search for council_N or named agent directories
    for child in sorted(target.iterdir()):
        if child.is_dir() and (child / "traces" / "execution.jsonl").exists():
            results.append((child.name, child))

    return results


def _agent_status(report: TraceAgentReport) -> BenchmarkStatus:
    """Determine per-agent status from metrics."""
    a = report.autonomy
    if a.productive_rate.value >= 0.5 and a.tool_failure_rate.value < 0.15:
        return BenchmarkStatus.PASS
    if a.productive_rate.value < 0.2 or a.tool_failure_rate.value > 0.3:
        return BenchmarkStatus.FAIL
    return BenchmarkStatus.MIXED


async def run_trace_benchmarks(
    target: Path,
    *,
    run_label: str = "",
    trace_limit: int = 500,
) -> TraceBenchmarkRunReport:
    """Run all trace-based benchmarks against real agent data."""
    agent_dirs = _discover_trace_dirs(target)
    if not agent_dirs:
        logger.warning("trace_benchmarks_no_data", target=str(target))
        return TraceBenchmarkRunReport(
            run_label=run_label,
            target=str(target),
            overall_status=BenchmarkStatus.NOT_APPLICABLE,
        )

    all_agent_traces: dict[str, list[ExecutionTrace]] = {}
    agent_reports: list[TraceAgentReport] = []

    for name, data_dir in agent_dirs:
        traces = load_traces(data_dir, limit=trace_limit)
        if not traces:
            agent_reports.append(TraceAgentReport(
                agent_name=name,
                status=BenchmarkStatus.NOT_APPLICABLE,
            ))
            continue

        all_agent_traces[name] = traces

        report = TraceAgentReport(
            agent_name=name,
            trace_count=len(traces),
            autonomy=_compute_autonomy(traces),
            cost_latency=_compute_cost_latency(traces),
            vow_adherence=_compute_vow_adherence(traces),
        )
        report.status = _agent_status(report)
        agent_reports.append(report)

    # Aggregate across all agents
    all_traces = [t for ts in all_agent_traces.values() for t in ts]
    agg_autonomy = _compute_autonomy(all_traces)
    agg_cost = _compute_cost_latency(all_traces)
    agg_multi = _compute_multi_agent(all_agent_traces)
    agg_vow = _compute_vow_adherence(all_traces)

    statuses = [r.status for r in agent_reports if r.status != BenchmarkStatus.NOT_APPLICABLE]
    if not statuses:
        overall = BenchmarkStatus.NOT_APPLICABLE
    elif all(s == BenchmarkStatus.PASS for s in statuses):
        overall = BenchmarkStatus.PASS
    elif all(s == BenchmarkStatus.FAIL for s in statuses):
        overall = BenchmarkStatus.FAIL
    else:
        overall = BenchmarkStatus.MIXED

    return TraceBenchmarkRunReport(
        run_label=run_label,
        target=str(target),
        agent_count=len(agent_dirs),
        overall_status=overall,
        autonomy=agg_autonomy,
        cost_latency=agg_cost,
        multi_agent=agg_multi,
        vow_adherence=agg_vow,
        agent_reports=agent_reports,
    )


# ── Interpretation ───────────────────────────────────────────────────


def _fmt(m: MetricSummary, *, pct: bool = False) -> str:
    """Format a metric summary for human display."""
    if pct:
        val = f"{m.value:.0%}"
    else:
        val = f"{m.value:.2f}"
    if m.ci_lower is not None and m.ci_upper is not None:
        if pct:
            ci = f" [{m.ci_lower:.0%}, {m.ci_upper:.0%}]"
        else:
            ci = f" [{m.ci_lower:.2f}, {m.ci_upper:.2f}]"
        return val + ci
    return val


def interpret_trace_report(report: TraceBenchmarkRunReport) -> str:
    """Human-readable interpretation of trace benchmark results."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  TRACE-BASED BENCHMARK REPORT")
    if report.run_label:
        lines.append(f"  Label: {report.run_label}")
    lines.append(f"  Target: {report.target}")
    lines.append(f"  Agents: {report.agent_count}")
    lines.append(f"  Status: {report.overall_status.upper()}")
    lines.append("=" * 60)

    # Autonomy
    a = report.autonomy
    lines.append("\n📊 AUTONOMY USEFULNESS")
    lines.append(f"  Total actions:         {_fmt(a.total_actions)}")
    lines.append(f"  Productive rate:       {_fmt(a.productive_rate, pct=True)}")
    lines.append(f"  Goal-directed rate:    {_fmt(a.goal_directed_rate, pct=True)}")
    lines.append(f"  Search→Browse:         {_fmt(a.search_to_browse_rate, pct=True)}")
    lines.append(f"  Browse→Share:          {_fmt(a.browse_to_share_rate, pct=True)}")
    lines.append(f"  Tool failure rate:     {_fmt(a.tool_failure_rate, pct=True)}")
    lines.append(f"  Idle rate:             {_fmt(a.idle_rate, pct=True)}")

    # Cost / Latency
    c = report.cost_latency
    lines.append("\n💰 COST & LATENCY")
    lines.append(f"  Total context tokens:  {_fmt(c.total_context_tokens)}")
    lines.append(f"  Total completion tkns: {_fmt(c.total_completion_tokens)}")
    lines.append(f"  Avg context tokens:    {_fmt(c.avg_context_tokens)}")
    lines.append(f"  Avg completion tokens: {_fmt(c.avg_completion_tokens)}")
    lines.append(f"  Retrieval hit rate:    {_fmt(c.retrieval_hit_rate, pct=True)}")
    lines.append(f"  Context efficiency:    {_fmt(c.context_efficiency)}")

    # Multi-agent
    m = report.multi_agent
    lines.append("\n🤝 MULTI-AGENT QUALITY")
    lines.append(f"  Agent count:           {m.agent_count}")
    lines.append(f"  Peer engagement rate:  {_fmt(m.peer_engagement_rate, pct=True)}")
    lines.append(f"  Speaker fairness:      {_fmt(m.speaker_fairness, pct=True)}")
    lines.append(f"  Action diversity:      {_fmt(m.action_diversity, pct=True)}")
    lines.append(f"  Purpose variance:      {_fmt(m.purpose_distribution_variance)}")

    # Vow adherence
    v = report.vow_adherence
    lines.append("\n📜 VOW ADHERENCE")
    lines.append(f"  Calibration frequency: {_fmt(v.calibration_frequency, pct=True)}")
    lines.append(f"  Vow refresh count:     {_fmt(v.vow_refresh_count)}")
    lines.append(f"  Foundation fraction:   {_fmt(v.foundation_trace_fraction, pct=True)}")

    # Per-agent summary
    if report.agent_reports:
        lines.append("\n── Per-Agent Summary ──")
        for ar in report.agent_reports:
            status_icon = {"pass": "✅", "fail": "❌", "mixed": "⚠️"}.get(
                ar.status, "—"
            )
            lines.append(
                f"  {status_icon} {ar.agent_name:20s} "
                f"traces={ar.trace_count:4d}  "
                f"productive={ar.autonomy.productive_rate.value:.0%}  "
                f"failures={ar.autonomy.tool_failure_rate.value:.0%}"
            )

    lines.append("")
    return "\n".join(lines)
