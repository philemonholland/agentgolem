"""Tests for trace-based benchmarks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.benchmarks.models import BenchmarkStatus
from agentgolem.benchmarks.trace_benchmarks import (
    _compute_autonomy,
    _compute_cost_latency,
    _compute_multi_agent,
    _compute_vow_adherence,
    _gini,
    _normalized_entropy,
    interpret_trace_report,
    run_trace_benchmarks,
)
from agentgolem.harness.trace import ExecutionTrace


# ── 1. Helpers ────────────────────────────────────────────────────────


def _make_trace(**overrides) -> ExecutionTrace:
    defaults = dict(
        call_site="unknown",
        purpose="peer_response",
        agent_name="test",
        prompt_summary="hello",
        context_tokens=100,
        completion_tokens=50,
        response_length=200,
        action_taken="share",
        outcome_type="",
        outcome_value="",
        goal_id="",
        peer_engagement_signal=None,
        timestamp="2026-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    return ExecutionTrace(**defaults)


# ── 2. Autonomy metrics ──────────────────────────────────────────────


def test_autonomy_productive_rate():
    traces = [
        _make_trace(purpose="peer_response"),
        _make_trace(purpose="browse_reflection"),
        _make_trace(purpose="search"),
        _make_trace(purpose="unknown_junk", action_taken=""),
    ]
    m = _compute_autonomy(traces)
    assert m.productive_rate.value == pytest.approx(0.75)


def test_autonomy_tool_failure_rate():
    traces = [
        _make_trace(outcome_type="tool_failure"),
        _make_trace(),
        _make_trace(),
        _make_trace(),
    ]
    m = _compute_autonomy(traces)
    assert m.tool_failure_rate.value == pytest.approx(0.25)


def test_autonomy_goal_directed():
    traces = [
        _make_trace(goal_id="g1"),
        _make_trace(goal_id="g2"),
        _make_trace(goal_id=""),
    ]
    m = _compute_autonomy(traces)
    assert m.goal_directed_rate.value == pytest.approx(2 / 3, abs=0.01)


def test_autonomy_empty():
    m = _compute_autonomy([])
    assert m.total_actions.value == 0.0


# ── 3. Cost/latency metrics ──────────────────────────────────────────


def test_cost_total_tokens():
    traces = [
        _make_trace(context_tokens=100, completion_tokens=50),
        _make_trace(context_tokens=200, completion_tokens=100),
    ]
    m = _compute_cost_latency(traces)
    assert m.total_context_tokens.value == 300.0
    assert m.total_completion_tokens.value == 150.0


def test_cost_retrieval_hit_rate():
    traces = [
        _make_trace(
            memory_node_ids_retrieved=["a", "b", "c"],
            memory_node_ids_referenced=["a"],
        ),
        _make_trace(
            memory_node_ids_retrieved=["x", "y"],
            memory_node_ids_referenced=["x", "y"],
        ),
    ]
    m = _compute_cost_latency(traces)
    # (1/3 + 2/2) / 2 = (0.333 + 1.0) / 2 = 0.667
    assert m.retrieval_hit_rate.value == pytest.approx(0.667, abs=0.01)


# ── 4. Vow adherence metrics ─────────────────────────────────────────


def test_vow_calibration_frequency():
    traces = [
        _make_trace(call_site="_run_calibration_protocol"),
        _make_trace(call_site="_autonomous_browse"),
        _make_trace(call_site="_autonomous_browse"),
        _make_trace(call_site="_run_calibration_protocol"),
        _make_trace(call_site="_autonomous_think"),
    ]
    m = _compute_vow_adherence(traces)
    assert m.calibration_frequency.value == pytest.approx(0.4)


def test_vow_foundation_fraction():
    traces = [
        _make_trace(call_site="_absorb_common_foundation"),
        _make_trace(call_site="_background_vow_review"),
        _make_trace(call_site="other"),
        _make_trace(call_site="other"),
    ]
    m = _compute_vow_adherence(traces)
    assert m.foundation_trace_fraction.value == pytest.approx(0.5)


# ── 5. Multi-agent metrics ───────────────────────────────────────────


def test_multi_agent_speaker_fairness_equal():
    agent_traces = {
        "a": [_make_trace()] * 10,
        "b": [_make_trace()] * 10,
    }
    m = _compute_multi_agent(agent_traces)
    assert m.speaker_fairness.value == pytest.approx(1.0, abs=0.01)


def test_multi_agent_speaker_fairness_unequal():
    agent_traces = {
        "a": [_make_trace()] * 100,
        "b": [_make_trace()] * 1,
    }
    m = _compute_multi_agent(agent_traces)
    assert m.speaker_fairness.value < 0.8


def test_multi_agent_peer_engagement():
    agent_traces = {
        "a": [
            _make_trace(peer_engagement_signal=True),
            _make_trace(peer_engagement_signal=True),
            _make_trace(peer_engagement_signal=False),
        ],
    }
    m = _compute_multi_agent(agent_traces)
    assert m.peer_engagement_rate.value == pytest.approx(2 / 3, abs=0.05)


def test_multi_agent_empty():
    m = _compute_multi_agent({})
    assert m.agent_count == 0


# ── 6. Utility functions ─────────────────────────────────────────────


def test_gini_equal():
    assert _gini([10.0, 10.0, 10.0]) == pytest.approx(0.0, abs=0.01)


def test_gini_unequal():
    assert _gini([0.0, 0.0, 100.0]) > 0.5


def test_normalized_entropy_uniform():
    items = ["a", "b", "c", "d"] * 10
    assert _normalized_entropy(items) == pytest.approx(1.0, abs=0.01)


def test_normalized_entropy_single():
    assert _normalized_entropy(["a"] * 10) == 0.0


# ── 7. Integration: run_trace_benchmarks with temp data ──────────────


@pytest.mark.asyncio
async def test_run_trace_benchmarks_with_data(tmp_path: Path):
    agent_dir = tmp_path / "council_1"
    traces_dir = agent_dir / "traces"
    traces_dir.mkdir(parents=True)

    traces = [
        _make_trace(
            agent_name="council_1",
            call_site="_autonomous_browse",
            purpose="browse_reflection",
        ),
        _make_trace(
            agent_name="council_1",
            call_site="_run_calibration_protocol",
            purpose="calibration",
        ),
        _make_trace(
            agent_name="council_1",
            purpose="peer_response",
        ),
    ]
    jsonl = "\n".join(json.dumps(t.to_dict()) for t in traces) + "\n"
    (traces_dir / "execution.jsonl").write_text(jsonl, encoding="utf-8")

    report = await run_trace_benchmarks(tmp_path, run_label="test")

    assert report.agent_count == 1
    assert len(report.agent_reports) == 1
    assert report.agent_reports[0].trace_count == 3
    assert report.autonomy.productive_rate.value > 0


@pytest.mark.asyncio
async def test_run_trace_benchmarks_no_data(tmp_path: Path):
    report = await run_trace_benchmarks(tmp_path, run_label="empty")
    assert report.overall_status == BenchmarkStatus.NOT_APPLICABLE
    assert report.agent_count == 0


def test_interpret_trace_report():
    from agentgolem.benchmarks.models import TraceBenchmarkRunReport

    report = TraceBenchmarkRunReport(
        run_label="test",
        target="data",
        agent_count=2,
        overall_status=BenchmarkStatus.PASS,
    )
    text = interpret_trace_report(report)
    assert "TRACE-BASED BENCHMARK REPORT" in text
    assert "AUTONOMY" in text
    assert "COST" in text
