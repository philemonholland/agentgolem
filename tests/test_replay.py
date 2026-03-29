"""Tests for the audit replay module."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agentgolem.dashboard.replay import AuditReplay

# ── Fixtures ─────────────────────────────────────────────────────────

ACTIVITY_ENTRIES = [
    {
        "timestamp": "2024-01-01T10:00:00+00:00",
        "event": "message_received",
        "log_level": "info",
        "message": "Hello agent",
        "target_id": "node-123",
    },
    {
        "timestamp": "2024-01-01T10:01:00+00:00",
        "event": "tool_call",
        "log_level": "info",
        "message": "Called web_browse",
        "target_id": "node-123",
    },
    {
        "timestamp": "2024-01-01T10:02:00+00:00",
        "event": "memory_write",
        "log_level": "info",
        "message": "Encoded 3 nodes",
    },
]

AUDIT_ENTRIES = [
    {
        "timestamp": "2024-01-01T10:01:30+00:00",
        "mutation_type": "add_node",
        "target_id": "node-123",
        "actor": "agent",
        "evidence": {"text": "test"},
    },
    {
        "timestamp": "2024-01-01T10:01:31+00:00",
        "mutation_type": "add_edge",
        "target_id": "edge-456",
        "actor": "agent",
        "evidence": {"source": "node-123"},
    },
    {
        "timestamp": "2024-01-01T10:02:00+00:00",
        "mutation_type": "update_node",
        "target_id": "node-123",
        "actor": "agent",
        "evidence": {"trust": 0.8},
    },
]


@pytest.fixture
def data_dir(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    activity = logs_dir / "activity.jsonl"
    activity.write_text(
        "\n".join(json.dumps(e) for e in ACTIVITY_ENTRIES) + "\n"
    )

    audit = logs_dir / "audit.jsonl"
    audit.write_text(
        "\n".join(json.dumps(e) for e in AUDIT_ENTRIES) + "\n"
    )

    return tmp_path


@pytest.fixture
def empty_data_dir(tmp_path):
    (tmp_path / "logs").mkdir()
    return tmp_path


# ── read_activity ────────────────────────────────────────────────────


def test_read_activity_empty(empty_data_dir):
    """read_activity with empty/missing log returns []."""
    replay = AuditReplay(empty_data_dir)
    assert replay.read_activity() == []


def test_read_activity_reads_entries(data_dir):
    """read_activity reads entries (most recent first)."""
    replay = AuditReplay(data_dir)
    entries = replay.read_activity()
    assert len(entries) == 3
    assert entries[0]["event"] == "memory_write"
    assert entries[-1]["event"] == "message_received"


def test_read_activity_filters_by_time_range(data_dir):
    """read_activity filters by from_time / to_time."""
    replay = AuditReplay(data_dir)
    entries = replay.read_activity(
        from_time=datetime(2024, 1, 1, 10, 0, 30, tzinfo=timezone.utc),
        to_time=datetime(2024, 1, 1, 10, 1, 30, tzinfo=timezone.utc),
    )
    assert len(entries) == 1
    assert entries[0]["event"] == "tool_call"


def test_read_activity_filters_by_event_type(data_dir):
    """read_activity filters by event_type (matches 'event' field)."""
    replay = AuditReplay(data_dir)
    entries = replay.read_activity(event_type="tool_call")
    assert len(entries) == 1
    assert entries[0]["message"] == "Called web_browse"


def test_read_activity_filters_by_search(data_dir):
    """read_activity filters by substring search across string values."""
    replay = AuditReplay(data_dir)
    entries = replay.read_activity(search="web_browse")
    assert len(entries) == 1
    assert entries[0]["event"] == "tool_call"


def test_read_activity_limit_and_offset(data_dir):
    """read_activity respects limit and offset pagination."""
    replay = AuditReplay(data_dir)
    page1 = replay.read_activity(limit=2, offset=0)
    page2 = replay.read_activity(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 1
    # page1 has the two most recent, page2 the oldest
    assert page1[0]["event"] == "memory_write"
    assert page2[0]["event"] == "message_received"


# ── read_audit ───────────────────────────────────────────────────────


def test_read_audit_empty(empty_data_dir):
    """read_audit with empty/missing log returns []."""
    replay = AuditReplay(empty_data_dir)
    assert replay.read_audit() == []


def test_read_audit_reads_entries(data_dir):
    """read_audit reads entries (most recent first)."""
    replay = AuditReplay(data_dir)
    entries = replay.read_audit()
    assert len(entries) == 3
    assert entries[0]["mutation_type"] == "update_node"
    assert entries[-1]["mutation_type"] == "add_node"


def test_read_audit_filters_by_mutation_type(data_dir):
    """read_audit filters by mutation_type."""
    replay = AuditReplay(data_dir)
    entries = replay.read_audit(mutation_type="add_node")
    assert len(entries) == 1
    assert entries[0]["target_id"] == "node-123"


def test_read_audit_filters_by_target_id(data_dir):
    """read_audit filters by target_id."""
    replay = AuditReplay(data_dir)
    entries = replay.read_audit(target_id="node-123")
    assert len(entries) == 2
    types = {e["mutation_type"] for e in entries}
    assert types == {"add_node", "update_node"}


def test_read_audit_filters_by_actor(data_dir):
    """read_audit filters by actor."""
    replay = AuditReplay(data_dir)
    entries = replay.read_audit(actor="agent")
    assert len(entries) == 3
    entries_none = replay.read_audit(actor="unknown")
    assert len(entries_none) == 0


# ── get_timeline ─────────────────────────────────────────────────────


def test_get_timeline_merges_chronologically(data_dir):
    """get_timeline merges both logs in chronological order (most recent first)."""
    replay = AuditReplay(data_dir)
    timeline = replay.get_timeline()
    assert len(timeline) == 6
    # Verify descending timestamp order
    timestamps = [e["timestamp"] for e in timeline]
    assert timestamps == sorted(timestamps, reverse=True)
    # Every entry is tagged with a log_source
    sources = {e["log_source"] for e in timeline}
    assert sources == {"activity", "audit"}


def test_get_timeline_respects_time_filter(data_dir):
    """get_timeline applies from_time / to_time."""
    replay = AuditReplay(data_dir)
    timeline = replay.get_timeline(
        from_time=datetime(2024, 1, 1, 10, 1, 0, tzinfo=timezone.utc),
        to_time=datetime(2024, 1, 1, 10, 1, 31, tzinfo=timezone.utc),
    )
    # Should include: tool_call (10:01), add_node (10:01:30), add_edge (10:01:31)
    assert len(timeline) == 3


# ── trace_causal_chain ───────────────────────────────────────────────


def test_trace_causal_chain(data_dir):
    """trace_causal_chain finds related entries across both logs."""
    replay = AuditReplay(data_dir)
    chain = replay.trace_causal_chain("node-123")
    # Expected: activity entries mentioning node-123 (2) + audit target_id matches (2)
    assert len(chain) == 4
    # Sorted oldest-first (cause → effect)
    timestamps = [e["timestamp"] for e in chain]
    assert timestamps == sorted(timestamps)
    # Every entry is tagged with log_source
    assert all("log_source" in e for e in chain)


def test_trace_causal_chain_no_match(data_dir):
    """trace_causal_chain with unknown target returns []."""
    replay = AuditReplay(data_dir)
    assert replay.trace_causal_chain("nonexistent") == []
