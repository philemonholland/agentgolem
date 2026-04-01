"""Tests for federated read-only memory retrieval."""
from __future__ import annotations

from pathlib import Path

from agentgolem.memory.federated_retrieval import FederatedMemoryRetriever
from agentgolem.memory.models import ConceptualNode, NodeType, Source, SourceKind
from agentgolem.memory.mycelium import EntangledReference, MemoryReference
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.shared_exports import SharedMemoryExporter
from agentgolem.memory.store import SQLiteMemoryStore


async def _create_export(
    tmp_path: Path,
    exports_dir: Path,
    *,
    agent_id: str,
    agent_label: str,
    text: str,
    search_text: str,
    source_origin: str | None = None,
) -> ConceptualNode:
    db = await init_db(tmp_path / f"{agent_id}.db")
    store = SQLiteMemoryStore(db)
    node = ConceptualNode(
        text=text,
        search_text=search_text,
        type=NodeType.INTERPRETATION,
        trustworthiness=0.85,
        base_usefulness=0.8,
        salience=0.75,
        centrality=0.7,
    )
    await store.add_node(node)
    if source_origin:
        source = Source(
            kind=SourceKind.HUMAN,
            origin=source_origin,
            reliability=0.9,
            raw_reference="Relevant local excerpt.",
        )
        await store.add_source(source)
        await store.link_node_source(node.id, source.id)
    exporter = SharedMemoryExporter(store, exports_dir / f"{agent_id}.sqlite")
    await exporter.export_snapshot(agent_id, agent_label)
    await close_db(db)
    return node


async def test_search_external_excludes_current_agent_and_matches_search_text(
    tmp_path: Path,
) -> None:
    exports_dir = tmp_path / "exports"
    await _create_export(
        tmp_path,
        exports_dir,
        agent_id="c1",
        agent_label="Council-1",
        text="Local reflection on memory systems.",
        search_text="local memory reflection",
    )
    foreign = await _create_export(
        tmp_path,
        exports_dir,
        agent_id="c2",
        agent_label="Council-2",
        text="Dream walks reinforce spiking memory traces.",
        search_text="dream walks spiking memory consolidation",
    )

    retriever = FederatedMemoryRetriever(exports_dir)
    results = await retriever.search_external(
        "spiking consolidation",
        current_agent_id="c1",
        top_k=5,
    )

    assert len(results) == 1
    assert results[0].agent_id == "c2"
    assert results[0].node_id == foreign.id


async def test_search_external_matches_source_hint(tmp_path: Path) -> None:
    exports_dir = tmp_path / "exports"
    await _create_export(
        tmp_path,
        exports_dir,
        agent_id="c1",
        agent_label="Council-1",
        text="Local reflection on vows.",
        search_text="local vow reflection",
    )
    foreign = await _create_export(
        tmp_path,
        exports_dir,
        agent_id="c2",
        agent_label="Council-2",
        text="A shared TFV interpretation.",
        search_text="shared tfv interpretation",
        source_origin="tfv/five_vows.txt",
    )

    retriever = FederatedMemoryRetriever(exports_dir)
    results = await retriever.search_external(
        "five_vows txt",
        current_agent_id="c1",
        top_k=5,
    )

    assert len(results) == 1
    assert results[0].node_id == foreign.id
    assert results[0].source_hint == "tfv/five_vows.txt"


async def test_hydrate_entangled_refs_preserves_overlay_metadata(tmp_path: Path) -> None:
    exports_dir = tmp_path / "exports"
    foreign = await _create_export(
        tmp_path,
        exports_dir,
        agent_id="c3",
        agent_label="Council-3",
        text="Ethics and memory should stay provenance-aware.",
        search_text="ethics provenance memory",
        source_origin="tfv/ethics_memory.txt",
    )

    retriever = FederatedMemoryRetriever(exports_dir)
    hydrated = await retriever.hydrate_entangled_refs(
        [
            EntangledReference(
                reference=MemoryReference("c3", foreign.id),
                weight=0.7,
                link_kind="sleep_resonance",
                confidence=0.9,
            )
        ],
        query="ethics provenance",
        top_k=5,
    )

    assert len(hydrated) == 1
    assert hydrated[0].agent_id == "c3"
    assert hydrated[0].agent_label == "Council-3"
    assert hydrated[0].node_id == foreign.id
    assert hydrated[0].source_hint == "tfv/ethics_memory.txt"
    assert hydrated[0].overlay_weight == 0.7


def test_build_query_from_local_nodes_prefers_search_text_terms() -> None:
    retriever = FederatedMemoryRetriever(Path("unused"))
    query = retriever.build_query_from_local_nodes(
        [
            ConceptualNode(
                text="Long-form reflection about neural systems.",
                search_text="spiking neural memory consolidation",
                type=NodeType.INTERPRETATION,
                trustworthiness=0.9,
                base_usefulness=0.8,
                salience=0.9,
                centrality=0.8,
            )
        ]
    )

    assert "spiking" in query
    assert "neural" in query
    assert "memory" in query
