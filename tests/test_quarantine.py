"""Tests for the quarantine / suspicion module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import MemoryCluster, NodeStatus
from agentgolem.memory.schema import init_db
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.trust.quarantine import QuarantineManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path: Path):
    conn = await init_db(tmp_path / "test.db")
    yield conn
    await conn.close()


@pytest.fixture
def audit(tmp_path: Path) -> AuditLogger:
    return AuditLogger(tmp_path)


@pytest.fixture
async def store(db, audit) -> SQLiteMemoryStore:
    return SQLiteMemoryStore(db, audit)


@pytest.fixture
def qm(store, audit) -> QuarantineManager:
    return QuarantineManager(store, audit)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_cluster(
    label: str = "test",
    emotion_score: float = 0.0,
    base_usefulness: float = 0.5,
    trustworthiness: float = 0.5,
    contradiction_status: str = "none",
) -> MemoryCluster:
    return MemoryCluster(
        label=label,
        emotion_score=emotion_score,
        base_usefulness=base_usefulness,
        trustworthiness=trustworthiness,
        contradiction_status=contradiction_status,
    )


def _read_audit_entries(tmp_path: Path) -> list[dict]:
    log_path = tmp_path / "logs" / "audit.jsonl"
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


# ------------------------------------------------------------------
# Tests – evaluate_cluster
# ------------------------------------------------------------------

async def test_evaluate_high_emotion_low_trust_is_suspicious(store, qm):
    """Emotion > threshold AND trust_useful < threshold → suspicious."""
    cluster = _make_cluster(emotion_score=0.9, base_usefulness=0.2, trustworthiness=0.5)
    # trust_useful = 0.2 * 0.5 = 0.10, well below 0.3
    cid = await store.add_cluster(cluster)
    assert await qm.evaluate_cluster(cid) is True


async def test_evaluate_low_emotion_not_suspicious(store, qm):
    """Emotion below threshold → not suspicious regardless of trust."""
    cluster = _make_cluster(emotion_score=0.3, base_usefulness=0.1, trustworthiness=0.1)
    cid = await store.add_cluster(cluster)
    assert await qm.evaluate_cluster(cid) is False


async def test_evaluate_high_trust_not_suspicious(store, qm):
    """Trust_useful above threshold → not suspicious regardless of emotion."""
    cluster = _make_cluster(emotion_score=0.95, base_usefulness=0.8, trustworthiness=0.8)
    # trust_useful = 0.64
    cid = await store.add_cluster(cluster)
    assert await qm.evaluate_cluster(cid) is False


# ------------------------------------------------------------------
# Tests – quarantine / release
# ------------------------------------------------------------------

async def test_quarantine_sets_status(store, qm):
    """quarantine() sets contradiction_status to 'quarantined'."""
    cluster = _make_cluster()
    cid = await store.add_cluster(cluster)
    await qm.quarantine(cid, "test reason")
    updated = await store.get_cluster(cid)
    assert updated is not None
    assert updated.contradiction_status == "quarantined"


async def test_release_clears_status(store, qm):
    """release() resets contradiction_status to 'none'."""
    cluster = _make_cluster(contradiction_status="quarantined")
    cid = await store.add_cluster(cluster)
    await qm.release(cid, "cleared by review")
    updated = await store.get_cluster(cid)
    assert updated is not None
    assert updated.contradiction_status == "none"


# ------------------------------------------------------------------
# Tests – get_quarantined
# ------------------------------------------------------------------

async def test_get_quarantined_returns_only_quarantined(store, qm):
    """Only clusters with contradiction_status='quarantined' are returned."""
    c1 = _make_cluster(label="clean")
    c2 = _make_cluster(label="quarantined-one")
    c3 = _make_cluster(label="quarantined-two")

    id1 = await store.add_cluster(c1)
    id2 = await store.add_cluster(c2)
    id3 = await store.add_cluster(c3)

    await qm.quarantine(id2, "reason2")
    await qm.quarantine(id3, "reason3")

    quarantined = await qm.get_quarantined()
    ids = {c.id for c in quarantined}
    assert id2 in ids
    assert id3 in ids
    assert id1 not in ids


# ------------------------------------------------------------------
# Tests – canonical semantics
# ------------------------------------------------------------------

async def test_quarantined_not_canonical(store, qm):
    """A quarantined cluster has contradiction_status='quarantined', confirming non-canonical."""
    cluster = _make_cluster()
    cid = await store.add_cluster(cluster)
    await qm.quarantine(cid, "suspicious")
    updated = await store.get_cluster(cid)
    assert updated is not None
    assert updated.contradiction_status == "quarantined"


# ------------------------------------------------------------------
# Tests – audit logging
# ------------------------------------------------------------------

async def test_audit_logged_on_quarantine(store, qm, tmp_path):
    """Audit log should contain an entry for quarantine."""
    cluster = _make_cluster()
    cid = await store.add_cluster(cluster)
    await qm.quarantine(cid, "unit test quarantine")

    entries = _read_audit_entries(tmp_path)
    quarantine_entries = [e for e in entries if e["mutation_type"] == "quarantine"]
    assert len(quarantine_entries) >= 1
    assert quarantine_entries[-1]["target_id"] == cid
    assert quarantine_entries[-1]["evidence"]["reason"] == "unit test quarantine"


async def test_audit_logged_on_release(store, qm, tmp_path):
    """Audit log should contain an entry for quarantine release."""
    cluster = _make_cluster(contradiction_status="quarantined")
    cid = await store.add_cluster(cluster)
    await qm.release(cid, "unit test release")

    entries = _read_audit_entries(tmp_path)
    release_entries = [e for e in entries if e["mutation_type"] == "quarantine_release"]
    assert len(release_entries) >= 1
    assert release_entries[-1]["target_id"] == cid
    assert release_entries[-1]["evidence"]["reason"] == "unit test release"


# ------------------------------------------------------------------
# Tests – evaluate_and_quarantine convenience
# ------------------------------------------------------------------

async def test_evaluate_and_quarantine_convenience(store, qm):
    """evaluate_and_quarantine quarantines suspicious clusters and returns True."""
    suspicious = _make_cluster(emotion_score=0.9, base_usefulness=0.1, trustworthiness=0.1)
    safe = _make_cluster(emotion_score=0.1, base_usefulness=0.9, trustworthiness=0.9)

    sid = await store.add_cluster(suspicious)
    safe_id = await store.add_cluster(safe)

    assert await qm.evaluate_and_quarantine(sid) is True
    assert await qm.evaluate_and_quarantine(safe_id) is False

    # Verify quarantine was actually applied
    updated = await store.get_cluster(sid)
    assert updated is not None
    assert updated.contradiction_status == "quarantined"

    safe_updated = await store.get_cluster(safe_id)
    assert safe_updated is not None
    assert safe_updated.contradiction_status == "none"
