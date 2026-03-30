"""Tests for the memory encoding pipeline."""
from __future__ import annotations

import pytest

from agentgolem.memory.encoding import (
    BatchComparisonResult,
    ComparisonDecision,
    DecomposedConcept,
    DecompositionResult,
    MemoryEncoder,
    TYPE_PRIORS,
)
from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    NodeFilter,
    NodeType,
    Source,
    SourceKind,
)
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore


class MockLLM:
    """Mock LLM that returns predictable responses."""

    def __init__(self) -> None:
        self.complete_calls: list = []
        self.structured_calls: list = []
        self._structured_responses: list = []

    def set_structured_responses(self, responses: list) -> None:
        self._structured_responses = list(responses)

    async def complete(self, messages, **kwargs):
        self.complete_calls.append(messages)
        return "mock response"

    async def complete_structured(self, messages, schema, **kwargs):
        self.structured_calls.append((messages, schema))
        if self._structured_responses:
            return self._structured_responses.pop(0)
        # Default: return a new_node decision or decomposition
        if schema == DecompositionResult:
            return DecompositionResult(
                concepts=[DecomposedConcept(text="the sky is blue", type="fact")]
            )
        if schema == ComparisonDecision:
            return ComparisonDecision(decision="new_node")
        if schema == BatchComparisonResult:
            return BatchComparisonResult(
                decisions=[ComparisonDecision(decision="new_node")]
            )
        raise ValueError(f"Unexpected schema: {schema}")


@pytest.fixture
async def setup(tmp_path):
    db = await init_db(tmp_path / "test.db")
    store = SQLiteMemoryStore(db)
    llm = MockLLM()
    encoder = MemoryEncoder(store=store, llm=llm)
    yield store, llm, encoder
    await close_db(db)


def _make_source(**kwargs) -> Source:
    defaults = {"kind": SourceKind.HUMAN, "origin": "test-user"}
    defaults.update(kwargs)
    return Source(**defaults)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_encode_creates_node(setup):
    """Encode with LLM returning one concept → one node created in store."""
    store, llm, encoder = setup
    source = _make_source()

    llm.set_structured_responses([
        DecompositionResult(concepts=[DecomposedConcept(text="the sky is blue", type="fact")]),
        BatchComparisonResult(decisions=[ComparisonDecision(decision="new_node")]),
    ])

    nodes = await encoder.encode("The sky is blue.", source)

    assert len(nodes) == 1
    assert nodes[0].text == "the sky is blue"
    assert nodes[0].type == NodeType.FACT

    # Verify it's persisted in the store
    fetched = await store.get_node(nodes[0].id)
    assert fetched is not None
    assert fetched.text == "the sky is blue"


async def test_encode_applies_type_priors(setup):
    """Fact type gets TYPE_PRIORS[FACT] as initial trust and usefulness."""
    store, llm, encoder = setup
    source = _make_source()
    expected_trust = TYPE_PRIORS[NodeType.FACT]

    llm.set_structured_responses([
        DecompositionResult(concepts=[DecomposedConcept(text="water boils at 100C", type="fact")]),
        BatchComparisonResult(decisions=[ComparisonDecision(decision="new_node")]),
    ])

    nodes = await encoder.encode("Water boils at 100C.", source)

    assert len(nodes) == 1
    assert nodes[0].trustworthiness == expected_trust
    assert nodes[0].base_usefulness == expected_trust


async def test_encode_links_source(setup):
    """The created node is linked to the provided source."""
    store, llm, encoder = setup
    source = _make_source()

    llm.set_structured_responses([
        DecompositionResult(concepts=[DecomposedConcept(text="dogs are loyal", type="fact")]),
        BatchComparisonResult(decisions=[ComparisonDecision(decision="new_node")]),
    ])

    nodes = await encoder.encode("Dogs are loyal.", source)

    assert len(nodes) == 1
    sources = await store.get_node_sources(nodes[0].id)
    assert len(sources) == 1
    assert sources[0].id == source.id


