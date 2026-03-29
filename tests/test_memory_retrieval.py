"""Tests for MemoryRetriever."""
from __future__ import annotations

import pytest

from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryEdge,
    NodeFilter,
    NodeStatus,
    NodeType,
)
from agentgolem.memory.retrieval import MemoryRetriever
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore


@pytest.fixture
async def setup(tmp_path):
    db = await init_db(tmp_path / "test.db")
    store = SQLiteMemoryStore(db)
    retriever = MemoryRetriever(store)
    yield store, retriever
    await close_db(db)


def _node(
    text: str,
    *,
    base_usefulness: float = 0.5,
    trustworthiness: float = 0.5,
    node_type: NodeType = NodeType.FACT,
    node_id: str | None = None,
) -> ConceptualNode:
    kwargs: dict = dict(text=text, type=node_type, base_usefulness=base_usefulness, trustworthiness=trustworthiness)
    if node_id is not None:
        kwargs["id"] = node_id
    return ConceptualNode(**kwargs)


# ------------------------------------------------------------------
# retrieve()
# ------------------------------------------------------------------


async def test_retrieve_by_keyword(setup):
    store, retriever = setup
    await store.add_node(_node("Python is dynamically typed"))
    await store.add_node(_node("Rust is statically typed"))
    await store.add_node(_node("Java runs on JVM"))

    results = await retriever.retrieve("Python")
    assert len(results) == 1
    assert results[0].text == "Python is dynamically typed"


async def test_retrieve_ranked_by_trust_useful(setup):
    store, retriever = setup
    # trust_useful = base_usefulness * trustworthiness
    # low: 0.3 * 0.3 = 0.09
    await store.add_node(_node("Python basics intro", base_usefulness=0.3, trustworthiness=0.3))
    # high: 0.9 * 0.9 = 0.81
    await store.add_node(_node("Python advanced guide", base_usefulness=0.9, trustworthiness=0.9))
    # mid: 0.5 * 0.5 = 0.25
    await store.add_node(_node("Python standard library", base_usefulness=0.5, trustworthiness=0.5))

    results = await retriever.retrieve("Python")
    assert len(results) == 3
    # Sorted by trust_useful descending
    assert results[0].text == "Python advanced guide"
    assert results[1].text == "Python standard library"
    assert results[2].text == "Python basics intro"


async def test_retrieve_empty_query(setup):
    store, retriever = setup
    await store.add_node(_node("Python is great"))
    await store.add_node(_node("Rust is fast"))

    results = await retriever.retrieve("zzzznonexistent")
    assert results == []


async def test_retrieve_limits_results(setup):
    store, retriever = setup
    for i in range(15):
        await store.add_node(_node(f"memory item number {i}"))

    results = await retriever.retrieve("memory", top_k=5)
    assert len(results) == 5


# ------------------------------------------------------------------
# retrieve_neighborhood()
# ------------------------------------------------------------------


async def test_retrieve_neighborhood_depth_1(setup):
    store, retriever = setup
    a = _node("Node A", node_id="a")
    b = _node("Node B", node_id="b")
    c = _node("Node C", node_id="c")
    await store.add_node(a)
    await store.add_node(b)
    await store.add_node(c)

    await store.add_edge(MemoryEdge(source_id="a", target_id="b", edge_type=EdgeType.RELATED_TO))
    await store.add_edge(MemoryEdge(source_id="b", target_id="c", edge_type=EdgeType.RELATED_TO))

    result = await retriever.retrieve_neighborhood("a", depth=1)
    node_ids = {node.id for node, _ in result}
    assert "a" in node_ids
    assert "b" in node_ids
    assert "c" not in node_ids


async def test_retrieve_neighborhood_depth_2(setup):
    store, retriever = setup
    a = _node("Node A", node_id="a")
    b = _node("Node B", node_id="b")
    c = _node("Node C", node_id="c")
    await store.add_node(a)
    await store.add_node(b)
    await store.add_node(c)

    await store.add_edge(MemoryEdge(source_id="a", target_id="b", edge_type=EdgeType.RELATED_TO))
    await store.add_edge(MemoryEdge(source_id="b", target_id="c", edge_type=EdgeType.RELATED_TO))

    result = await retriever.retrieve_neighborhood("a", depth=2)
    node_ids = {node.id for node, _ in result}
    assert node_ids == {"a", "b", "c"}


# ------------------------------------------------------------------
# retrieve_contradictions()
# ------------------------------------------------------------------


async def test_retrieve_contradictions(setup):
    store, retriever = setup
    n1 = _node("Earth is flat", node_id="flat")
    n2 = _node("Earth is round", node_id="round")
    await store.add_node(n1)
    await store.add_node(n2)

    await store.add_edge(
        MemoryEdge(source_id="flat", target_id="round", edge_type=EdgeType.CONTRADICTS)
    )

    # From flat's perspective
    contradictions = await retriever.retrieve_contradictions("flat")
    assert len(contradictions) == 1
    node, edge = contradictions[0]
    assert node.id == "round"
    assert edge.edge_type == EdgeType.CONTRADICTS

    # From round's perspective (incoming edge)
    contradictions2 = await retriever.retrieve_contradictions("round")
    assert len(contradictions2) == 1
    node2, edge2 = contradictions2[0]
    assert node2.id == "flat"


# ------------------------------------------------------------------
# retrieve_supersession_chain()
# ------------------------------------------------------------------


async def test_retrieve_supersession_chain(setup):
    store, retriever = setup
    a = _node("Version 3", node_id="a")
    b = _node("Version 2", node_id="b")
    c = _node("Version 1", node_id="c")
    await store.add_node(a)
    await store.add_node(b)
    await store.add_node(c)

    # A supersedes B, B supersedes C
    await store.add_edge(MemoryEdge(source_id="a", target_id="b", edge_type=EdgeType.SUPERSEDES))
    await store.add_edge(MemoryEdge(source_id="b", target_id="c", edge_type=EdgeType.SUPERSEDES))

    chain = await retriever.retrieve_supersession_chain("a")
    assert len(chain) == 2
    assert chain[0].id == "b"
    assert chain[1].id == "c"
