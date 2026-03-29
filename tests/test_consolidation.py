"""Tests for the sleep-mode consolidation engine."""
from __future__ import annotations

import pytest

from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryEdge,
    NodeType,
)
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.sleep.consolidation import (
    AbstractionProposal,
    ConsolidationEngine,
    ContradictionChain,
    MergeProposal,
)
from agentgolem.sleep.walker import WalkResult


# ------------------------------------------------------------------ helpers


def _node(text: str, nid: str, ntype: NodeType = NodeType.FACT) -> ConceptualNode:
    return ConceptualNode(text=text, type=ntype, id=nid)


def _edge(
    src: str, tgt: str, etype: EdgeType, eid: str, weight: float = 1.0
) -> MemoryEdge:
    return MemoryEdge(source_id=src, target_id=tgt, edge_type=etype, id=eid, weight=weight)


def _walk(visited_ids: list[str], edge_activations: dict[str, float] | None = None) -> WalkResult:
    return WalkResult(
        seed_id=visited_ids[0] if visited_ids else "",
        visited_node_ids=visited_ids,
        edge_activations=edge_activations or {},
        proposed_actions=[],
        steps_taken=len(visited_ids),
        time_ms=100.0,
    )


# ------------------------------------------------------------------ fixtures


@pytest.fixture
async def store(tmp_path):
    audit = AuditLogger(tmp_path)
    db = await init_db(tmp_path / "test.db")
    s = SQLiteMemoryStore(db, audit)
    yield s
    await close_db(db)


@pytest.fixture
def engine(store, tmp_path):
    audit = AuditLogger(tmp_path)
    return ConsolidationEngine(store, audit, state_path=tmp_path / "state")


# ------------------------------------------------------------------ merge tests


async def test_propose_merges_from_merge_candidate_edges(store, engine):
    """Walk with MERGE_CANDIDATE edges yields MergeProposal."""
    n1 = _node("Python is dynamic", "n1")
    n2 = _node("Python uses dynamic typing", "n2")
    await store.add_node(n1)
    await store.add_node(n2)

    edge = _edge("n1", "n2", EdgeType.MERGE_CANDIDATE, "e1")
    await store.add_edge(edge)

    walk = _walk(["n1", "n2"], edge_activations={"e1": 0.9})
    proposals = await engine.propose_merges(walk)

    assert len(proposals) == 1
    assert isinstance(proposals[0], MergeProposal)
    assert set(proposals[0].node_ids) == {"n1", "n2"}
    assert "Python is dynamic" in proposals[0].proposed_text
    assert "Python uses dynamic typing" in proposals[0].proposed_text


async def test_propose_merges_same_as_edge(store, engine):
    """SAME_AS edges also produce merge proposals."""
    n1 = _node("ML is machine learning", "n1")
    n2 = _node("Machine learning abbreviated ML", "n2")
    await store.add_node(n1)
    await store.add_node(n2)

    edge = _edge("n1", "n2", EdgeType.SAME_AS, "e1")
    await store.add_edge(edge)

    walk = _walk(["n1", "n2"], edge_activations={"e1": 0.8})
    proposals = await engine.propose_merges(walk)

    assert len(proposals) == 1
    assert "same_as" in proposals[0].reason


async def test_propose_merges_empty_when_no_candidates(store, engine):
    """No merge candidate edges → empty list."""
    n1 = _node("Fact A", "n1")
    n2 = _node("Fact B", "n2")
    await store.add_node(n1)
    await store.add_node(n2)

    edge = _edge("n1", "n2", EdgeType.RELATED_TO, "e1")
    await store.add_edge(edge)

    walk = _walk(["n1", "n2"], edge_activations={"e1": 0.5})
    proposals = await engine.propose_merges(walk)

    assert proposals == []


# ------------------------------------------------------------------ abstraction tests


async def test_propose_abstractions_from_related_cluster(store, engine):
    """3+ RELATED_TO nodes → AbstractionProposal."""
    nodes = [
        _node("Dog is a pet", "n1"),
        _node("Cat is a pet", "n2"),
        _node("Fish is a pet", "n3"),
    ]
    for n in nodes:
        await store.add_node(n)

    # Connect them: n1->n2, n2->n3
    await store.add_edge(_edge("n1", "n2", EdgeType.RELATED_TO, "e1"))
    await store.add_edge(_edge("n2", "n3", EdgeType.RELATED_TO, "e2"))

    walk = _walk(["n1", "n2", "n3"])
    proposals = await engine.propose_abstractions(walk)

    assert len(proposals) >= 1
    p = proposals[0]
    assert isinstance(p, AbstractionProposal)
    assert len(p.source_node_ids) == 3
    assert p.proposed_text.startswith("Abstraction of:")
    assert p.proposed_type == NodeType.ASSOCIATION.value


