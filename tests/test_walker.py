"""Tests for the bounded graph walker (sleep subsystem)."""
from __future__ import annotations

import pytest

from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryEdge,
    NodeType,
)
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.runtime.state import RuntimeState
from agentgolem.sleep.walker import GraphWalker, WalkResult


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path):
    db = await init_db(tmp_path / "test.db")
    s = SQLiteMemoryStore(db)
    yield s
    await close_db(db)


@pytest.fixture
def runtime_state(tmp_path):
    return RuntimeState(tmp_path)


@pytest.fixture
def walker(store, runtime_state):
    return GraphWalker(store, runtime_state)


async def create_test_graph(store: SQLiteMemoryStore):
    """Create: A --related_to--> B --related_to--> C"""
    node_a = ConceptualNode(
        text="concept alpha", type=NodeType.FACT,
        centrality=0.8, base_usefulness=0.7, trustworthiness=0.8,
    )
    node_b = ConceptualNode(
        text="concept beta", type=NodeType.FACT,
        centrality=0.5, base_usefulness=0.6, trustworthiness=0.7,
    )
    node_c = ConceptualNode(
        text="concept gamma", type=NodeType.FACT,
        centrality=0.3, base_usefulness=0.5, trustworthiness=0.6,
    )

    await store.add_node(node_a)
    await store.add_node(node_b)
    await store.add_node(node_c)

    edge_ab = MemoryEdge(
        source_id=node_a.id, target_id=node_b.id, edge_type=EdgeType.RELATED_TO,
    )
    edge_bc = MemoryEdge(
        source_id=node_b.id, target_id=node_c.id, edge_type=EdgeType.RELATED_TO,
    )
    await store.add_edge(edge_ab)
    await store.add_edge(edge_bc)

    return node_a, node_b, node_c, edge_ab, edge_bc


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_sample_seeds_weighted(walker: GraphWalker, store: SQLiteMemoryStore):
    """Higher-centrality nodes should be sampled more often."""
    node_a, node_b, node_c, *_ = await create_test_graph(store)

    counts: dict[str, int] = {node_a.id: 0, node_b.id: 0, node_c.id: 0}
    for _ in range(200):
        seeds = await walker.sample_seeds(1)
        assert len(seeds) == 1
        counts[seeds[0]] += 1

    # Node A (centrality=0.8) should appear most often
    assert counts[node_a.id] > counts[node_c.id]


async def test_sample_seeds_fewer_than_n(walker: GraphWalker, store: SQLiteMemoryStore):
    """When fewer active nodes exist than n, return all of them."""
    node_a, node_b, node_c, *_ = await create_test_graph(store)

    seeds = await walker.sample_seeds(10)
    assert set(seeds) == {node_a.id, node_b.id, node_c.id}


async def test_bounded_walk_visits_nodes(walker: GraphWalker, store: SQLiteMemoryStore):
    """Walk from A should visit connected nodes B and C."""
    node_a, node_b, node_c, *_ = await create_test_graph(store)

    result = await walker.bounded_walk(node_a.id)

    assert isinstance(result, WalkResult)
    assert node_a.id in result.visited_node_ids
    assert node_b.id in result.visited_node_ids
    assert node_c.id in result.visited_node_ids
    assert result.steps_taken >= 3
    assert result.time_ms >= 0
    assert not result.interrupted


async def test_bounded_walk_respects_max_steps(
    walker: GraphWalker, store: SQLiteMemoryStore
):
    """Walk should stop when max_steps is reached."""
    node_a, *_ = await create_test_graph(store)

    result = await walker.bounded_walk(node_a.id, max_steps=1)

    assert result.steps_taken <= 1


async def test_bounded_walk_respects_max_time(
    walker: GraphWalker, store: SQLiteMemoryStore
):
    """Walk should respect time budget (very small max_time_ms)."""
    node_a, *_ = await create_test_graph(store)

    # max_time_ms=0 guarantees immediate stop (after first step at most)
    result = await walker.bounded_walk(node_a.id, max_steps=1000, max_time_ms=0)

    assert result.steps_taken <= 1


