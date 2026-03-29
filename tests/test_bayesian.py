"""Tests for the Bayesian trust‑update module."""
from __future__ import annotations

import json

import pytest

from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import (
    ConceptualNode,
    NodeType,
    NodeUpdate,
    Source,
    SourceKind,
)
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.trust.bayesian import TYPE_PRIORS, BayesianTrustUpdater

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    conn = await init_db(tmp_path / "test.db")
    yield conn
    await close_db(conn)


@pytest.fixture
async def store(db, tmp_path):
    audit = AuditLogger(tmp_path)
    return SQLiteMemoryStore(db, audit)


@pytest.fixture
def audit(tmp_path):
    return AuditLogger(tmp_path)


@pytest.fixture
def updater(store, audit):
    return BayesianTrustUpdater(store, audit)


def _node(
    trust: float = 0.5,
    usefulness: float = 0.6,
    **kw,
) -> ConceptualNode:
    return ConceptualNode(
        text="test fact",
        type=NodeType.FACT,
        trustworthiness=trust,
        base_usefulness=usefulness,
        **kw,
    )


def _source(
    reliability: float = 0.8,
    group: str = "",
    **kw,
) -> Source:
    return Source(
        kind=SourceKind.HUMAN,
        origin="tester",
        reliability=reliability,
        independence_group=group,
        **kw,
    )


# ------------------------------------------------------------------
# Core Bayesian update
# ------------------------------------------------------------------


async def test_confirming_source_raises_trust(store, updater):
    node = _node(trust=0.5)
    await store.add_node(node)
    p_new = await updater.update_trust(node.id, _source(reliability=0.8), confirms=True)
    assert p_new > 0.5


async def test_contradicting_source_lowers_trust(store, updater):
    node = _node(trust=0.5)
    await store.add_node(node)
    p_new = await updater.update_trust(node.id, _source(reliability=0.8), confirms=False)
    assert p_new < 0.5


async def test_odds_math_correctness(store, updater):
    """Verify exact Bayesian odds calculation with known values."""
    p_old = 0.6
    r = 0.9
    node = _node(trust=p_old)
    await store.add_node(node)

    p_new = await updater.update_trust(node.id, _source(reliability=r), confirms=True)

    # Manual computation (no discount — no existing sources)
    odds = p_old / (1.0 - p_old)          # 1.5
    lr = r / (1.0 - r)                    # 9.0
    odds_new = odds * lr                   # 13.5
    expected = odds_new / (1.0 + odds_new) # 0.931034…

    assert p_new == pytest.approx(expected, abs=1e-9)


# ------------------------------------------------------------------
# Independence discount
# ------------------------------------------------------------------


async def test_independence_discount_first_source(store, updater):
    node = _node()
    await store.add_node(node)
    # No existing sources linked yet → first from group → discount = 1.0
    discount = await updater.get_independence_discount(node.id, _source(group="g1"))
    assert discount == 1.0


async def test_independence_discount_same_group(store, updater):
    node = _node()
    await store.add_node(node)

    # Link two sources with the same group
    for _ in range(2):
        s = _source(group="g1")
        await store.add_source(s)
        await store.link_node_source(node.id, s.id)

    # Third source from same group → n=2 → 0.5**2 = 0.25
    discount = await updater.get_independence_discount(node.id, _source(group="g1"))
    assert discount == pytest.approx(0.25)


async def test_independence_discount_empty_group(store, updater):
    node = _node()
    await store.add_node(node)
    discount = await updater.get_independence_discount(node.id, _source(group=""))
    assert discount == 1.0


# ------------------------------------------------------------------
# Clamping
# ------------------------------------------------------------------


async def test_clamp_bounds_near_zero(store, updater):
    node = _node(trust=0.02)
    await store.add_node(node)
    # Very reliable contradiction should push toward 0 but clamp at 0.01
    p_new = await updater.update_trust(node.id, _source(reliability=0.99), confirms=False)
    assert p_new >= 0.01


async def test_clamp_bounds_near_one(store, updater):
    node = _node(trust=0.98)
    await store.add_node(node)
    # Very reliable confirmation should push toward 1 but clamp at 0.99
    p_new = await updater.update_trust(node.id, _source(reliability=0.99), confirms=True)
    assert p_new <= 0.99


# ------------------------------------------------------------------
# TYPE_PRIORS
# ------------------------------------------------------------------


async def test_type_priors_all_types():
    for nt in NodeType:
        assert nt in TYPE_PRIORS, f"Missing prior for {nt}"
    assert TYPE_PRIORS[NodeType.FACT] == 0.5
    assert TYPE_PRIORS[NodeType.IDENTITY] == 0.9


# ------------------------------------------------------------------
# Audit logging
# ------------------------------------------------------------------


async def test_audit_logged(store, updater, audit):
    node = _node(trust=0.5)
    await store.add_node(node)
    await updater.update_trust(node.id, _source(reliability=0.7), confirms=True)

    entries = audit.read(limit=10)
    trust_entries = [e for e in entries if e["mutation_type"] == "trust_update"]
    assert len(trust_entries) >= 1
    entry = trust_entries[0]
    assert entry["target_id"] == node.id
    assert entry["evidence"]["confirms"] is True
    assert "diff" in entry
