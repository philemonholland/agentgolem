"""Shared overlay store for cross-agent memory entanglement."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

MYCELIUM_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MemoryReference:
    """Stable reference to a memory owned by a specific agent."""

    agent_id: str
    node_id: str


@dataclass(frozen=True)
class EntangledReference:
    """Foreign memory reached through the shared mycelium overlay."""

    reference: MemoryReference
    weight: float
    link_kind: str
    confidence: float


class MyceliumStore:
    """Persistent overlay for read-only cross-agent entanglements."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
        self._db = None

    async def upsert_entanglement(
        self,
        ref_a: MemoryReference,
        ref_b: MemoryReference,
        *,
        weight_delta: float = 0.1,
        link_kind: str = "resonance",
        confidence: float = 0.5,
        phase: str = "sleep",
    ) -> int:
        """Create or reinforce an undirected entanglement between two memory refs."""
        if ref_a == ref_b:
            return 0

        agent_a_id, node_a_id, agent_b_id, node_b_id = _canonicalize_pair(ref_a, ref_b)
        now = datetime.now(timezone.utc).isoformat()
        db = await self._get_db()

        async with db.execute(
            """
            SELECT id, weight, confidence
            FROM entanglements
            WHERE agent_a_id = ? AND node_a_id = ? AND agent_b_id = ? AND node_b_id = ?
            """,
            (agent_a_id, node_a_id, agent_b_id, node_b_id),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            await db.execute(
                """
                INSERT INTO entanglements (
                    id,
                    agent_a_id,
                    node_a_id,
                    agent_b_id,
                    node_b_id,
                    weight,
                    link_kind,
                    confidence,
                    created_at,
                    last_seen,
                    last_reinforced_at,
                    created_during_phase
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    agent_a_id,
                    node_a_id,
                    agent_b_id,
                    node_b_id,
                    _clamp_weight(weight_delta),
                    link_kind,
                    min(max(confidence, 0.0), 1.0),
                    now,
                    now,
                    now,
                    phase,
                ),
            )
            await db.commit()
            return 1

        new_weight = _clamp_weight(float(row["weight"]) + weight_delta)
        new_confidence = max(float(row["confidence"]), min(max(confidence, 0.0), 1.0))
        await db.execute(
            """
            UPDATE entanglements
            SET weight = ?,
                link_kind = ?,
                confidence = ?,
                last_seen = ?,
                last_reinforced_at = ?
            WHERE id = ?
            """,
            (
                new_weight,
                link_kind,
                new_confidence,
                now,
                now,
                row["id"],
            ),
        )
        await db.commit()
        return 1

    async def get_entangled_refs_for_local_nodes(
        self,
        agent_id: str,
        node_ids: list[str],
        *,
        limit: int = 20,
    ) -> list[EntangledReference]:
        """Return foreign refs entangled with any of the given local nodes."""
        if not node_ids:
            return []

        placeholders = ", ".join("?" for _ in node_ids)
        params = [agent_id, *node_ids, agent_id, *node_ids, limit * 4]
        sql = f"""
            SELECT
                agent_a_id,
                node_a_id,
                agent_b_id,
                node_b_id,
                weight,
                link_kind,
                confidence
            FROM entanglements
            WHERE (
                agent_a_id = ? AND node_a_id IN ({placeholders})
            ) OR (
                agent_b_id = ? AND node_b_id IN ({placeholders})
            )
            ORDER BY weight DESC, confidence DESC
            LIMIT ?
        """
        db = await self._get_db()
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()

        aggregated: dict[tuple[str, str], EntangledReference] = {}
        for row in rows:
            if row["agent_a_id"] == agent_id:
                foreign = MemoryReference(row["agent_b_id"], row["node_b_id"])
            else:
                foreign = MemoryReference(row["agent_a_id"], row["node_a_id"])
            key = (foreign.agent_id, foreign.node_id)
            current = aggregated.get(key)
            candidate = EntangledReference(
                reference=foreign,
                weight=float(row["weight"]),
                link_kind=row["link_kind"],
                confidence=float(row["confidence"]),
            )
            if current is None or candidate.weight > current.weight:
                aggregated[key] = candidate

        ranked = sorted(
            aggregated.values(),
            key=lambda item: (item.weight, item.confidence),
            reverse=True,
        )
        return ranked[:limit]

    async def get_entanglements_for_agent(
        self,
        agent_id: str,
        *,
        local_node_ids: set[str] | None = None,
        limit: int = 500,
    ) -> list[dict[str, str | float]]:
        """Return entanglement rows touching the given agent for UI rendering."""
        db = await self._get_db()
        async with db.execute(
            """
            SELECT *
            FROM entanglements
            WHERE agent_a_id = ? OR agent_b_id = ?
            ORDER BY weight DESC, confidence DESC
            LIMIT ?
            """,
            (agent_id, agent_id, limit),
        ) as cur:
            rows = await cur.fetchall()

        results: list[dict[str, str | float]] = []
        for row in rows:
            if local_node_ids is not None:
                if row["agent_a_id"] == agent_id and row["node_a_id"] not in local_node_ids:
                    if row["agent_b_id"] != agent_id or row["node_b_id"] not in local_node_ids:
                        continue
                if row["agent_b_id"] == agent_id and row["node_b_id"] not in local_node_ids:
                    if row["agent_a_id"] != agent_id or row["node_a_id"] not in local_node_ids:
                        continue

            results.append(
                {
                    "agent_a_id": row["agent_a_id"],
                    "node_a_id": row["node_a_id"],
                    "agent_b_id": row["agent_b_id"],
                    "node_b_id": row["node_b_id"],
                    "weight": float(row["weight"]),
                    "link_kind": row["link_kind"],
                    "confidence": float(row["confidence"]),
                }
            )
        return results

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
            await _ensure_schema(self._db)
        return self._db


async def _ensure_schema(db: aiosqlite.Connection) -> None:
    await db.execute(f"PRAGMA user_version = {MYCELIUM_SCHEMA_VERSION}")
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS entanglements (
            id TEXT PRIMARY KEY,
            agent_a_id TEXT NOT NULL,
            node_a_id TEXT NOT NULL,
            agent_b_id TEXT NOT NULL,
            node_b_id TEXT NOT NULL,
            weight REAL NOT NULL,
            link_kind TEXT NOT NULL,
            confidence REAL NOT NULL,
            created_at TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            last_reinforced_at TEXT NOT NULL,
            created_during_phase TEXT NOT NULL
        )
        """
    )
    await db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_entanglement_pair
        ON entanglements(agent_a_id, node_a_id, agent_b_id, node_b_id)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_entanglement_agent_a
        ON entanglements(agent_a_id, node_a_id)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_entanglement_agent_b
        ON entanglements(agent_b_id, node_b_id)
        """
    )
    await db.commit()


def _canonicalize_pair(
    ref_a: MemoryReference, ref_b: MemoryReference
) -> tuple[str, str, str, str]:
    pair = sorted(
        [(ref_a.agent_id, ref_a.node_id), (ref_b.agent_id, ref_b.node_id)]
    )
    return pair[0][0], pair[0][1], pair[1][0], pair[1][1]


def _clamp_weight(weight: float) -> float:
    return max(0.01, min(weight, 5.0))
