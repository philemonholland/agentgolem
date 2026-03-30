"""Tests for the EKG-inspired memory encoding pipeline."""
from __future__ import annotations

import pytest

from agentgolem.memory.encoding import (
    BatchComparisonResult,
    ComparisonDecision,
    DecomposedConcept,
    DecompositionRelation,
    DecompositionResult,
    DecompositionView,
    MemoryEncoder,
    TYPE_PRIORS,
)
from agentgolem.memory.models import ConceptualNode, EdgeType, NodeType, Source, SourceKind
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore


class MockLLM:
    """Mock LLM that returns predictable structured responses."""

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

        if schema == DecompositionResult:
            return _decomposition_result(
                grounded_claims=[
                    DecomposedConcept(
                        text="the sky is blue",
                        search_text="sky blue",
                        type="fact",
                        salience=0.6,
                    )
                ]
            )
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


def _decomposition_result(
    *,
    grounded_claims: list[DecomposedConcept] | None = None,
    semantic_claims: list[DecomposedConcept] | None = None,
    grounded_relations: list[DecompositionRelation] | None = None,
    semantic_relations: list[DecompositionRelation] | None = None,
    grounded_label: str = "",
    semantic_label: str = "",
) -> DecompositionResult:
    return DecompositionResult(
        grounded_view=DecompositionView(
            label=grounded_label,
            concepts=grounded_claims or [],
            relations=grounded_relations or [],
        ),
        semantic_view=DecompositionView(
            label=semantic_label,
            concepts=semantic_claims or [],
            relations=semantic_relations or [],
        ),
    )


async def test_encode_creates_node(setup):
    """Encode with one reconciled claim → one node created in store."""
    store, llm, encoder = setup
    source = _make_source()

    llm.set_structured_responses(
        [
            _decomposition_result(
                grounded_claims=[
                    DecomposedConcept(
                        text="the sky is blue",
                        search_text="sky blue",
                        type="fact",
                        salience=0.6,
                    )
                ]
            ),
            BatchComparisonResult(decisions=[ComparisonDecision(decision="new_node")]),
        ]
    )

    nodes = await encoder.encode("The sky is blue.", source)

    assert len(nodes) == 1
    assert nodes[0].text == "the sky is blue"
    assert nodes[0].search_text == "sky blue"
    assert nodes[0].type == NodeType.FACT

    fetched = await store.get_node(nodes[0].id)
    assert fetched is not None
    assert fetched.text == "the sky is blue"
    assert fetched.search_text == "sky blue"


async def test_encode_applies_type_priors(setup):
    """Fact type gets TYPE_PRIORS[FACT] as initial trust and usefulness."""
    store, llm, encoder = setup
    source = _make_source()
    expected_trust = TYPE_PRIORS[NodeType.FACT]

    llm.set_structured_responses(
        [
            _decomposition_result(
                grounded_claims=[
                    DecomposedConcept(
                        text="water boils at 100C",
                        search_text="water boils 100c",
                        type="fact",
                    )
                ]
            ),
            BatchComparisonResult(decisions=[ComparisonDecision(decision="new_node")]),
        ]
    )

    nodes = await encoder.encode("Water boils at 100C.", source)

    assert len(nodes) == 1
    assert nodes[0].trustworthiness == expected_trust
    assert nodes[0].base_usefulness == expected_trust


async def test_encode_links_source(setup):
    """The created node is linked to the provided source."""
    store, llm, encoder = setup
    source = _make_source()

    llm.set_structured_responses(
        [
            _decomposition_result(
                grounded_claims=[
                    DecomposedConcept(
                        text="dogs are loyal",
                        search_text="dogs loyal",
                        type="fact",
                    )
                ]
            ),
            BatchComparisonResult(decisions=[ComparisonDecision(decision="new_node")]),
        ]
    )

    nodes = await encoder.encode("Dogs are loyal.", source)

    assert len(nodes) == 1
    sources = await store.get_node_sources(nodes[0].id)
    assert len(sources) == 1
    assert sources[0].id == source.id


async def test_encode_multiple_concepts_creates_cluster(setup):
    """Multiple claims from one batch create an encoding cluster."""
    store, llm, encoder = setup
    source = _make_source()

    llm.set_structured_responses(
        [
            _decomposition_result(
                grounded_claims=[
                    DecomposedConcept(text="apples are red", search_text="apples red", type="fact"),
                    DecomposedConcept(text="bananas are yellow", search_text="bananas yellow", type="fact"),
                    DecomposedConcept(text="grapes are purple", search_text="grapes purple", type="fact"),
                ],
                semantic_label="fruit colors",
            ),
            BatchComparisonResult(
                decisions=[
                    ComparisonDecision(decision="new_node"),
                    ComparisonDecision(decision="new_node"),
                    ComparisonDecision(decision="new_node"),
                ]
            ),
        ]
    )

    nodes = await encoder.encode(
        "Apples are red, bananas yellow, grapes purple.", source
    )

    assert len(nodes) == 3
    stats = await store.get_statistics()
    assert stats["total_clusters"] == 1


