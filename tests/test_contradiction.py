"""Tests for contradiction detection and resolution."""
from __future__ import annotations

import pytest

from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryEdge,
    NodeStatus,
    NodeType,
    NodeUpdate,
)
from agentgolem.memory.schema import init_db
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.trust.contradiction import ContradictionPair, ContradictionResolver


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    conn = await init_db(tmp_path / "test.db")
    yield conn
    await conn.close()


@pytest.fixture
async def audit(tmp_path):
    return AuditLogger(tmp_path)


@pytest.fixture
async def store(db, audit):
    return SQLiteMemoryStore(db, audit)


@pytest.fixture
async def resolver(store, audit):
    return ContradictionResolver(store, audit)


def _node(text: str, trust: float = 0.5, useful: float = 0.5, **kw) -> ConceptualNode:
    return ConceptualNode(
        text=text,
        type=NodeType.FACT,
        trustworthiness=trust,
        base_usefulness=useful,
        **kw,
    )


# ------------------------------------------------------------------
# Detection
# ------------------------------------------------------------------


async def test_detect_contradictions_finds_pairs(store, resolver):
    a = _node("Earth is round", trust=0.9)
    b = _node("Earth is flat", trust=0.2)
    await store.add_node(a)
    await store.add_node(b)

    edge = MemoryEdge(source_id=a.id, target_id=b.id, edge_type=EdgeType.CONTRADICTS)
    await store.add_edge(edge)

    pairs = await resolver.detect_contradictions(a.id)
    assert len(pairs) == 1
    assert pairs[0].node_a_id == a.id
    assert pairs[0].node_b_id == b.id
    assert pairs[0].status == "unresolved"


async def test_detect_no_contradictions(store, resolver):
    a = _node("Python is great")
    await store.add_node(a)

    pairs = await resolver.detect_contradictions(a.id)
    assert pairs == []


async def test_severity_calculation(store, resolver):
    a = _node("Claim A", trust=0.8)
    b = _node("Claim B", trust=0.3)
    await store.add_node(a)
    await store.add_node(b)

    edge = MemoryEdge(source_id=a.id, target_id=b.id, edge_type=EdgeType.CONTRADICTS)
    await store.add_edge(edge)

    pairs = await resolver.detect_contradictions(a.id)
    assert len(pairs) == 1
    # severity = 1.0 - min(0.8, 0.3) = 0.7
    assert abs(pairs[0].severity - 0.7) < 1e-9


# ------------------------------------------------------------------
# Resolution
# ------------------------------------------------------------------


async def test_resolve_keep_both(store, resolver):
    a = _node("View A", trust=0.6, useful=0.7)
    b = _node("View B", trust=0.5, useful=0.6)
    await store.add_node(a)
    await store.add_node(b)

    edge = MemoryEdge(source_id=a.id, target_id=b.id, edge_type=EdgeType.CONTRADICTS)
    await store.add_edge(edge)

    pairs = await resolver.detect_contradictions(a.id)
    await resolver.resolve(pairs[0], "keep_both")

    assert pairs[0].status == "resolved"

    # Both nodes unchanged
    node_a = await store.get_node(a.id)
    node_b = await store.get_node(b.id)
    assert node_a.status == NodeStatus.ACTIVE
    assert node_b.status == NodeStatus.ACTIVE


async def test_resolve_supersede(store, resolver):
    # a has higher trust_useful (0.8*0.7=0.56) vs b (0.3*0.4=0.12)
    a = _node("Winner claim", trust=0.8, useful=0.7)
    b = _node("Loser claim", trust=0.3, useful=0.4)
    await store.add_node(a)
    await store.add_node(b)

    edge = MemoryEdge(source_id=a.id, target_id=b.id, edge_type=EdgeType.CONTRADICTS)
    await store.add_edge(edge)

    pairs = await resolver.detect_contradictions(a.id)
    await resolver.resolve(pairs[0], "supersede")

    assert pairs[0].status == "resolved"

    # SUPERSEDES edge created from winner to loser
    sup_edges = await store.get_edges_from(a.id, [EdgeType.SUPERSEDES])
    assert len(sup_edges) == 1
    assert sup_edges[0].target_id == b.id

    # Loser's usefulness reduced by 50%
    loser = await store.get_node(b.id)
    assert abs(loser.base_usefulness - 0.2) < 1e-9  # 0.4 * 0.5


