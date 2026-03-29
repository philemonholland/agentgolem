"""SQLite schema for the memory graph."""
from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA_VERSION = 1

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    access_count INTEGER DEFAULT 0,
    base_usefulness REAL DEFAULT 0.5,
    trustworthiness REAL DEFAULT 0.5,
    emotion_label TEXT DEFAULT 'neutral',
    emotion_score REAL DEFAULT 0.0,
    centrality REAL DEFAULT 0.0,
    status TEXT DEFAULT 'active',
    canonical INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES nodes(id),
    FOREIGN KEY (target_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    reliability REAL DEFAULT 0.5,
    independence_group TEXT DEFAULT '',
    timestamp TEXT NOT NULL,
    raw_reference TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS node_sources (
    node_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    PRIMARY KEY (node_id, source_id),
    FOREIGN KEY (node_id) REFERENCES nodes(id),
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS clusters (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    cluster_type TEXT DEFAULT 'general',
    emotion_label TEXT DEFAULT 'neutral',
    emotion_score REAL DEFAULT 0.0,
    base_usefulness REAL DEFAULT 0.5,
    trustworthiness REAL DEFAULT 0.5,
    contradiction_status TEXT DEFAULT 'none',
    created_at TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    access_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS cluster_members (
    cluster_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    PRIMARY KEY (cluster_id, node_id),
    FOREIGN KEY (cluster_id) REFERENCES clusters(id),
    FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS cluster_sources (
    cluster_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    PRIMARY KEY (cluster_id, source_id),
    FOREIGN KEY (cluster_id) REFERENCES clusters(id),
    FOREIGN KEY (source_id) REFERENCES sources(id)
);
"""

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
CREATE INDEX IF NOT EXISTS idx_nodes_trustworthiness ON nodes(trustworthiness);
CREATE INDEX IF NOT EXISTS idx_nodes_base_usefulness ON nodes(base_usefulness);
CREATE INDEX IF NOT EXISTS idx_nodes_last_accessed ON nodes(last_accessed);
CREATE INDEX IF NOT EXISTS idx_nodes_canonical ON nodes(canonical);
CREATE INDEX IF NOT EXISTS idx_edges_source_id ON edges(source_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_target_id ON edges(target_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_clusters_status ON clusters(status);
"""


async def init_db(db_path: Path) -> aiosqlite.Connection:
    """Initialize the database with schema. Returns open connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row

    # Enable WAL mode for better concurrency
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")

    # Create tables
    await db.executescript(TABLES_SQL)
    await db.executescript(INDEXES_SQL)

    # Set version
    async with db.execute("SELECT COUNT(*) FROM schema_version") as cursor:
        row = await cursor.fetchone()
        if row[0] == 0:
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )

    await db.commit()
    return db


async def get_db_version(db: aiosqlite.Connection) -> int:
    """Get current schema version."""
    async with db.execute("SELECT MAX(version) FROM schema_version") as cursor:
        row = await cursor.fetchone()
        return row[0] if row and row[0] else 0


async def close_db(db: aiosqlite.Connection) -> None:
    """Close the database connection."""
    await db.close()