async def test_encode_supersedes_creates_edge(setup):
    """LLM comparison returns 'supersedes' → SUPERSEDES edge created."""
    store, llm, encoder = setup
    source = _make_source()

    existing_node = ConceptualNode(text="earth is flat", type=NodeType.FACT)
    await store.add_node(existing_node)

    llm.set_structured_responses(
        [
            _decomposition_result(
                grounded_claims=[
                    DecomposedConcept(
                        text="earth is round",
                        search_text="earth round",
                        type="fact",
                    )
                ]
            ),
            BatchComparisonResult(
                decisions=[
                    ComparisonDecision(
                        decision="supersedes",
                        existing_node_id=existing_node.id,
                        reason="updated understanding",
                    )
                ]
            ),
        ]
    )

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

    existing_node = ConceptualNode(text="sugar is healthy", type=NodeType.FACT)
    await store.add_node(existing_node)

    llm.set_structured_responses(
        [
            _decomposition_result(
                grounded_claims=[
                    DecomposedConcept(
                        text="sugar is unhealthy",
                        search_text="sugar unhealthy",
                        type="fact",
                    )
                ]
            ),
            BatchComparisonResult(
                decisions=[
                    ComparisonDecision(
                        decision="contradicts",
                        existing_node_id=existing_node.id,
                        reason="conflicting claims",
                    )
                ]
            ),
        ]
    )

    nodes = await encoder.encode("Sugar is unhealthy.", source)

    assert len(nodes) == 1
    edges = await store.get_edges_from(nodes[0].id)
    assert len(edges) == 1
    assert edges[0].edge_type == EdgeType.CONTRADICTS
    assert edges[0].target_id == existing_node.id


async def test_encode_keep_exact_no_new_node(setup):
    """'keep_exact' returns no new node, just links source and updates search text."""
    store, llm, encoder = setup
    source = _make_source()

    existing_node = ConceptualNode(text="water is wet", type=NodeType.FACT)
    await store.add_node(existing_node)

    llm.set_structured_responses(
        [
            _decomposition_result(
                grounded_claims=[
                    DecomposedConcept(
                        text="water is wet",
                        search_text="water wet",
                        type="fact",
                        salience=0.8,
                    )
                ]
            ),
            BatchComparisonResult(
                decisions=[
                    ComparisonDecision(
                        decision="keep_exact",
                        existing_node_id=existing_node.id,
                        reason="identical concept exists",
                    )
                ]
            ),
        ]
    )

    nodes = await encoder.encode("Water is wet.", source)

    assert len(nodes) == 0
    sources = await store.get_node_sources(existing_node.id)
    assert any(s.id == source.id for s in sources)

    updated = await store.get_node(existing_node.id)
    assert updated is not None
    assert updated.search_text == "water wet"
    assert updated.salience == 0.8


async def test_encode_relations_create_intra_batch_edges(setup):
    """Structured relations should create semantic edges, not just chain links."""
    store, llm, encoder = setup
    source = _make_source()

    llm.set_structured_responses(
        [
            _decomposition_result(
                grounded_claims=[
                    DecomposedConcept(
                        text="Compassion reduces suffering",
                        search_text="compassion suffering",
                        type="rule",
                    ),
                    DecomposedConcept(
                        text="Reducing suffering aligns with our vow",
                        search_text="reduce suffering vow",
                        type="identity",
                    ),
                ],
                grounded_relations=[
                    DecompositionRelation(
                        source_text="Compassion reduces suffering",
                        target_text="Reducing suffering aligns with our vow",
                        edge_type="supports",
                        weight=0.9,
                    )
                ],
            ),
            BatchComparisonResult(
                decisions=[
                    ComparisonDecision(decision="new_node"),
                    ComparisonDecision(decision="new_node"),
                ]
            ),
        ]
    )

    nodes = await encoder.encode("Compassion reduces suffering and fits our vow.", source)

    assert len(nodes) == 2
    edges = await store.get_edges_from(nodes[0].id)
    assert any(edge.edge_type == EdgeType.SUPPORTS for edge in edges)


async def test_multiview_overlap_boosts_salience(setup):
    """Claims present in both views should receive a salience boost."""
    store, llm, encoder = setup
    source = _make_source()

    llm.set_structured_responses(
        [
            _decomposition_result(
                grounded_claims=[
                    DecomposedConcept(
                        text="A durable memory should preserve nuance",
                        search_text="durable memory nuance",
                        type="rule",
                        salience=0.55,
                    )
                ],
                semantic_claims=[
                    DecomposedConcept(
                        text="A durable memory should preserve nuance and context",
                        search_text="durable memory nuance",
                        type="rule",
                        salience=0.60,
                    )
                ],
            ),
            BatchComparisonResult(decisions=[ComparisonDecision(decision="new_node")]),
        ]
    )

    nodes = await encoder.encode("Durable memory should preserve nuance.", source)

    assert len(nodes) == 1
    assert nodes[0].salience > 0.60


async def test_type_priors_all_types_covered():
    """TYPE_PRIORS has an entry for every NodeType."""
    for nt in NodeType:
        assert nt in TYPE_PRIORS, f"Missing TYPE_PRIORS entry for {nt.value}"
