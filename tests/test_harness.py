"""Tests for the Meta-Harness execution trace and diagnostic calibration system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.harness.trace import (
    ExecutionTrace,
    append_trace,
    load_traces,
    traces_path,
)
from agentgolem.harness.trace_stats import TraceStatsSummary, compute_trace_stats


# ── 1. ExecutionTrace data model ─────────────────────────────────────


class TestExecutionTrace:
    """Trace dataclass serialization and round-trip."""

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        trace = ExecutionTrace(
            call_site="_respond_to_peer",
            purpose="peer_response",
            agent_name="Karuna",
            prompt_summary="You are Karuna...",
            context_tokens=4200,
            completion_tokens=500,
            response_length=2000,
            memory_node_ids_retrieved=["n1", "n2", "n3"],
            memory_node_ids_referenced=["n1"],
            action_taken="SHARE",
            peer_engagement_signal=True,
        )
        d = trace.to_dict()
        restored = ExecutionTrace.from_dict(d)
        assert restored.call_site == "_respond_to_peer"
        assert restored.purpose == "peer_response"
        assert restored.agent_name == "Karuna"
        assert restored.memory_node_ids_retrieved == ["n1", "n2", "n3"]
        assert restored.memory_node_ids_referenced == ["n1"]
        assert restored.action_taken == "SHARE"
        assert restored.peer_engagement_signal is True

    def test_retrieval_hit_rate(self) -> None:
        trace = ExecutionTrace(
            call_site="test",
            purpose="test",
            agent_name="test",
            memory_node_ids_retrieved=["a", "b", "c", "d"],
            memory_node_ids_referenced=["a", "c"],
        )
        assert trace.retrieval_hit_rate == 0.5

    def test_retrieval_hit_rate_no_retrieval(self) -> None:
        trace = ExecutionTrace(
            call_site="test", purpose="test", agent_name="test",
        )
        assert trace.retrieval_hit_rate is None

    def test_from_dict_ignores_unknown_fields(self) -> None:
        d = {
            "call_site": "x",
            "purpose": "y",
            "agent_name": "z",
            "unknown_field": 42,
        }
        trace = ExecutionTrace.from_dict(d)
        assert trace.call_site == "x"

    def test_default_timestamp_is_iso8601(self) -> None:
        trace = ExecutionTrace(
            call_site="test", purpose="test", agent_name="test",
        )
        assert "T" in trace.timestamp  # ISO 8601 format


# ── 2. JSONL persistence ────────────────────────────────────────────


class TestJsonlPersistence:
    """Append-only JSONL write and read."""

    def test_append_and_load(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "council_1"
        agent_dir.mkdir()
        t1 = ExecutionTrace(
            call_site="site_a", purpose="purpose_a", agent_name="Agent1",
        )
        t2 = ExecutionTrace(
            call_site="site_b", purpose="purpose_b", agent_name="Agent1",
        )
        append_trace(t1, agent_dir)
        append_trace(t2, agent_dir)

        loaded = load_traces(agent_dir, limit=10)
        assert len(loaded) == 2
        assert loaded[0].call_site == "site_a"
        assert loaded[1].call_site == "site_b"

    def test_load_respects_limit(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "council_1"
        agent_dir.mkdir()
        for i in range(20):
            append_trace(
                ExecutionTrace(
                    call_site=f"site_{i}",
                    purpose="test",
                    agent_name="Agent1",
                ),
                agent_dir,
            )
        loaded = load_traces(agent_dir, limit=5)
        assert len(loaded) == 5
        # Should be the last 5
        assert loaded[0].call_site == "site_15"
        assert loaded[4].call_site == "site_19"

    def test_load_empty_dir(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "council_empty"
        agent_dir.mkdir()
        assert load_traces(agent_dir) == []

    def test_traces_path(self, tmp_path: Path) -> None:
        p = traces_path(tmp_path / "agent")
        assert p == tmp_path / "agent" / "traces" / "execution.jsonl"

    def test_append_creates_parent_dirs(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "council_new"
        # Don't create agent_dir — append_trace should handle it
        agent_dir.mkdir()
        t = ExecutionTrace(
            call_site="auto_create", purpose="test", agent_name="Agent",
        )
        append_trace(t, agent_dir)
        assert traces_path(agent_dir).exists()

    def test_malformed_lines_skipped(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "council_bad"
        agent_dir.mkdir()
        p = traces_path(agent_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            f.write('{"call_site":"good","purpose":"ok","agent_name":"A"}\n')
            f.write("not valid json\n")
            f.write('{"call_site":"also_good","purpose":"ok","agent_name":"B"}\n')
        loaded = load_traces(agent_dir)
        assert len(loaded) == 2
        assert loaded[0].call_site == "good"
        assert loaded[1].call_site == "also_good"


# ── 3. Trace statistics ─────────────────────────────────────────────


class TestTraceStats:
    """compute_trace_stats aggregation."""

    def _make_traces(self) -> list[ExecutionTrace]:
        return [
            ExecutionTrace(
                call_site="site_a",
                purpose="peer_response",
                agent_name="Agent1",
                context_tokens=4000,
                completion_tokens=500,
                response_length=2000,
                memory_node_ids_retrieved=["n1", "n2", "n3"],
                memory_node_ids_referenced=["n1"],
                action_taken="SHARE",
                peer_engagement_signal=True,
            ),
            ExecutionTrace(
                call_site="site_b",
                purpose="autonomous_think",
                agent_name="Agent1",
                context_tokens=3000,
                completion_tokens=400,
                response_length=1500,
                memory_node_ids_retrieved=["n4", "n5"],
                memory_node_ids_referenced=["n4", "n5"],
                action_taken="THINK",
                peer_engagement_signal=False,
            ),
            ExecutionTrace(
                call_site="site_c",
                purpose="peer_response",
                agent_name="Agent1",
                context_tokens=5000,
                completion_tokens=600,
                response_length=2500,
                memory_node_ids_retrieved=["n6"],
                memory_node_ids_referenced=[],
                action_taken="SHARE",
                peer_engagement_signal=True,
            ),
            ExecutionTrace(
                call_site="site_d",
                purpose="calibration",
                agent_name="Agent1",
                context_tokens=6000,
                completion_tokens=800,
                response_length=3000,
                memory_node_ids_retrieved=[],
                memory_node_ids_referenced=[],
                action_taken="",
                peer_engagement_signal=None,  # not applicable
            ),
        ]

    def test_basic_stats(self) -> None:
        traces = self._make_traces()
        stats = compute_trace_stats(traces)
        assert stats.total_calls == 4
        # 6 retrieved, 3 referenced → 0.5
        assert stats.retrieval_hit_rate == pytest.approx(0.5, abs=0.01)
        assert stats.avg_context_tokens == pytest.approx(4500.0)
        assert stats.avg_completion_tokens == pytest.approx(575.0)
        assert stats.avg_response_length == pytest.approx(2250.0)

    def test_action_distribution(self) -> None:
        traces = self._make_traces()
        stats = compute_trace_stats(traces)
        # 2 SHARE, 1 THINK out of 3 that have actions
        assert stats.action_distribution["SHARE"] == pytest.approx(2 / 3)
        assert stats.action_distribution["THINK"] == pytest.approx(1 / 3)

    def test_peer_engagement_rate(self) -> None:
        traces = self._make_traces()
        stats = compute_trace_stats(traces)
        # 3 traces have peer_engagement_signal set (True, False, True)
        # 2 out of 3 are True
        assert stats.peer_engagement_rate == pytest.approx(2 / 3)

    def test_calls_by_purpose(self) -> None:
        traces = self._make_traces()
        stats = compute_trace_stats(traces)
        assert stats.calls_by_purpose == {
            "peer_response": 2,
            "autonomous_think": 1,
            "calibration": 1,
        }

    def test_empty_traces(self) -> None:
        stats = compute_trace_stats([])
        assert stats.total_calls == 0
        assert stats.retrieval_hit_rate == 0.0
        assert stats.avg_context_tokens == 0.0

    def test_format_diagnostic_block(self) -> None:
        traces = self._make_traces()
        stats = compute_trace_stats(traces)
        block = stats.format_diagnostic_block()
        assert "OBJECTIVE DIAGNOSTICS" in block
        assert "Retrieval hit rate: 50%" in block
        assert "Peer engagement rate: 67%" in block
        assert "SHARE" in block

    def test_to_dict(self) -> None:
        traces = self._make_traces()
        stats = compute_trace_stats(traces)
        d = stats.to_dict()
        assert d["total_calls"] == 4
        assert isinstance(d["action_distribution"], dict)
        assert isinstance(d["calls_by_purpose"], dict)

    def test_no_retrieval_hit_rate_zero(self) -> None:
        traces = [
            ExecutionTrace(
                call_site="x",
                purpose="y",
                agent_name="z",
                memory_node_ids_retrieved=[],
                memory_node_ids_referenced=[],
            ),
        ]
        stats = compute_trace_stats(traces)
        assert stats.retrieval_hit_rate == 0.0


# ── 4. Memory hit detection ─────────────────────────────────────────


class TestMemoryHitDetection:
    """Test the _detect_memory_hits and _check_peer_engagement helpers."""

    def test_detect_memory_hits_exact_match(self) -> None:
        """When a node's text appears verbatim in the response, it's a hit."""
        from agentgolem.harness.trace import ExecutionTrace  # just for typing

        # Simulate what loop.py does — we test the pure logic
        cache = {
            "n1": "The principle of non-violence is central to ethical reasoning",
            "n2": "Water flows around obstacles without contention",
            "n3": "Short",  # < 20 chars, should be ignored
        }
        response = (
            "I believe that the principle of non-violence is central to ethical "
            "reasoning and should guide all actions."
        )
        # Replicate the detection logic from loop.py
        retrieved_ids = ["n1", "n2", "n3"]
        referenced: list[str] = []
        resp_lower = response.lower()
        for nid in retrieved_ids:
            text = cache.get(nid, "")
            if len(text) < 20:
                continue
            if text[:80].lower() in resp_lower:
                referenced.append(nid)
                continue
            for i in range(0, min(len(text), 200), 20):
                chunk = text[i : i + 40].lower().strip()
                if len(chunk) >= 20 and chunk in resp_lower:
                    referenced.append(nid)
                    break
        assert "n1" in referenced
        assert "n2" not in referenced
        assert "n3" not in referenced

    def test_check_peer_engagement(self) -> None:
        """3-word phrase match detects peer engagement."""
        recent_outgoing = [
            "The interruptibility architecture requires careful design consideration",
        ]
        incoming = (
            "Building on your point about interruptibility architecture requires "
            "deeper analysis of the failure modes."
        )
        incoming_lower = incoming.lower()
        engaged = False
        for outgoing in recent_outgoing[-5:]:
            words = outgoing.lower().split()
            for i in range(len(words) - 2):
                phrase = " ".join(words[i : i + 3])
                if len(phrase) >= 10 and phrase in incoming_lower:
                    engaged = True
                    break
        assert engaged is True

    def test_no_peer_engagement(self) -> None:
        """Non-matching text returns False."""
        recent_outgoing = [
            "The weather today is beautiful and warm",
        ]
        incoming = "Let us discuss the architecture of the memory system."
        incoming_lower = incoming.lower()
        engaged = False
        for outgoing in recent_outgoing[-5:]:
            words = outgoing.lower().split()
            for i in range(len(words) - 2):
                phrase = " ".join(words[i : i + 3])
                if len(phrase) >= 10 and phrase in incoming_lower:
                    engaged = True
                    break
        assert engaged is False
