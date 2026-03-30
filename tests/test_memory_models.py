"""Tests for memory graph data models."""
from __future__ import annotations

from datetime import datetime, timezone

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


def test_conceptual_node_defaults():
    node = ConceptualNode(text="Python is great", type=NodeType.FACT)
    assert node.text == "Python is great"
    assert node.search_text == ""
    assert node.type is NodeType.FACT
    assert isinstance(node.id, str) and len(node.id) == 36
    assert isinstance(node.created_at, datetime)
    assert node.created_at.tzinfo is not None
    assert node.access_count == 0
    assert node.base_usefulness == 0.5
    assert node.trustworthiness == 0.5
    assert node.salience == 0.5
    assert node.emotion_label == "neutral"
    assert node.emotion_score == 0.0
    assert node.centrality == 0.0
    assert node.status is NodeStatus.ACTIVE
    assert node.canonical is False


def test_node_unique_ids():
    a = ConceptualNode(text="first", type=NodeType.FACT)
    b = ConceptualNode(text="second", type=NodeType.FACT)
    assert a.id != b.id


def test_node_trust_useful_property():
    node = ConceptualNode(
        text="test", type=NodeType.FACT, base_usefulness=0.8, trustworthiness=0.6
    )
    assert node.trust_useful == 0.8 * 0.6


def test_all_node_types():
    for nt in NodeType:
        node = ConceptualNode(text=f"node of type {nt.value}", type=nt)
        assert node.type is nt


def test_edge_creation():
    edge = MemoryEdge(
        source_id="aaa",
        target_id="bbb",
        edge_type=EdgeType.SUPPORTS,
        weight=0.9,
    )
    assert edge.source_id == "aaa"
    assert edge.target_id == "bbb"
    assert edge.edge_type is EdgeType.SUPPORTS
    assert edge.weight == 0.9
    assert isinstance(edge.id, str) and len(edge.id) == 36
    assert isinstance(edge.created_at, datetime)


def test_source_creation():
    src = Source(
        kind=SourceKind.WEB,
        origin="https://example.com",
        reliability=0.9,
        independence_group="web-group",
        raw_reference="page title",
    )
    assert src.kind is SourceKind.WEB
    assert src.origin == "https://example.com"
    assert src.reliability == 0.9
    assert src.independence_group == "web-group"
    assert src.raw_reference == "page title"
    assert isinstance(src.id, str) and len(src.id) == 36
    assert isinstance(src.timestamp, datetime)


def test_cluster_creation():
    cluster = MemoryCluster(
        label="Python knowledge",
        node_ids=["n1", "n2", "n3"],
        source_ids=["s1"],
    )
    assert cluster.label == "Python knowledge"
    assert cluster.node_ids == ["n1", "n2", "n3"]
    assert cluster.source_ids == ["s1"]
    assert cluster.cluster_type == "general"
    assert cluster.contradiction_status == "none"
    assert cluster.status is NodeStatus.ACTIVE
    assert cluster.trust_useful == 0.5 * 0.5


def test_node_filter_defaults():
    nf = NodeFilter()
    assert nf.type is None
    assert nf.status is None
    assert nf.canonical is None
    assert nf.trust_min is None
    assert nf.trust_max is None
    assert nf.usefulness_min is None
    assert nf.usefulness_max is None
    assert nf.text_contains is None
    assert nf.limit == 50
    assert nf.offset == 0


def test_node_update_partial():
    update = NodeUpdate(
        text="updated text",
        search_text="updated search text",
        base_usefulness=0.9,
        salience=0.8,
    )
    assert update.text == "updated text"
    assert update.search_text == "updated search text"
    assert update.base_usefulness == 0.9
    assert update.trustworthiness is None
    assert update.salience == 0.8
    assert update.emotion_label is None
    assert update.status is None
    assert update.canonical is None
    assert update.access_count is None
    assert update.last_accessed is None


def test_node_status_values():
    assert NodeStatus.ACTIVE.value == "active"
    assert NodeStatus.ARCHIVED.value == "archived"
    assert NodeStatus.PURGED.value == "purged"
    assert len(NodeStatus) == 3
