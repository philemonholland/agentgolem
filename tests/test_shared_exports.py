"""Tests for read-only shared memory exports."""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from agentgolem.memory.models import (
    ConceptualNode,
    NodeStatus,
    NodeType,
    NodeUpdate,
    Source,
    SourceKind,
)
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.shared_exports import SharedMemoryExporter, find_export_paths
from agentgolem.memory.store import SQLiteMemoryStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
async def store(tmp_path: Path) -> SQLiteMemoryStore:
    db = await init_db(tmp_path / "graph.db")
    memory_store = SQLiteMemoryStore(db)
    yield memory_store
    await close_db(db)


async def test_export_snapshot_writes_active_nodes_only(
    store: SQLiteMemoryStore,
    tmp_path: Path,
) -> None:
    active = ConceptualNode(
        text="Neural resonance shapes memory consolidation.",
        search_text="neural resonance memory consolidation",
        type=NodeType.INTERPRETATION,
        trustworthiness=0.9,
        base_usefulness=0.8,
        salience=0.85,
        centrality=0.7,
    )
    archived = ConceptualNode(
        text="Discarded archival note.",
        type=NodeType.FACT,
        trustworthiness=0.2,
        base_usefulness=0.2,
    )

    await store.add_node(active)
    await store.add_node(archived)
    await store.update_node(archived.id, NodeUpdate(status=NodeStatus.ARCHIVED))

    export_path = tmp_path / "shared_memory" / "exports" / "c1.sqlite"
    exporter = SharedMemoryExporter(store, export_path, max_nodes=10)

    exported_count = await exporter.export_snapshot("c1", "Council-1")

    assert exported_count == 1
    assert find_export_paths(export_path.parent) == {"c1": export_path}

    conn = sqlite3.connect(export_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM exported_nodes").fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    row = rows[0]
    assert row["agent_id"] == "c1"
    assert row["agent_label"] == "Council-1"
    assert row["node_id"] == active.id
    assert row["text"] == active.text
    assert row["search_text"] == active.search_text
    assert row["node_type"] == NodeType.INTERPRETATION.value
    assert row["trust_useful"] == pytest.approx(active.trust_useful)
    assert row["salience"] == pytest.approx(active.salience)
    assert row["centrality"] == pytest.approx(active.centrality)
    assert row["source_hint"] == ""


async def test_export_snapshot_includes_compact_source_hint(
    store: SQLiteMemoryStore,
    tmp_path: Path,
) -> None:
    node = ConceptualNode(
        text="The local TFV file reframes the vow as a daily practice.",
        search_text="tfv local vow daily practice",
        type=NodeType.INTERPRETATION,
        trustworthiness=0.9,
        base_usefulness=0.85,
        salience=0.8,
        centrality=0.7,
    )
    await store.add_node(node)
    source = Source(
        kind=SourceKind.HUMAN,
        origin="tfv/five_vows.txt",
        reliability=0.9,
        raw_reference="A concise excerpt from the Five Vows.",
    )
    await store.add_source(source)
    await store.link_node_source(node.id, source.id)

    export_path = tmp_path / "shared_memory" / "exports" / "c2.sqlite"
    exporter = SharedMemoryExporter(store, export_path, max_nodes=10)

    exported_count = await exporter.export_snapshot("c2", "Council-2")

    assert exported_count == 1
    conn = sqlite3.connect(export_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM exported_nodes").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["source_hint"] == "tfv/five_vows.txt"
