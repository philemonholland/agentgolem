"""Read-only export layer for cross-agent memory access."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from pathlib import Path

    from agentgolem.memory.store import SQLiteMemoryStore

EXPORT_SCHEMA_VERSION = 2
DEFAULT_EXPORT_LIMIT = 2000


@dataclass(frozen=True)
class ExportedMemory:
    """Compact searchable projection of a local memory node."""

    agent_id: str
    agent_label: str
    node_id: str
    text: str
    search_text: str
    node_type: str
    trust_useful: float
    salience: float
    centrality: float
    emotion_label: str
    emotion_score: float
    source_hint: str
    last_accessed: str
    exported_at: str


class SharedMemoryExporter:
    """Owner-written export of compact memory projections for foreign read-only use."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        export_path: Path,
        max_nodes: int = DEFAULT_EXPORT_LIMIT,
    ) -> None:
        self._store = store
        self._export_path = export_path
        self._max_nodes = max_nodes

    async def export_snapshot(self, agent_id: str, agent_label: str) -> int:
        """Rewrite the export snapshot from the authoritative local store."""
        self._export_path.parent.mkdir(parents=True, exist_ok=True)
        exported_at = datetime.now(UTC).isoformat()

        async with self._store._db.execute(
            """
            SELECT
                n.id AS node_id,
                n.text,
                COALESCE(n.search_text, '') AS search_text,
                n.type AS node_type,
                (n.base_usefulness * n.trustworthiness) AS trust_useful,
                n.salience,
                n.centrality,
                n.emotion_label,
                n.emotion_score,
                n.last_accessed,
                COALESCE(
                    (
                        SELECT GROUP_CONCAT(origin, ' | ')
                        FROM (
                            SELECT DISTINCT s.origin AS origin
                            FROM node_sources ns
                            JOIN sources s ON ns.source_id = s.id
                            WHERE ns.node_id = n.id
                              AND COALESCE(s.origin, '') != ''
                            ORDER BY s.timestamp DESC
                            LIMIT 3
                        )
                    ),
                    ''
                ) AS source_hint
            FROM nodes n
            WHERE n.status = 'active'
            ORDER BY
                (n.base_usefulness * n.trustworthiness) DESC,
                n.salience DESC,
                n.centrality DESC,
                n.last_accessed DESC
            LIMIT ?
            """,
            (self._max_nodes,),
        ) as cur:
            rows = await cur.fetchall()

        async with aiosqlite.connect(self._export_path) as db:
            db.row_factory = aiosqlite.Row
            await _ensure_export_schema(db)
            await db.execute("DELETE FROM exported_nodes")
            await db.executemany(
                """
                INSERT INTO exported_nodes (
                    agent_id,
                    agent_label,
                    node_id,
                    text,
                    search_text,
                    node_type,
                    trust_useful,
                    salience,
                    centrality,
                    emotion_label,
                    emotion_score,
                    source_hint,
                    last_accessed,
                    exported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        agent_id,
                        agent_label,
                        row["node_id"],
                        row["text"],
                        row["search_text"],
                        row["node_type"],
                        float(row["trust_useful"] or 0.0),
                        float(row["salience"] or 0.0),
                        float(row["centrality"] or 0.0),
                        row["emotion_label"] or "neutral",
                        float(row["emotion_score"] or 0.0),
                        row["source_hint"] or "",
                        row["last_accessed"],
                        exported_at,
                    )
                    for row in rows
                ],
            )
            await db.commit()
        return len(rows)


def find_export_paths(exports_dir: Path) -> dict[str, Path]:
    """Return stable agent_id -> export DB path mappings."""
    if not exports_dir.is_dir():
        return {}
    results: dict[str, Path] = {}
    for path in sorted(exports_dir.glob("*.sqlite")):
        results[path.stem] = path
    return results


def _row_to_exported_memory(row: aiosqlite.Row) -> ExportedMemory:
    return ExportedMemory(
        agent_id=row["agent_id"],
        agent_label=row["agent_label"],
        node_id=row["node_id"],
        text=row["text"],
        search_text=row["search_text"],
        node_type=row["node_type"],
        trust_useful=float(row["trust_useful"]),
        salience=float(row["salience"]),
        centrality=float(row["centrality"]),
        emotion_label=row["emotion_label"],
        emotion_score=float(row["emotion_score"]),
        source_hint=row["source_hint"],
        last_accessed=row["last_accessed"],
        exported_at=row["exported_at"],
    )


async def _ensure_export_schema(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    current_version = int(row[0] or 0) if row else 0
    if current_version != EXPORT_SCHEMA_VERSION:
        await db.execute("DROP TABLE IF EXISTS exported_nodes")

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS exported_nodes (
            agent_id TEXT NOT NULL,
            agent_label TEXT NOT NULL,
            node_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            search_text TEXT NOT NULL,
            node_type TEXT NOT NULL,
            trust_useful REAL NOT NULL,
            salience REAL NOT NULL,
            centrality REAL NOT NULL,
            emotion_label TEXT NOT NULL,
            emotion_score REAL NOT NULL,
            source_hint TEXT NOT NULL,
            last_accessed TEXT NOT NULL,
            exported_at TEXT NOT NULL
        )
        """
    )
    await db.execute(f"PRAGMA user_version = {EXPORT_SCHEMA_VERSION}")
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_exported_nodes_search
        ON exported_nodes(search_text)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_exported_nodes_source_hint
        ON exported_nodes(source_hint)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_exported_nodes_rank
        ON exported_nodes(trust_useful, salience, centrality)
        """
    )
    await db.commit()
