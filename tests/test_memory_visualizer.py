"""Tests for memory visualizer backend helpers."""
from __future__ import annotations

from pathlib import Path

from agentgolem.memory.models import ConceptualNode, NodeType
from agentgolem.memory.mycelium import MemoryReference, MyceliumStore
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.shared_exports import SharedMemoryExporter
from agentgolem.memory.store import SQLiteMemoryStore
from tools import memory_visualizer


async def _create_agent_store(
    data_dir: Path,
    agent_id: str,
    *,
    text: str,
    search_text: str,
) -> tuple[SQLiteMemoryStore, ConceptualNode]:
    db = await init_db(data_dir / agent_id / "memory" / "graph.db")
    store = SQLiteMemoryStore(db)
    node = ConceptualNode(
        text=text,
        search_text=search_text,
        type=NodeType.INTERPRETATION,
        trustworthiness=0.85,
        base_usefulness=0.8,
        salience=0.8,
        centrality=0.7,
    )
    await store.add_node(node)
    return store, node


async def test_visualizer_augments_graph_with_peer_ghosts(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    local_store, local_node = await _create_agent_store(
        data_dir,
        "c1",
        text="Local memory about sleep resonance.",
        search_text="sleep resonance local memory",
    )
    peer_store, peer_node = await _create_agent_store(
        data_dir,
        "c2",
        text="Peer memory about ethical resonance.",
        search_text="ethical resonance peer memory",
    )
    mycelium_store = MyceliumStore(data_dir / "shared_memory" / "mycelium.db")

    try:
        exporter = SharedMemoryExporter(
            peer_store,
            data_dir / "shared_memory" / "exports" / "c2.sqlite",
        )
        await exporter.export_snapshot("c2", "Council-2")
        await mycelium_store.upsert_entanglement(
            MemoryReference("c1", local_node.id),
            MemoryReference("c2", peer_node.id),
            weight_delta=0.6,
            confidence=0.9,
        )

        graph = memory_visualizer._get_graph_data(
            data_dir / "c1" / "memory" / "graph.db",
            {"status": "active", "limit": "50"},
            "c1",
        )
        augmented = memory_visualizer._augment_with_mycelium(data_dir, "c1", graph)

        assert any(node["is_peer_ghost"] for node in augmented["nodes"])
        assert any(
            edge["edge_type"] == "entangled_with"
            for edge in augmented["edges"]
        )
        assert augmented["stats"]["_peer_nodes"] == 1
        assert augmented["stats"]["_entanglements"] == 1
    finally:
        await mycelium_store.close()
        await close_db(local_store._db)
        await close_db(peer_store._db)
