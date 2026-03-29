"""Tests for memory mutation operations."""
from __future__ import annotations

import json

import pytest

from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryCluster,
    MemoryEdge,
    NodeStatus,
    NodeType,
    Source,
    SourceKind,
)
from agentgolem.memory.mutations import MemoryMutator
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore


@pytest.fixture
async def setup(tmp_path):
    db = await init_db(tmp_path / "test.db")
    store = SQLiteMemoryStore(db)
    audit = AuditLogger(tmp_path)
    mutator = MemoryMutator(store=store, audit_logger=audit)
    yield store, mutator, audit
    await close_db(db)


def _node(text: str, ntype: NodeType = NodeType.FACT, **kw) -> ConceptualNode:
    return ConceptualNode(text=text, type=ntype, **kw)


def _evidence() -> Source:
    return Source(kind=SourceKind.HUMAN, origin="test-user")


# ------------------------------------------------------------------
# merge_nodes
# ------------------------------------------------------------------


async def test_merge_creates_new_node(setup):
    store, mutator, _ = setup
    n1 = _node("Earth orbits the Sun")
    n2 = _node("Sun is orbited by Earth")
    await store.add_node(n1)
    await store.add_node(n2)

    merged = await mutator.merge_nodes(
        [n1.id, n2.id], "Earth orbits the Sun (merged)", _evidence()
    )

    fetched = await store.get_node(merged.id)
    assert fetched is not None
    assert fetched.text == "Earth orbits the Sun (merged)"


async def test_merge_archives_originals(setup):
    store, mutator, _ = setup
    n1 = _node("A")
    n2 = _node("B")
    await store.add_node(n1)
    await store.add_node(n2)

    await mutator.merge_nodes([n1.id, n2.id], "AB merged", _evidence())

    # get_node bumps access, so read raw via query to avoid side effects
    for nid in [n1.id, n2.id]:
        fetched = await store.get_node(nid)
        assert fetched is not None
        assert fetched.status == NodeStatus.ARCHIVED


async def test_merge_creates_same_as_edges(setup):
    store, mutator, _ = setup
    n1 = _node("X")
    n2 = _node("Y")
    await store.add_node(n1)
    await store.add_node(n2)

    merged = await mutator.merge_nodes([n1.id, n2.id], "XY", _evidence())

    edges = await store.get_edges_from(merged.id, edge_types=[EdgeType.SAME_AS])
    target_ids = {e.target_id for e in edges}
    assert n1.id in target_ids
    assert n2.id in target_ids
    assert len(edges) == 2


async def test_merge_combines_scores(setup):
    store, mutator, _ = setup
    n1 = _node("A", trustworthiness=0.8, base_usefulness=0.6)
    n2 = _node("B", trustworthiness=0.4, base_usefulness=0.9)
    await store.add_node(n1)
    await store.add_node(n2)

    merged = await mutator.merge_nodes([n1.id, n2.id], "AB", _evidence())

    fetched = await store.get_node(merged.id)
    assert fetched is not None
    assert fetched.trustworthiness == pytest.approx(0.6)  # avg(0.8, 0.4)
    assert fetched.base_usefulness == pytest.approx(0.9)  # max(0.6, 0.9)


async def test_merge_audit_logged(setup):
    store, mutator, audit = setup
    n1 = _node("P")
    n2 = _node("Q")
    await store.add_node(n1)
    await store.add_node(n2)

    await mutator.merge_nodes([n1.id, n2.id], "PQ", _evidence())

    entries = audit.read(limit=10)
    merge_entries = [e for e in entries if e["mutation_type"] == "merge_nodes"]
    assert len(merge_entries) == 1
    assert set(merge_entries[0]["evidence"]["merged_from"]) == {n1.id, n2.id}


# ------------------------------------------------------------------
# supersede
# ------------------------------------------------------------------


async def test_supersede_creates_edge(setup):
    store, mutator, _ = setup
    old = _node("old info")
    new = _node("new info")
    await store.add_node(old)
    await store.add_node(new)

    edge = await mutator.supersede(old.id, new.id, _evidence())

    assert edge.edge_type == EdgeType.SUPERSEDES
    assert edge.source_id == new.id
    assert edge.target_id == old.id

    edges = await store.get_edges_from(new.id, edge_types=[EdgeType.SUPERSEDES])
    assert len(edges) == 1


async def test_supersede_reduces_usefulness(setup):
    store, mutator, _ = setup
    old = _node("outdated", base_usefulness=0.8)
    new = _node("current")
    await store.add_node(old)
    await store.add_node(new)

    await mutator.supersede(old.id, new.id, _evidence())

    fetched = await store.get_node(old.id)
    assert fetched is not None
    assert fetched.base_usefulness == pytest.approx(0.4)  # 0.8 * 0.5


# ------------------------------------------------------------------
# mark_contradiction
# ------------------------------------------------------------------


async def test_mark_contradiction_creates_edge(setup):
    store, mutator, _ = setup
    a = _node("Sky is blue")
    b = _node("Sky is green")
    await store.add_node(a)
    await store.add_node(b)

    edge = await mutator.mark_contradiction(a.id, b.id, _evidence())

    assert edge.edge_type == EdgeType.CONTRADICTS
    assert edge.source_id == a.id
    assert edge.target_id == b.id

    edges = await store.get_edges_from(a.id, edge_types=[EdgeType.CONTRADICTS])
    assert len(edges) == 1


async def test_mark_contradiction_audit(setup):
    store, mutator, audit = setup
    a = _node("Cats are mammals")
    b = _node("Cats are reptiles")
    await store.add_node(a)
    await store.add_node(b)

    await mutator.mark_contradiction(a.id, b.id, _evidence())

    entries = audit.read(limit=10)
    contra = [e for e in entries if e["mutation_type"] == "mark_contradiction"]
    assert len(contra) == 1
    assert contra[0]["evidence"]["node_a"] == a.id
    assert contra[0]["evidence"]["node_b"] == b.id


# ------------------------------------------------------------------
# update_cluster_membership
# ------------------------------------------------------------------


async def test_update_cluster_membership(setup):
    store, mutator, _ = setup
    n1 = _node("C1")
    n2 = _node("C2")
    n3 = _node("C3")
    await store.add_node(n1)
    await store.add_node(n2)
    await store.add_node(n3)

    cluster = MemoryCluster(label="test-cluster")
    await store.add_cluster(cluster)

    # Add two members
    await mutator.update_cluster_membership(cluster.id, add_ids=[n1.id, n2.id])
    members = await store.get_cluster_nodes(cluster.id)
    assert len(members) == 2

    # Add third, remove first
    await mutator.update_cluster_membership(
        cluster.id, add_ids=[n3.id], remove_ids=[n1.id]
    )
    members = await store.get_cluster_nodes(cluster.id)
    member_ids = {m.id for m in members}
    assert n1.id not in member_ids
    assert n2.id in member_ids
    assert n3.id in member_ids


# ------------------------------------------------------------------
# Error cases
# ------------------------------------------------------------------


async def test_merge_empty_raises(setup):
    _, mutator, _ = setup

    with pytest.raises(ValueError, match="No valid nodes to merge"):
        await mutator.merge_nodes(
            ["nonexistent-1", "nonexistent-2"], "nope", _evidence()
        )
