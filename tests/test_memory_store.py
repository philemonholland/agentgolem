"""Comprehensive tests for SQLiteMemoryStore."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryCluster,
    MemoryEdge,
    NodeFilter,
    NodeStatus,
    NodeType,
    NodeUpdate,
    Source,
    SourceKind,
)
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore


@pytest.fixture
async def store(tmp_path):
    db = await init_db(tmp_path / "test.db")
    s = SQLiteMemoryStore(db)
    yield s
    await close_db(db)


def _make_node(
    text: str = "Python is dynamically typed",
    node_type: NodeType = NodeType.FACT,
    **kwargs,
) -> ConceptualNode:
    return ConceptualNode(text=text, type=node_type, **kwargs)


# ------------------------------------------------------------------
# Node tests
# ------------------------------------------------------------------


async def test_add_and_get_node(store: SQLiteMemoryStore):
    node = _make_node(
        trustworthiness=0.8,
        base_usefulness=0.7,
        canonical=True,
        search_text="python dynamically typed",
        salience=0.85,
    )
    returned_id = await store.add_node(node)
    assert returned_id == node.id

    fetched = await store.get_node(node.id)
    assert fetched is not None
    assert fetched.id == node.id
    assert fetched.text == node.text
    assert fetched.search_text == "python dynamically typed"
    assert fetched.type == NodeType.FACT
    assert fetched.trustworthiness == 0.8
    assert fetched.base_usefulness == 0.7
    assert fetched.salience == 0.85
    assert fetched.canonical is True
    assert fetched.status == NodeStatus.ACTIVE
    assert fetched.emotion_label == "neutral"


async def test_get_node_bumps_access(store: SQLiteMemoryStore):
    node = _make_node()
    await store.add_node(node)

    before = datetime.now(timezone.utc)
    fetched1 = await store.get_node(node.id)
    assert fetched1 is not None
    assert fetched1.access_count == 1
    assert fetched1.last_accessed >= before

    fetched2 = await store.get_node(node.id)
    assert fetched2 is not None
    assert fetched2.access_count == 2


async def test_get_nonexistent_node(store: SQLiteMemoryStore):
    assert await store.get_node("nonexistent-id") is None


async def test_update_node(store: SQLiteMemoryStore):
    node = _make_node()
    await store.add_node(node)

    await store.update_node(
        node.id,
        NodeUpdate(
            text="Updated text",
            search_text="updated text search",
            trustworthiness=0.9,
            salience=0.75,
        ),
    )
    fetched = await store.get_node(node.id)
    assert fetched is not None
    assert fetched.text == "Updated text"
    assert fetched.search_text == "updated text search"
    assert fetched.trustworthiness == 0.9
    assert fetched.salience == 0.75
    # Unchanged fields stay the same
    assert fetched.base_usefulness == 0.5
    assert fetched.emotion_label == "neutral"


# ------------------------------------------------------------------
# Query tests
# ------------------------------------------------------------------


async def test_query_nodes_by_type(store: SQLiteMemoryStore):
    await store.add_node(_make_node("fact1", NodeType.FACT))
    await store.add_node(_make_node("goal1", NodeType.GOAL))
    await store.add_node(_make_node("fact2", NodeType.FACT))

    results = await store.query_nodes(NodeFilter(type=NodeType.FACT))
    assert len(results) == 2
    assert all(n.type == NodeType.FACT for n in results)


async def test_query_nodes_by_status(store: SQLiteMemoryStore):
    n1 = _make_node("active node")
    n2 = _make_node("archived node")
    await store.add_node(n1)
    await store.add_node(n2)
    await store.update_node(n2.id, NodeUpdate(status=NodeStatus.ARCHIVED))

    results = await store.query_nodes(NodeFilter(status=NodeStatus.ARCHIVED))
    assert len(results) == 1
    assert results[0].id == n2.id


async def test_query_nodes_by_trust_range(store: SQLiteMemoryStore):
    await store.add_node(_make_node("low", trustworthiness=0.1))
    await store.add_node(_make_node("mid", trustworthiness=0.5))
    await store.add_node(_make_node("high", trustworthiness=0.9))

    results = await store.query_nodes(NodeFilter(trust_min=0.4, trust_max=0.6))
    assert len(results) == 1
    assert results[0].trustworthiness == 0.5


async def test_query_nodes_text_contains(store: SQLiteMemoryStore):
    await store.add_node(_make_node("Python is great"))
    await store.add_node(_make_node("Rust is fast"))
    await store.add_node(_make_node("Python rocks"))

    results = await store.query_nodes(NodeFilter(text_contains="Python"))
    assert len(results) == 2


async def test_query_nodes_text_contains_matches_search_text(store: SQLiteMemoryStore):
    await store.add_node(
        _make_node(
            "Long-form memory about runtime tuning",
            search_text="runtime tuning optimization",
        )
    )

    results = await store.query_nodes(NodeFilter(text_contains="optimization"))
    assert len(results) == 1


async def test_search_nodes_by_keywords_matches_search_text(store: SQLiteMemoryStore):
    await store.add_node(
        _make_node(
            "Long-form memory about runtime tuning",
            search_text="runtime tuning optimization",
        )
    )

    results = await store.search_nodes_by_keywords(["optimization"], limit=5)
    assert len(results) == 1


async def test_query_nodes_limit_offset(store: SQLiteMemoryStore):
    for i in range(5):
        await store.add_node(_make_node(f"node {i}"))

    page1 = await store.query_nodes(NodeFilter(limit=2, offset=0))
    page2 = await store.query_nodes(NodeFilter(limit=2, offset=2))
    page3 = await store.query_nodes(NodeFilter(limit=2, offset=4))

    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1

    all_ids = {n.id for n in page1} | {n.id for n in page2} | {n.id for n in page3}
    assert len(all_ids) == 5


# ------------------------------------------------------------------
# Edge tests
# ------------------------------------------------------------------


async def test_add_and_get_edge(store: SQLiteMemoryStore):
    n1 = _make_node("A")
    n2 = _make_node("B")
    await store.add_node(n1)
    await store.add_node(n2)

    edge = MemoryEdge(source_id=n1.id, target_id=n2.id, edge_type=EdgeType.RELATED_TO)
    returned_id = await store.add_edge(edge)
    assert returned_id == edge.id

    edges = await store.get_edges_from(n1.id)
    assert len(edges) == 1
    assert edges[0].source_id == n1.id
    assert edges[0].target_id == n2.id
    assert edges[0].edge_type == EdgeType.RELATED_TO


async def test_get_neighbors(store: SQLiteMemoryStore):
    n1 = _make_node("A")
    n2 = _make_node("B")
    n3 = _make_node("C")
    await store.add_node(n1)
    await store.add_node(n2)
    await store.add_node(n3)

    await store.add_edge(MemoryEdge(source_id=n1.id, target_id=n2.id, edge_type=EdgeType.SUPPORTS))
    await store.add_edge(MemoryEdge(source_id=n1.id, target_id=n3.id, edge_type=EdgeType.RELATED_TO))

    neighbors = await store.get_neighbors(n1.id)
    assert len(neighbors) == 2
    neighbor_ids = {node.id for _, node in neighbors}
    assert n2.id in neighbor_ids
    assert n3.id in neighbor_ids


async def test_get_neighbors_filtered_by_type(store: SQLiteMemoryStore):
    n1 = _make_node("A")
    n2 = _make_node("B")
    n3 = _make_node("C")
    await store.add_node(n1)
    await store.add_node(n2)
    await store.add_node(n3)

    await store.add_edge(MemoryEdge(source_id=n1.id, target_id=n2.id, edge_type=EdgeType.SUPPORTS))
    await store.add_edge(MemoryEdge(source_id=n1.id, target_id=n3.id, edge_type=EdgeType.RELATED_TO))

    neighbors = await store.get_neighbors(n1.id, edge_types=[EdgeType.SUPPORTS])
    assert len(neighbors) == 1
    assert neighbors[0][1].id == n2.id


# ------------------------------------------------------------------
# Source tests
# ------------------------------------------------------------------


async def test_add_source_and_link(store: SQLiteMemoryStore):
    node = _make_node()
    await store.add_node(node)

    src = Source(kind=SourceKind.WEB, origin="https://example.com", reliability=0.8)
    await store.add_source(src)
    await store.link_node_source(node.id, src.id)

    sources = await store.get_node_sources(node.id)
    assert len(sources) == 1
    assert sources[0].id == src.id
    assert sources[0].kind == SourceKind.WEB
    assert sources[0].origin == "https://example.com"


# ------------------------------------------------------------------
# Cluster tests
# ------------------------------------------------------------------


async def test_add_and_get_cluster(store: SQLiteMemoryStore):
    cluster = MemoryCluster(label="Test Cluster", cluster_type="topic")
    cid = await store.add_cluster(cluster)
    assert cid == cluster.id

    fetched = await store.get_cluster(cluster.id)
    assert fetched is not None
    assert fetched.label == "Test Cluster"
    assert fetched.cluster_type == "topic"
    assert fetched.status == NodeStatus.ACTIVE


async def test_cluster_membership(store: SQLiteMemoryStore):
    n1 = _make_node("A")
    n2 = _make_node("B")
    await store.add_node(n1)
    await store.add_node(n2)

    cluster = MemoryCluster(label="My Cluster")
    await store.add_cluster(cluster)

    await store.add_cluster_member(cluster.id, n1.id)
    await store.add_cluster_member(cluster.id, n2.id)

    nodes = await store.get_cluster_nodes(cluster.id)
    assert len(nodes) == 2
    node_ids = {n.id for n in nodes}
    assert n1.id in node_ids
    assert n2.id in node_ids

    await store.remove_cluster_member(cluster.id, n1.id)
    nodes = await store.get_cluster_nodes(cluster.id)
    assert len(nodes) == 1
    assert nodes[0].id == n2.id


# ------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------


async def test_get_statistics(store: SQLiteMemoryStore):
    await store.add_node(_make_node("fact1", NodeType.FACT))
    await store.add_node(_make_node("goal1", NodeType.GOAL))
    n3 = _make_node("archived", NodeType.FACT)
    await store.add_node(n3)
    await store.update_node(n3.id, NodeUpdate(status=NodeStatus.ARCHIVED))

    n1_node = _make_node("src", NodeType.EVENT)
    n2_node = _make_node("tgt", NodeType.EVENT)
    await store.add_node(n1_node)
    await store.add_node(n2_node)
    await store.add_edge(
        MemoryEdge(source_id=n1_node.id, target_id=n2_node.id, edge_type=EdgeType.RELATED_TO)
    )

    stats = await store.get_statistics()
    assert stats["total_nodes"] == 5
    assert stats["total_edges"] == 1
    assert stats["nodes_by_type"]["fact"] == 2
    assert stats["nodes_by_type"]["goal"] == 1
    assert stats["nodes_by_type"]["event"] == 2
    assert stats["nodes_by_status"]["active"] == 4
    assert stats["nodes_by_status"]["archived"] == 1
