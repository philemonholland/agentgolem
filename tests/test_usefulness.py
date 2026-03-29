"""Tests for UsefulnessScorer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import ConceptualNode, NodeType, NodeUpdate
from agentgolem.memory.schema import init_db
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.trust.usefulness import UsefulnessScorer


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path: Path):
    conn = await init_db(tmp_path / "test.db")
    yield conn
    await conn.close()


@pytest.fixture
async def audit(tmp_path: Path) -> AuditLogger:
    return AuditLogger(tmp_path)


@pytest.fixture
async def store(db, audit) -> SQLiteMemoryStore:
    return SQLiteMemoryStore(db, audit)


@pytest.fixture
def scorer(store, audit) -> UsefulnessScorer:
    return UsefulnessScorer(store, audit)


async def _make_node(
    store: SQLiteMemoryStore,
    *,
    base_usefulness: float = 0.5,
    trustworthiness: float = 0.5,
) -> str:
    node = ConceptualNode(
        text="test fact",
        type=NodeType.FACT,
        base_usefulness=base_usefulness,
        trustworthiness=trustworthiness,
    )
    return await store.add_node(node)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

async def test_bump_retrieval_increases(scorer: UsefulnessScorer, store: SQLiteMemoryStore):
    nid = await _make_node(store, base_usefulness=0.5)
    result = await scorer.bump_retrieval(nid)
    assert result == pytest.approx(0.51)


async def test_bump_task_success_increases(scorer: UsefulnessScorer, store: SQLiteMemoryStore):
    nid = await _make_node(store, base_usefulness=0.5)
    result = await scorer.bump_task_success(nid)
    assert result == pytest.approx(0.55)


async def test_penalize_misleading_decreases(scorer: UsefulnessScorer, store: SQLiteMemoryStore):
    nid = await _make_node(store, base_usefulness=0.5)
    result = await scorer.penalize_misleading(nid)
    assert result == pytest.approx(0.40)


async def test_clamp_upper_bound(scorer: UsefulnessScorer, store: SQLiteMemoryStore):
    nid = await _make_node(store, base_usefulness=0.99)
    result = await scorer.bump_task_success(nid)
    assert result == 1.0


async def test_clamp_lower_bound(scorer: UsefulnessScorer, store: SQLiteMemoryStore):
    nid = await _make_node(store, base_usefulness=0.05)
    result = await scorer.penalize_misleading(nid)
    assert result == 0.0


async def test_compute_trust_useful(scorer: UsefulnessScorer):
    node = ConceptualNode(
        text="test",
        type=NodeType.FACT,
        base_usefulness=0.8,
        trustworthiness=0.6,
    )
    assert scorer.compute_trust_useful(node) == pytest.approx(0.48)


async def test_batch_recompute(scorer: UsefulnessScorer, store: SQLiteMemoryStore):
    nid1 = await _make_node(store, base_usefulness=0.8, trustworthiness=0.6)
    nid2 = await _make_node(store, base_usefulness=0.4, trustworthiness=0.9)

    results = await scorer.batch_recompute([nid1, nid2])

    # get_node bumps access so we read the persisted values directly
    assert nid1 in results
    assert nid2 in results
    assert results[nid1] == pytest.approx(0.8 * 0.6)
    assert results[nid2] == pytest.approx(0.4 * 0.9)


async def test_audit_logged(
    scorer: UsefulnessScorer,
    store: SQLiteMemoryStore,
    audit: AuditLogger,
):
    nid = await _make_node(store, base_usefulness=0.5)
    await scorer.bump_retrieval(nid)

    entries = audit.read(limit=10)
    usefulness_entries = [
        e for e in entries if e["mutation_type"] == "usefulness_bump_retrieval"
    ]
    assert len(usefulness_entries) >= 1
    entry = usefulness_entries[0]
    assert entry["target_id"] == nid
    assert entry["evidence"]["before"] == pytest.approx(0.5)
    assert entry["evidence"]["after"] == pytest.approx(0.51)
    assert "diff" in entry


async def test_penalize_from_low_value(scorer: UsefulnessScorer, store: SQLiteMemoryStore):
    nid = await _make_node(store, base_usefulness=0.03)
    result = await scorer.penalize_misleading(nid)
    assert result == 0.0

    # Verify persisted value is also 0.0
    node = await store.get_node(nid)
    assert node is not None
    assert node.base_usefulness == 0.0