async def test_bounded_walk_interrupt_check(
    walker: GraphWalker, store: SQLiteMemoryStore
):
    """Interrupt callback should stop the walk and set interrupted=True."""
    # Build a longer chain so the walker has work to do
    nodes = []
    for i in range(25):
        n = ConceptualNode(
            text=f"node-{i}", type=NodeType.FACT,
            centrality=0.5, base_usefulness=0.6, trustworthiness=0.7,
        )
        await store.add_node(n)
        nodes.append(n)
    for i in range(len(nodes) - 1):
        e = MemoryEdge(
            source_id=nodes[i].id, target_id=nodes[i + 1].id,
            edge_type=EdgeType.RELATED_TO,
        )
        await store.add_edge(e)

    call_count = 0

    def interrupt_after_10() -> bool:
        nonlocal call_count
        call_count += 1
        return True  # always interrupt when called

    result = await walker.bounded_walk(
        nodes[0].id, max_steps=100, interrupt_check=interrupt_after_10,
    )

    assert result.interrupted
    # Interrupt is checked at step 10, so we should have exactly 10 steps
    assert result.steps_taken == 10


async def test_reinforce_edge_increases_weight(
    walker: GraphWalker, store: SQLiteMemoryStore
):
    """reinforce_edge should increase edge weight."""
    *_, edge_ab, _ = await create_test_graph(store)

    original_weight = edge_ab.weight  # 1.0
    await walker.reinforce_edge(edge_ab.id, amount=0.2)

    async with store._db.execute(
        "SELECT weight FROM edges WHERE id = ?", (edge_ab.id,)
    ) as cur:
        row = await cur.fetchone()

    assert row["weight"] == pytest.approx(original_weight + 0.2)


async def test_weaken_edge_decreases_weight(
    walker: GraphWalker, store: SQLiteMemoryStore
):
    """weaken_edge should decrease weight but not below 0.01."""
    *_, edge_ab, _ = await create_test_graph(store)

    # Weaken by a huge amount — weight should floor at 0.01
    await walker.weaken_edge(edge_ab.id, amount=999.0)

    async with store._db.execute(
        "SELECT weight FROM edges WHERE id = ?", (edge_ab.id,)
    ) as cur:
        row = await cur.fetchone()

    assert row["weight"] == pytest.approx(0.01)


async def test_walk_produces_proposed_actions(
    walker: GraphWalker, store: SQLiteMemoryStore
):
    """Walk result should contain proposed actions based on edge activations."""
    # Use high trust_useful so activation crosses the >0.5 threshold
    node_a = ConceptualNode(
        text="high alpha", type=NodeType.FACT,
        centrality=0.9, base_usefulness=0.9, trustworthiness=0.9,
    )
    node_b = ConceptualNode(
        text="high beta", type=NodeType.FACT,
        centrality=0.7, base_usefulness=0.9, trustworthiness=0.9,
    )
    await store.add_node(node_a)
    await store.add_node(node_b)
    edge = MemoryEdge(
        source_id=node_a.id, target_id=node_b.id, edge_type=EdgeType.RELATED_TO,
    )
    await store.add_edge(edge)

    result = await walker.bounded_walk(node_a.id)

    # activation for edge = 1.0 * 1.0(weight) * 0.81(trust_useful) > 0.5
    assert len(result.proposed_actions) > 0
    action_types = {a["type"] for a in result.proposed_actions}
    assert "reinforce" in action_types
    for action in result.proposed_actions:
        assert action["type"] in ("reinforce", "weaken")
        assert "edge_id" in action
        assert "amount" in action


async def test_activation_propagation(
    walker: GraphWalker, store: SQLiteMemoryStore
):
    """Activation should decrease with distance from seed."""
    node_a, node_b, node_c, edge_ab, edge_bc = await create_test_graph(store)

    result = await walker.bounded_walk(node_a.id)

    # edge_ab connects A->B, edge_bc connects B->C
    act_ab = result.edge_activations.get(edge_ab.id, 0.0)
    act_bc = result.edge_activations.get(edge_bc.id, 0.0)

    # A has activation 1.0; propagation to B is 1.0 * weight * trust_useful
    # B's activation is less than 1.0, so propagation to C should be smaller
    assert act_ab > act_bc
    assert act_ab > 0
    assert act_bc > 0
