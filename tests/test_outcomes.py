"""Tests for the outcome tracking system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.harness.outcomes import OutcomeStats, compute_outcome_stats
from agentgolem.harness.trace import ExecutionTrace


# ── 1. OutcomeStats model ─────────────────────────────────────────────


class TestOutcomeStats:
    """OutcomeStats dataclass behavior."""

    def test_defaults(self):
        stats = OutcomeStats()
        assert stats.total_actions == 0
        assert stats.productive_rate == 0.0

    def test_to_dict_round_trip(self):
        stats = OutcomeStats(
            total_actions=50,
            actions_with_outcome=34,
            productive_rate=0.68,
            tool_failure_rate=0.06,
        )
        d = stats.to_dict()
        assert d["total_actions"] == 50
        assert d["productive_rate"] == 0.68
        assert d["tool_failure_rate"] == 0.06

    def test_format_diagnostic(self):
        stats = OutcomeStats(
            total_actions=50,
            actions_with_outcome=34,
            productive_rate=0.68,
            search_count=10,
            browse_count=8,
            search_to_browse_rate=0.8,
            goals_set=2,
            goals_progressed=3,
            goals_completed=1,
            tool_failures=3,
            tool_failure_rate=0.06,
            idle_count=2,
            idle_rate=0.04,
        )
        text = stats.format_diagnostic()
        assert "OUTCOME DIAGNOSTICS" in text
        assert "68%" in text
        assert "Tool failures: 3" in text
        assert "Idle: 2" in text
        assert "Goals:" in text

    def test_diagnostic_empty(self):
        stats = OutcomeStats()
        text = stats.format_diagnostic()
        assert "0 actions" in text


# ── 2. compute_outcome_stats ──────────────────────────────────────────


class TestComputeOutcomeStats:
    """Tests for the stats aggregator."""

    def test_empty_traces(self):
        stats = compute_outcome_stats([])
        assert stats.total_actions == 0

    def test_basic_counts(self):
        traces = [
            ExecutionTrace(
                call_site="test",
                purpose="test",
                agent_name="A",
                action_taken="THINK",
                outcome_type="reflection",
            ),
            ExecutionTrace(
                call_site="test",
                purpose="test",
                agent_name="A",
                action_taken="SHARE",
                outcome_type="peer_shared",
            ),
            ExecutionTrace(
                call_site="test",
                purpose="test",
                agent_name="A",
                action_taken="idle",
            ),
        ]
        stats = compute_outcome_stats(traces)
        assert stats.total_actions == 3
        assert stats.actions_with_outcome == 2
        assert stats.idle_count == 1
        assert stats.idle_rate == pytest.approx(1 / 3, abs=0.01)

    def test_goal_tracking(self):
        traces = [
            ExecutionTrace(
                call_site="t", purpose="t", agent_name="A",
                outcome_type="goal_set",
            ),
            ExecutionTrace(
                call_site="t", purpose="t", agent_name="A",
                outcome_type="goal_progress",
                goal_id="g1",
            ),
            ExecutionTrace(
                call_site="t", purpose="t", agent_name="A",
                outcome_type="goal_completed",
                goal_id="g1",
            ),
        ]
        stats = compute_outcome_stats(traces)
        assert stats.goals_set == 1
        assert stats.goals_progressed == 1
        assert stats.goals_completed == 1
        assert stats.actions_toward_goals == 2  # the ones with goal_id

    def test_tool_failures(self):
        traces = [
            ExecutionTrace(
                call_site="t", purpose="t", agent_name="A",
                outcome_type="tool_failure",
            ),
            ExecutionTrace(
                call_site="t", purpose="t", agent_name="A",
                outcome_type="search_results",
            ),
        ]
        stats = compute_outcome_stats(traces)
        assert stats.tool_failures == 1
        assert stats.tool_failure_rate == pytest.approx(0.5, abs=0.01)

    def test_settings_modified(self):
        traces = [
            ExecutionTrace(
                call_site="t", purpose="t", agent_name="A",
                outcome_type="setting_changed",
            ),
        ]
        stats = compute_outcome_stats(traces)
        assert stats.settings_modified == 1

    def test_search_browse_conversion(self):
        traces = [
            ExecutionTrace(
                call_site="t", purpose="t", agent_name="A",
                action_taken="SEARCH",
                outcome_type="search_results",
            ),
            ExecutionTrace(
                call_site="t", purpose="t", agent_name="A",
                action_taken="BROWSE",
                outcome_type="browse_insight",
            ),
            ExecutionTrace(
                call_site="t", purpose="t", agent_name="A",
                action_taken="SHARE",
                outcome_type="peer_shared",
            ),
        ]
        stats = compute_outcome_stats(traces)
        assert stats.search_count == 1
        assert stats.search_to_browse_rate == pytest.approx(1.0)
        assert stats.browse_to_share_rate == pytest.approx(1.0)


# ── 3. ExecutionTrace outcome fields ──────────────────────────────────


class TestTraceOutcomeFields:
    """Verify new outcome fields serialize correctly."""

    def test_outcome_fields_present(self):
        t = ExecutionTrace(
            call_site="test",
            purpose="test",
            agent_name="A",
            outcome_type="goal_progress",
            outcome_value="Changed top_k",
            goal_id="abc123",
        )
        d = t.to_dict()
        assert d["outcome_type"] == "goal_progress"
        assert d["outcome_value"] == "Changed top_k"
        assert d["goal_id"] == "abc123"

    def test_outcome_fields_default_empty(self):
        t = ExecutionTrace(call_site="t", purpose="t", agent_name="A")
        d = t.to_dict()
        assert d["outcome_type"] == ""
        assert d["outcome_value"] == ""
        assert d["goal_id"] == ""

    def test_round_trip_with_outcomes(self):
        t = ExecutionTrace(
            call_site="t",
            purpose="t",
            agent_name="A",
            outcome_type="setting_changed",
            outcome_value="memory_retrieval_top_k: 10 → 15",
        )
        d = t.to_dict()
        restored = ExecutionTrace.from_dict(d)
        assert restored.outcome_type == "setting_changed"
        assert restored.outcome_value == "memory_retrieval_top_k: 10 → 15"

    def test_jsonl_persistence_with_outcomes(self, tmp_path: Path):
        from agentgolem.harness.trace import append_trace, load_traces

        t = ExecutionTrace(
            call_site="t",
            purpose="t",
            agent_name="A",
            outcome_type="browse_insight",
            outcome_value="Learned about IIT",
            goal_id="g1",
        )
        append_trace(t, tmp_path)
        loaded = load_traces(tmp_path, limit=1)
        assert len(loaded) == 1
        assert loaded[0].outcome_type == "browse_insight"
        assert loaded[0].goal_id == "g1"
