"""Read-only export layer for cross-agent memory access."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from agentgolem.memory.store import SQLiteMemoryStore

EXPORT_SCHEMA_VERSION = 1
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
        exported_at = datetime.now(timezone.utc).isoformat()

        async with self._store._db.execute(
            """
            SELECT
                id AS node_id,
                text,
                COALESCE(search_text, '') AS search_text,
                type AS node_type,
                (base_usefulness * trustworthiness) AS trust_useful,
                salience,
                centrality,
                emotion_label,
                emotion_score,
                last_accessed
            FROM nodes
            WHERE status = 'active'
            ORDER BY
                (base_usefulness * trustworthiness) DESC,
                salience DESC,
                centrality DESC,
                last_accessed DESC
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
                    last_accessed,
                    exported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        last_accessed=row["last_accessed"],
        exported_at=row["exported_at"],
    )


async def _ensure_export_schema(db: aiosqlite.Connection) -> None:
    await db.execute(f"PRAGMA user_version = {EXPORT_SCHEMA_VERSION}")
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
            last_accessed TEXT NOT NULL,
            exported_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_exported_nodes_search
        ON exported_nodes(search_text)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_exported_nodes_rank
        ON exported_nodes(trust_useful, salience, centrality)
        """
    )
    await db.commit()