async def test_encode_multiple_concepts_creates_cluster(setup):
    """LLM returns 3 concepts → cluster created."""
    store, llm, encoder = setup
    source = _make_source()

    llm.set_structured_responses([
        DecompositionResult(concepts=[
            DecomposedConcept(text="apples are red", type="fact"),
            DecomposedConcept(text="bananas are yellow", type="fact"),
            DecomposedConcept(text="grapes are purple", type="fact"),
        ]),
        # One batched comparison for all 3 concepts
        BatchComparisonResult(decisions=[
            ComparisonDecision(decision="new_node"),
            ComparisonDecision(decision="new_node"),
            ComparisonDecision(decision="new_node"),
        ]),
    ])

    nodes = await encoder.encode("Apples are red, bananas yellow, grapes purple.", source)

    assert len(nodes) == 3

    stats = await store.get_statistics()
    assert stats["total_clusters"] == 1


async def test_encode_supersedes_creates_edge(setup):
    """LLM comparison returns 'supersedes' → SUPERSEDES edge created."""
    store, llm, encoder = setup
    source = _make_source()

    # Pre-insert an existing node
    existing_node = ConceptualNode(text="earth is flat", type=NodeType.FACT)
    await store.add_node(existing_node)

    llm.set_structured_responses([
        DecompositionResult(
            concepts=[DecomposedConcept(text="earth is round", type="fact")]
        ),
        BatchComparisonResult(decisions=[
            ComparisonDecision(
                decision="supersedes",
                existing_node_id=existing_node.id,
                reason="updated understanding",
            ),
        ]),
    ])

    nodes = await encoder.encode("Earth is round.", source)

    assert len(nodes) == 1
    edges = await store.get_edges_from(nodes[0].id)
    assert len(edges) == 1
    assert edges[0].edge_type == EdgeType.SUPERSEDES
    assert edges[0].target_id == existing_node.id


async def test_encode_contradicts_creates_edge(setup):
    """LLM comparison returns 'contradicts' → CONTRADICTS edge created."""
    store, llm, encoder = setup
    source = _make_source()

    # Pre-insert an existing node
    existing_node = ConceptualNode(text="sugar is healthy", type=NodeType.FACT)
    await store.add_node(existing_node)

    llm.set_structured_responses([
        DecompositionResult(
            concepts=[DecomposedConcept(text="sugar is unhealthy", type="fact")]
        ),
        BatchComparisonResult(decisions=[
            ComparisonDecision(
                decision="contradicts",
                existing_node_id=existing_node.id,
                reason="conflicting claims",
            ),
        ]),
    ])

    nodes = await encoder.encode("Sugar is unhealthy.", source)

    assert len(nodes) == 1
    edges = await store.get_edges_from(nodes[0].id)
    assert len(edges) == 1
    assert edges[0].edge_type == EdgeType.CONTRADICTS
    assert edges[0].target_id == existing_node.id


async def test_encode_keep_exact_no_new_node(setup):
    """'keep_exact' returns no new node, just links source."""
    store, llm, encoder = setup
    source = _make_source()

    # Pre-insert an existing node
    existing_node = ConceptualNode(text="water is wet", type=NodeType.FACT)
    await store.add_node(existing_node)

    llm.set_structured_responses([
        DecompositionResult(
            concepts=[DecomposedConcept(text="water is wet", type="fact")]
        ),
        BatchComparisonResult(decisions=[
            ComparisonDecision(
                decision="keep_exact",
                existing_node_id=existing_node.id,
                reason="identical concept exists",
            ),
        ]),
    ])

    nodes = await encoder.encode("Water is wet.", source)

    assert len(nodes) == 0

    # Existing node should now be linked to the new source
    sources = await store.get_node_sources(existing_node.id)
    assert any(s.id == source.id for s in sources)


async def test_type_priors_all_types_covered():
    """TYPE_PRIORS has an entry for every NodeType."""
    for nt in NodeType:
        assert nt in TYPE_PRIORS, f"Missing TYPE_PRIORS entry for {nt.value}"
