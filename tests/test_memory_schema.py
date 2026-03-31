"""Tests for the memory graph SQLite schema."""
from __future__ import annotations

import pytest
import aiosqlite

from agentgolem.memory.schema import init_db, get_db_version, close_db, SCHEMA_VERSION


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "memory" / "graph.db"
    conn = await init_db(db_path)
    yield conn
    await close_db(conn)


EXPECTED_TABLES = {
    "schema_version",
    "nodes",
    "edges",
    "sources",
    "node_sources",
    "clusters",
    "cluster_members",
    "cluster_sources",
}

EXPECTED_INDEXES = {
    "idx_nodes_type",
    "idx_nodes_status",
    "idx_nodes_trustworthiness",
    "idx_nodes_base_usefulness",
    "idx_nodes_search_text",
    "idx_nodes_last_accessed",
    "idx_nodes_canonical",
    "idx_edges_source_id",
    "idx_edges_target_id",
    "idx_clusters_status",
}


async def test_init_db_creates_file(tmp_path):
    db_path = tmp_path / "memory" / "graph.db"
    assert not db_path.exists()
    conn = await init_db(db_path)
    try:
        assert db_path.exists()
    finally:
        await close_db(conn)


async def test_all_tables_exist(db: aiosqlite.Connection):
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ) as cursor:
        rows = await cursor.fetchall()
    table_names = {row[0] for row in rows}
    assert table_names == EXPECTED_TABLES


async def test_indexes_exist(db: aiosqlite.Connection):
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    ) as cursor:
        rows = await cursor.fetchall()
    index_names = {row[0] for row in rows}
    assert index_names == EXPECTED_INDEXES


async def test_schema_version_set(db: aiosqlite.Connection):
    version = await get_db_version(db)
    assert version == SCHEMA_VERSION


async def test_foreign_keys_enabled(db: aiosqlite.Connection):
    async with db.execute("PRAGMA foreign_keys") as cursor:
        row = await cursor.fetchone()
    assert row[0] == 1


async def test_wal_mode(db: aiosqlite.Connection):
    async with db.execute("PRAGMA journal_mode") as cursor:
        row = await cursor.fetchone()
    assert row[0] == "wal"


async def test_init_db_idempotent(tmp_path):
    db_path = tmp_path / "memory" / "graph.db"
    conn1 = await init_db(db_path)
    await close_db(conn1)
    conn2 = await init_db(db_path)
    try:
        version = await get_db_version(conn2)
        assert version == SCHEMA_VERSION
    finally:
        await close_db(conn2)


async def test_init_db_resets_outdated_schema(tmp_path):
    db_path = tmp_path / "memory" / "graph.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    stale = await aiosqlite.connect(str(db_path))
    try:
        await stale.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        await stale.execute("INSERT INTO schema_version (version) VALUES (1)")
        await stale.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY, text TEXT NOT NULL)")
        await stale.commit()
    finally:
        await stale.close()

    conn = await init_db(db_path)
    try:
        version = await get_db_version(conn)
        assert version == SCHEMA_VERSION

        async with conn.execute("PRAGMA table_info(nodes)") as cursor:
            columns = {row[1] async for row in cursor}
        assert "search_text" in columns
        assert "salience" in columns
    finally:
        await close_db(conn)


async def test_v2_to_v3_migration_adds_edge_columns(tmp_path):
    """A v2 database should be migrated to v3 with new edge columns."""
    db_path = tmp_path / "memory" / "graph.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Create a minimal v2 database
    stale = await aiosqlite.connect(str(db_path))
    try:
        await stale.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY)"
        )
        await stale.execute("INSERT INTO schema_version (version) VALUES (2)")
        await stale.execute(
            """CREATE TABLE nodes (
                id TEXT PRIMARY KEY, text TEXT NOT NULL, search_text TEXT DEFAULT '',
                type TEXT NOT NULL, created_at TEXT NOT NULL, last_accessed TEXT NOT NULL,
                access_count INTEGER DEFAULT 0, base_usefulness REAL DEFAULT 0.5,
                trustworthiness REAL DEFAULT 0.5, salience REAL DEFAULT 0.5,
                emotion_label TEXT DEFAULT 'neutral', emotion_score REAL DEFAULT 0.0,
                centrality REAL DEFAULT 0.0, status TEXT DEFAULT 'active',
                canonical INTEGER DEFAULT 0
            )"""
        )
        await stale.execute(
            """CREATE TABLE edges (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL, weight REAL DEFAULT 1.0, created_at TEXT NOT NULL
            )"""
        )
        await stale.execute(
            """CREATE TABLE sources (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, origin TEXT NOT NULL,
                reliability REAL DEFAULT 0.5, independence_group TEXT DEFAULT '',
                timestamp TEXT NOT NULL, raw_reference TEXT DEFAULT ''
            )"""
        )
        await stale.execute(
            """CREATE TABLE node_sources (
                node_id TEXT NOT NULL, source_id TEXT NOT NULL,
                PRIMARY KEY (node_id, source_id)
            )"""
        )
        await stale.execute(
            """CREATE TABLE clusters (
                id TEXT PRIMARY KEY, label TEXT NOT NULL,
                cluster_type TEXT DEFAULT 'general',
                emotion_label TEXT DEFAULT 'neutral', emotion_score REAL DEFAULT 0.0,
                base_usefulness REAL DEFAULT 0.5, trustworthiness REAL DEFAULT 0.5,
                contradiction_status TEXT DEFAULT 'none', created_at TEXT NOT NULL,
                last_accessed TEXT NOT NULL, access_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            )"""
        )
        await stale.execute(
            """CREATE TABLE cluster_members (
                cluster_id TEXT NOT NULL, node_id TEXT NOT NULL,
                PRIMARY KEY (cluster_id, node_id)
            )"""
        )
        await stale.execute(
            """CREATE TABLE cluster_sources (
                cluster_id TEXT NOT NULL, source_id TEXT NOT NULL,
                PRIMARY KEY (cluster_id, source_id)
            )"""
        )
        # Insert a v2 edge (no semantic columns)
        await stale.execute(
            "INSERT INTO edges VALUES "
            "('e1', 'n1', 'n2', 'related_to', 1.0, '2025-01-01T00:00:00')"
        )
        await stale.commit()
    finally:
        await stale.close()

    # Now open with init_db — should migrate v2→v3
    conn = await init_db(db_path)
    try:
        version = await get_db_version(conn)
        assert version == SCHEMA_VERSION

        # Check new columns exist
        async with conn.execute("PRAGMA table_info(edges)") as cursor:
            columns = {row[1] async for row in cursor}
        for col in (
            "confidence", "temporal_valid_from", "temporal_valid_to",
            "direction", "constraint_tag", "probability", "behavior",
            "modified_at",
        ):
            assert col in columns, f"Missing column: {col}"

        # The pre-existing edge should have defaults
        async with conn.execute("SELECT * FROM edges WHERE id = 'e1'") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["confidence"] == 1.0
        assert row["probability"] == 1.0
        assert row["direction"] == "forward"
    finally:
        await close_db(conn)
