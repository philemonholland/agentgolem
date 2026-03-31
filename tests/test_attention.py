"""Tests for the attention-request system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.runtime.attention import (
    AttentionRequest,
    list_all,
    list_pending,
    load_request,
    resolve_oldest_blocking,
    resolve_request,
    save_request,
)


# ── 1. Model round-trip ────────────────────────────────────────────


def test_attention_request_round_trip():
    req = AttentionRequest(
        agent_name="Anvaya",
        reason="tool_failure",
        context="Search failed 3 times",
        urgency="blocking",
    )
    d = req.to_dict()
    restored = AttentionRequest.from_dict(d)
    assert restored.agent_name == "Anvaya"
    assert restored.reason == "tool_failure"
    assert restored.urgency == "blocking"
    assert not restored.resolved


def test_from_dict_ignores_unknown_keys():
    data = {"agent_name": "K", "reason": "x", "extra_field": 42}
    req = AttentionRequest.from_dict(data)
    assert req.agent_name == "K"


# ── 2. Persistence ────────────────────────────────────────────────


def test_save_and_load(tmp_path: Path):
    req = AttentionRequest(agent_name="Karuna", reason="need_input", context="?")
    save_request(req, tmp_path)
    loaded = load_request(req.id, tmp_path)
    assert loaded is not None
    assert loaded.agent_name == "Karuna"
    assert loaded.reason == "need_input"


def test_load_nonexistent(tmp_path: Path):
    assert load_request("does-not-exist", tmp_path) is None


def test_list_pending_returns_unresolved(tmp_path: Path):
    r1 = AttentionRequest(agent_name="A", reason="r1")
    r2 = AttentionRequest(agent_name="B", reason="r2", resolved=True)
    r3 = AttentionRequest(agent_name="C", reason="r3")
    save_request(r1, tmp_path)
    save_request(r2, tmp_path)
    save_request(r3, tmp_path)
    pending = list_pending(tmp_path)
    ids = {r.id for r in pending}
    assert r1.id in ids
    assert r3.id in ids
    assert r2.id not in ids


def test_list_all(tmp_path: Path):
    r1 = AttentionRequest(agent_name="A", reason="r1")
    r2 = AttentionRequest(agent_name="B", reason="r2", resolved=True)
    save_request(r1, tmp_path)
    save_request(r2, tmp_path)
    assert len(list_all(tmp_path)) == 2


# ── 3. Resolution ────────────────────────────────────────────────


def test_resolve_request(tmp_path: Path):
    req = AttentionRequest(agent_name="A", reason="tool_failure")
    save_request(req, tmp_path)
    resolved = resolve_request(req.id, tmp_path, "Fixed API key")
    assert resolved is not None
    assert resolved.resolved is True
    assert resolved.resolution == "Fixed API key"
    assert resolved.resolved_at is not None
    # Verify persisted
    reloaded = load_request(req.id, tmp_path)
    assert reloaded is not None
    assert reloaded.resolved is True


def test_resolve_nonexistent(tmp_path: Path):
    assert resolve_request("nope", tmp_path) is None


def test_resolve_oldest_blocking(tmp_path: Path):
    r1 = AttentionRequest(
        agent_name="A", reason="r1", urgency="informational",
        timestamp="2025-01-01T00:00:00+00:00",
    )
    r2 = AttentionRequest(
        agent_name="B", reason="r2", urgency="blocking",
        timestamp="2025-01-01T00:00:01+00:00",
    )
    r3 = AttentionRequest(
        agent_name="C", reason="r3", urgency="blocking",
        timestamp="2025-01-01T00:00:02+00:00",
    )
    save_request(r1, tmp_path)
    save_request(r2, tmp_path)
    save_request(r3, tmp_path)
    resolved = resolve_oldest_blocking(tmp_path, "Done")
    assert resolved is not None
    assert resolved.id == r2.id
    # r3 still pending
    pending = list_pending(tmp_path)
    blocking_pending = [r for r in pending if r.urgency == "blocking"]
    assert len(blocking_pending) == 1
    assert blocking_pending[0].id == r3.id


def test_resolve_oldest_blocking_none_pending(tmp_path: Path):
    assert resolve_oldest_blocking(tmp_path) is None


# ── 4. Informational vs blocking ────────────────────────────────


def test_informational_does_not_block(tmp_path: Path):
    req = AttentionRequest(agent_name="A", reason="discovery", urgency="informational")
    save_request(req, tmp_path)
    # resolve_oldest_blocking should skip informational
    assert resolve_oldest_blocking(tmp_path) is None
    # but it's still in pending
    assert len(list_pending(tmp_path)) == 1


# ── 5. File integrity ────────────────────────────────────────────


def test_corrupted_file_skipped(tmp_path: Path):
    d = tmp_path / "attention_requests"
    d.mkdir(parents=True, exist_ok=True)
    (d / "bad.json").write_text("not json", encoding="utf-8")
    req = AttentionRequest(agent_name="Good", reason="ok")
    save_request(req, tmp_path)
    pending = list_pending(tmp_path)
    assert len(pending) == 1
    assert pending[0].agent_name == "Good"