async def test_propose_abstractions_fewer_than_three(store, engine):
    """<3 related nodes → no proposals."""
    n1 = _node("Alpha", "n1")
    n2 = _node("Beta", "n2")
    await store.add_node(n1)
    await store.add_node(n2)

    await store.add_edge(_edge("n1", "n2", EdgeType.RELATED_TO, "e1"))

    walk = _walk(["n1", "n2"])
    proposals = await engine.propose_abstractions(walk)

    assert proposals == []


# ------------------------------------------------------------------ contradiction tests


async def test_surface_contradictions_finds_chains(store, engine):
    """CONTRADICTS edges in walk → ContradictionChain."""
    n1 = _node("Earth is flat", "n1")
    n2 = _node("Earth is round", "n2")
    await store.add_node(n1)
    await store.add_node(n2)

    await store.add_edge(_edge("n1", "n2", EdgeType.CONTRADICTS, "e1"))

    walk = _walk(["n1", "n2"])
    chains = await engine.surface_contradictions(walk)

    assert len(chains) == 1
    assert isinstance(chains[0], ContradictionChain)
    assert len(chains[0].pairs) == 1
    pair = chains[0].pairs[0]
    assert set(pair) == {"n1", "n2"}
    assert chains[0].severity > 0


async def test_surface_contradictions_none(store, engine):
    """No contradictions → empty list."""
    n1 = _node("Grass is green", "n1")
    n2 = _node("Sky is blue", "n2")
    await store.add_node(n1)
    await store.add_node(n2)

    await store.add_edge(_edge("n1", "n2", EdgeType.RELATED_TO, "e1"))

    walk = _walk(["n1", "n2"])
    chains = await engine.surface_contradictions(walk)

    assert chains == []


# ------------------------------------------------------------------ queue tests


async def test_queue_for_heartbeat_persists(engine):
    """Items saved to JSON file."""
    items = [
        MergeProposal(node_ids=["a", "b"], proposed_text="merged", reason="test", confidence=0.9),
    ]
    engine.queue_for_heartbeat(items)

    queue = engine.get_queue()
    assert len(queue) == 1
    assert queue[0]["node_ids"] == ["a", "b"]
    assert queue[0]["_type"] == "MergeProposal"


async def test_get_queue_reads_items(engine):
    """Items can be round-tripped through the queue."""
    items = [
        MergeProposal(node_ids=["x"], proposed_text="m", reason="r", confidence=0.5),
        ContradictionChain(pairs=[("a", "b")], severity=0.8),
    ]
    engine.queue_for_heartbeat(items)

    queue = engine.get_queue()
    assert len(queue) == 2
    assert queue[0]["_type"] == "MergeProposal"
    assert queue[1]["_type"] == "ContradictionChain"


async def test_queue_appends_not_overwrites(engine):
    """Successive calls append to the queue."""
    engine.queue_for_heartbeat(
        [MergeProposal(node_ids=["a"], proposed_text="1", reason="r")]
    )
    engine.queue_for_heartbeat(
        [MergeProposal(node_ids=["b"], proposed_text="2", reason="r")]
    )

    queue = engine.get_queue()
    assert len(queue) == 2


async def test_clear_queue(engine):
    """Queue is cleared."""
    engine.queue_for_heartbeat(
        [MergeProposal(node_ids=["a"], proposed_text="x", reason="r")]
    )
    assert len(engine.get_queue()) == 1

    engine.clear_queue()
    assert engine.get_queue() == []


# ------------------------------------------------------------------ safety


async def test_nothing_auto_applied(store, engine):
    """Proposals are returned but NO changes to the store."""
    n1 = _node("A", "n1")
    n2 = _node("B", "n2")
    n3 = _node("C", "n3")
    for n in [n1, n2, n3]:
        await store.add_node(n)

    await store.add_edge(_edge("n1", "n2", EdgeType.MERGE_CANDIDATE, "e1"))
    await store.add_edge(_edge("n1", "n3", EdgeType.RELATED_TO, "e2"))
    await store.add_edge(_edge("n2", "n3", EdgeType.RELATED_TO, "e3"))
    await store.add_edge(_edge("n1", "n2", EdgeType.CONTRADICTS, "e4"))

    stats_before = await store.get_statistics()

    walk = _walk(["n1", "n2", "n3"], edge_activations={"e1": 1.0})

    merges = await engine.propose_merges(walk)
    abstractions = await engine.propose_abstractions(walk)
    contradictions = await engine.surface_contradictions(walk)

    # Proposals were generated
    assert len(merges) >= 1
    assert len(contradictions) >= 1

    # Store unchanged (node/edge counts identical — accounting for access bumps only)
    stats_after = await store.get_statistics()
    assert stats_after["total_nodes"] == stats_before["total_nodes"]
    assert stats_after["total_edges"] == stats_before["total_edges"]