async def test_resolve_defer(store, resolver):
    a = _node("Pending A")
    b = _node("Pending B")
    await store.add_node(a)
    await store.add_node(b)

    edge = MemoryEdge(source_id=a.id, target_id=b.id, edge_type=EdgeType.CONTRADICTS)
    await store.add_edge(edge)

    pairs = await resolver.detect_contradictions(a.id)
    await resolver.resolve(pairs[0], "defer")

    assert pairs[0].status == "deferred"


# ------------------------------------------------------------------
# Unresolved query
# ------------------------------------------------------------------


async def test_get_unresolved_active_only(store, resolver):
    a = _node("Active claim")
    b = _node("Active claim 2")
    c = _node("Archived claim")
    await store.add_node(a)
    await store.add_node(b)
    await store.add_node(c)

    # a-b: both active
    e1 = MemoryEdge(source_id=a.id, target_id=b.id, edge_type=EdgeType.CONTRADICTS)
    await store.add_edge(e1)

    # a-c: c will be archived
    e2 = MemoryEdge(source_id=a.id, target_id=c.id, edge_type=EdgeType.CONTRADICTS)
    await store.add_edge(e2)

    await store.update_node(c.id, NodeUpdate(status=NodeStatus.ARCHIVED))

    unresolved = await resolver.get_unresolved()
    assert len(unresolved) == 1
    assert unresolved[0].node_a_id == a.id
    assert unresolved[0].node_b_id == b.id


# ------------------------------------------------------------------
# Chain surfacing
# ------------------------------------------------------------------


async def test_surface_chains_finds_connected(store, resolver):
    """A contradicts B, B contradicts C → one chain of 2 pairs."""
    a = _node("Claim A")
    b = _node("Claim B")
    c = _node("Claim C")
    await store.add_node(a)
    await store.add_node(b)
    await store.add_node(c)

    e1 = MemoryEdge(source_id=a.id, target_id=b.id, edge_type=EdgeType.CONTRADICTS)
    e2 = MemoryEdge(source_id=b.id, target_id=c.id, edge_type=EdgeType.CONTRADICTS)
    await store.add_edge(e1)
    await store.add_edge(e2)

    chains = await resolver.surface_chains()
    assert len(chains) == 1
    assert len(chains[0]) == 2


async def test_surface_chains_separate_components(store, resolver):
    """A-B and C-D are disjoint → two separate chains."""
    a = _node("Claim A")
    b = _node("Claim B")
    c = _node("Claim C")
    d = _node("Claim D")
    await store.add_node(a)
    await store.add_node(b)
    await store.add_node(c)
    await store.add_node(d)

    e1 = MemoryEdge(source_id=a.id, target_id=b.id, edge_type=EdgeType.CONTRADICTS)
    e2 = MemoryEdge(source_id=c.id, target_id=d.id, edge_type=EdgeType.CONTRADICTS)
    await store.add_edge(e1)
    await store.add_edge(e2)

    chains = await resolver.surface_chains()
    assert len(chains) == 2
    for chain in chains:
        assert len(chain) == 1


# ------------------------------------------------------------------
# Audit logging
# ------------------------------------------------------------------


async def test_resolve_audit_logged(store, resolver, audit):
    a = _node("Audited A")
    b = _node("Audited B")
    await store.add_node(a)
    await store.add_node(b)

    edge = MemoryEdge(source_id=a.id, target_id=b.id, edge_type=EdgeType.CONTRADICTS)
    await store.add_edge(edge)

    pairs = await resolver.detect_contradictions(a.id)
    await resolver.resolve(pairs[0], "keep_both")

    entries = audit.read()
    # Find the contradiction_resolved entry
    resolved_entries = [e for e in entries if e["mutation_type"] == "contradiction_resolved"]
    assert len(resolved_entries) >= 1
    assert resolved_entries[0]["evidence"]["strategy"] == "keep_both"
