"""Async CRUD layer for the memory graph."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

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


class SQLiteMemoryStore:
    def __init__(self, db: aiosqlite.Connection, audit_logger: Any = None) -> None:
        self._db = db
        self._audit = audit_logger

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    async def add_node(self, node: ConceptualNode) -> str:
        """Insert a node. Returns the node id."""
        await self._db.execute(
            """INSERT INTO nodes
               (id, text, type, created_at, last_accessed, access_count,
                base_usefulness, trustworthiness, emotion_label, emotion_score,
                centrality, status, canonical)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node.id,
                node.text,
                node.type.value,
                node.created_at.isoformat(),
                node.last_accessed.isoformat(),
                node.access_count,
                node.base_usefulness,
                node.trustworthiness,
                node.emotion_label,
                node.emotion_score,
                node.centrality,
                node.status.value,
                int(node.canonical),
            ),
        )
        await self._db.commit()
        self._log("add_node", node.id, {"text": node.text, "type": node.type.value})
        return node.id

    async def get_node(self, node_id: str) -> ConceptualNode | None:
        """Get a node by id. Updates last_accessed and access_count."""
        async with self._db.execute(
            "SELECT * FROM nodes WHERE id = ?", (node_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None

        now = datetime.now(timezone.utc)
        await self._db.execute(
            "UPDATE nodes SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
            (now.isoformat(), node_id),
        )
        await self._db.commit()

        node = self._row_to_node(row)
        node.last_accessed = now
        node.access_count += 1
        return node

    async def update_node(self, node_id: str, updates: NodeUpdate) -> None:
        """Apply partial updates to a node."""
        sets: list[str] = []
        params: list[Any] = []
        for fld, val in [
            ("text", updates.text),
            ("base_usefulness", updates.base_usefulness),
            ("trustworthiness", updates.trustworthiness),
            ("emotion_label", updates.emotion_label),
            ("emotion_score", updates.emotion_score),
            ("centrality", updates.centrality),
            ("access_count", updates.access_count),
        ]:
            if val is not None:
                sets.append(f"{fld} = ?")
                params.append(val)

        if updates.status is not None:
            sets.append("status = ?")
            params.append(updates.status.value)
        if updates.canonical is not None:
            sets.append("canonical = ?")
            params.append(int(updates.canonical))
        if updates.last_accessed is not None:
            sets.append("last_accessed = ?")
            params.append(updates.last_accessed.isoformat())

        if not sets:
            return

        params.append(node_id)
        await self._db.execute(
            f"UPDATE nodes SET {', '.join(sets)} WHERE id = ?",  # noqa: S608
            params,
        )
        await self._db.commit()
        self._log("update_node", node_id, {"fields": [s.split(" = ")[0] for s in sets]})

    async def query_nodes(self, filters: NodeFilter) -> list[ConceptualNode]:
        """Query nodes with flexible filtering (no access-count bump)."""
        clauses: list[str] = []
        params: list[Any] = []

        if filters.type is not None:
            clauses.append("type = ?")
            params.append(filters.type.value)
        if filters.status is not None:
            clauses.append("status = ?")
            params.append(filters.status.value)
        if filters.canonical is not None:
            clauses.append("canonical = ?")
            params.append(int(filters.canonical))
        if filters.trust_min is not None:
            clauses.append("trustworthiness >= ?")
            params.append(filters.trust_min)
        if filters.trust_max is not None:
            clauses.append("trustworthiness <= ?")
            params.append(filters.trust_max)
        if filters.usefulness_min is not None:
            clauses.append("base_usefulness >= ?")
            params.append(filters.usefulness_min)
        if filters.usefulness_max is not None:
            clauses.append("base_usefulness <= ?")
            params.append(filters.usefulness_max)
        if filters.text_contains is not None:
            clauses.append("text LIKE ?")
            params.append(f"%{filters.text_contains}%")

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM nodes{where} LIMIT ? OFFSET ?"  # noqa: S608
        params.extend([filters.limit, filters.offset])

        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_node(r) for r in rows]

    async def search_nodes_by_keywords(
        self, keywords: list[str], limit: int = 10
    ) -> list[ConceptualNode]:
        """Search active nodes matching ANY of the given keywords."""
        if not keywords:
            return []
        clauses = " OR ".join("text LIKE ?" for _ in keywords)
        params: list[Any] = [f"%{kw}%" for kw in keywords]
        params.append(limit)
        sql = (
            f"SELECT * FROM nodes WHERE status = 'active'"  # noqa: S608
            f" AND ({clauses}) LIMIT ?"
        )
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_node(r) for r in rows]

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------

    async def add_edge(self, edge: MemoryEdge) -> str:
        """Insert an edge. Returns the edge id."""
        await self._db.execute(
            """INSERT INTO edges (id, source_id, target_id, edge_type, weight, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                edge.id,
                edge.source_id,
                edge.target_id,
                edge.edge_type.value,
                edge.weight,
                edge.created_at.isoformat(),
            ),
        )
        await self._db.commit()
        self._log("add_edge", edge.id, {
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "edge_type": edge.edge_type.value,
        })
        return edge.id

    async def get_edges_from(
        self, node_id: str, edge_types: list[EdgeType] | None = None
    ) -> list[MemoryEdge]:
        """Get outgoing edges from a node."""
        if edge_types:
            placeholders = ", ".join("?" for _ in edge_types)
            sql = f"SELECT * FROM edges WHERE source_id = ? AND edge_type IN ({placeholders})"  # noqa: S608
            params: list[Any] = [node_id, *(et.value for et in edge_types)]
        else:
            sql = "SELECT * FROM edges WHERE source_id = ?"
            params = [node_id]

        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_edge(r) for r in rows]

    async def get_edges_to(
        self, node_id: str, edge_types: list[EdgeType] | None = None
    ) -> list[MemoryEdge]:
        """Get incoming edges to a node."""
        if edge_types:
            placeholders = ", ".join("?" for _ in edge_types)
            sql = f"SELECT * FROM edges WHERE target_id = ? AND edge_type IN ({placeholders})"  # noqa: S608
            params: list[Any] = [node_id, *(et.value for et in edge_types)]
        else:
            sql = "SELECT * FROM edges WHERE target_id = ?"
            params = [node_id]

        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_edge(r) for r in rows]

    async def get_neighbors(
        self, node_id: str, edge_types: list[EdgeType] | None = None
    ) -> list[tuple[MemoryEdge, ConceptualNode]]:
        """Get neighboring nodes via outgoing edges."""
        edges = await self.get_edges_from(node_id, edge_types)
        results: list[tuple[MemoryEdge, ConceptualNode]] = []
        for edge in edges:
            async with self._db.execute(
                "SELECT * FROM nodes WHERE id = ?", (edge.target_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is not None:
                results.append((edge, self._row_to_node(row)))
        return results

    async def update_edge(self, edge_id: str, weight: float) -> None:
        """Update edge weight."""
        await self._db.execute(
            "UPDATE edges SET weight = ? WHERE id = ?", (weight, edge_id)
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    async def add_source(self, source: Source) -> str:
        """Insert a source. Returns the source id."""
        await self._db.execute(
            """INSERT INTO sources
               (id, kind, origin, reliability, independence_group, timestamp, raw_reference)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                source.id,
                source.kind.value,
                source.origin,
                source.reliability,
                source.independence_group,
                source.timestamp.isoformat(),
                source.raw_reference,
            ),
        )
        await self._db.commit()
        return source.id

    async def get_source(self, source_id: str) -> Source | None:
        async with self._db.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ) as cur:
            row = await cur.fetchone()
        return self._row_to_source(row) if row else None

    async def link_node_source(self, node_id: str, source_id: str) -> None:
        """Link a node to a source (idempotent)."""
        await self._db.execute(
            "INSERT OR IGNORE INTO node_sources (node_id, source_id) VALUES (?, ?)",
            (node_id, source_id),
        )
        await self._db.commit()

    async def get_node_sources(self, node_id: str) -> list[Source]:
        """Get all sources for a node."""
        async with self._db.execute(
            """SELECT s.* FROM sources s
               JOIN node_sources ns ON s.id = ns.source_id
               WHERE ns.node_id = ?""",
            (node_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_source(r) for r in rows]

    # ------------------------------------------------------------------
    # Clusters
    # ------------------------------------------------------------------

    async def add_cluster(self, cluster: MemoryCluster) -> str:
        """Insert a cluster. Returns the cluster id."""
        await self._db.execute(
            """INSERT INTO clusters
               (id, label, cluster_type, emotion_label, emotion_score,
                base_usefulness, trustworthiness, contradiction_status,
                created_at, last_accessed, access_count, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cluster.id,
                cluster.label,
                cluster.cluster_type,
                cluster.emotion_label,
                cluster.emotion_score,
                cluster.base_usefulness,
                cluster.trustworthiness,
                cluster.contradiction_status,
                cluster.created_at.isoformat(),
                cluster.last_accessed.isoformat(),
                cluster.access_count,
                cluster.status.value,
            ),
        )
        await self._db.commit()
        self._log("add_cluster", cluster.id, {"label": cluster.label})

        for nid in cluster.node_ids:
            await self.add_cluster_member(cluster.id, nid)
        for sid in cluster.source_ids:
            await self.link_cluster_source(cluster.id, sid)

        return cluster.id

    async def get_cluster(self, cluster_id: str) -> MemoryCluster | None:
        async with self._db.execute(
            "SELECT * FROM clusters WHERE id = ?", (cluster_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None

        async with self._db.execute(
            "SELECT node_id FROM cluster_members WHERE cluster_id = ?", (cluster_id,)
        ) as cur:
            member_rows = await cur.fetchall()
        node_ids = [r["node_id"] for r in member_rows]

        async with self._db.execute(
            "SELECT source_id FROM cluster_sources WHERE cluster_id = ?", (cluster_id,)
        ) as cur:
            source_rows = await cur.fetchall()
        source_ids = [r["source_id"] for r in source_rows]

        return MemoryCluster(
            id=row["id"],
            label=row["label"],
            cluster_type=row["cluster_type"],
            emotion_label=row["emotion_label"],
            emotion_score=row["emotion_score"],
            base_usefulness=row["base_usefulness"],
            trustworthiness=row["trustworthiness"],
            contradiction_status=row["contradiction_status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_accessed=datetime.fromisoformat(row["last_accessed"]),
            access_count=row["access_count"],
            status=NodeStatus(row["status"]),
            node_ids=node_ids,
            source_ids=source_ids,
        )

    async def update_cluster(self, cluster_id: str, **kwargs: Any) -> None:
        """Update cluster fields."""
        allowed = {
            "label", "cluster_type", "emotion_label", "emotion_score",
            "base_usefulness", "trustworthiness", "contradiction_status",
            "status", "last_accessed", "access_count",
        }
        sets: list[str] = []
        params: list[Any] = []
        for key, val in kwargs.items():
            if key not in allowed:
                continue
            if key == "status" and isinstance(val, NodeStatus):
                val = val.value
            if key == "last_accessed" and isinstance(val, datetime):
                val = val.isoformat()
            sets.append(f"{key} = ?")
            params.append(val)

        if not sets:
            return

        params.append(cluster_id)
        await self._db.execute(
            f"UPDATE clusters SET {', '.join(sets)} WHERE id = ?",  # noqa: S608
            params,
        )
        await self._db.commit()
        self._log("update_cluster", cluster_id, {"fields": [s.split(" = ")[0] for s in sets]})

    async def add_cluster_member(self, cluster_id: str, node_id: str) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO cluster_members (cluster_id, node_id) VALUES (?, ?)",
            (cluster_id, node_id),
        )
        await self._db.commit()

    async def remove_cluster_member(self, cluster_id: str, node_id: str) -> None:
        await self._db.execute(
            "DELETE FROM cluster_members WHERE cluster_id = ? AND node_id = ?",
            (cluster_id, node_id),
        )
        await self._db.commit()

    async def get_cluster_nodes(self, cluster_id: str) -> list[ConceptualNode]:
        """Get all nodes in a cluster (no access-count bump)."""
        async with self._db.execute(
            """SELECT n.* FROM nodes n
               JOIN cluster_members cm ON n.id = cm.node_id
               WHERE cm.cluster_id = ?""",
            (cluster_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_node(r) for r in rows]

    async def link_cluster_source(self, cluster_id: str, source_id: str) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO cluster_sources (cluster_id, source_id) VALUES (?, ?)",
            (cluster_id, source_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def get_statistics(self) -> dict[str, Any]:
        """Get counts by status, type, etc."""
        stats: dict[str, Any] = {}

        async with self._db.execute("SELECT COUNT(*) FROM nodes") as cur:
            stats["total_nodes"] = (await cur.fetchone())[0]

        async with self._db.execute("SELECT COUNT(*) FROM edges") as cur:
            stats["total_edges"] = (await cur.fetchone())[0]

        async with self._db.execute("SELECT COUNT(*) FROM sources") as cur:
            stats["total_sources"] = (await cur.fetchone())[0]

        async with self._db.execute("SELECT COUNT(*) FROM clusters") as cur:
            stats["total_clusters"] = (await cur.fetchone())[0]

        async with self._db.execute(
            "SELECT status, COUNT(*) AS cnt FROM nodes GROUP BY status"
        ) as cur:
            stats["nodes_by_status"] = {r["status"]: r["cnt"] async for r in cur}

        async with self._db.execute(
            "SELECT type, COUNT(*) AS cnt FROM nodes GROUP BY type"
        ) as cur:
            stats["nodes_by_type"] = {r["type"]: r["cnt"] async for r in cur}

        return stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_node(self, row: aiosqlite.Row) -> ConceptualNode:
        return ConceptualNode(
            id=row["id"],
            text=row["text"],
            type=NodeType(row["type"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            last_accessed=datetime.fromisoformat(row["last_accessed"]),
            access_count=row["access_count"],
            base_usefulness=row["base_usefulness"],
            trustworthiness=row["trustworthiness"],
            emotion_label=row["emotion_label"],
            emotion_score=row["emotion_score"],
            centrality=row["centrality"],
            status=NodeStatus(row["status"]),
            canonical=bool(row["canonical"]),
        )

    def _row_to_edge(self, row: aiosqlite.Row) -> MemoryEdge:
        return MemoryEdge(
            id=row["id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            edge_type=EdgeType(row["edge_type"]),
            weight=row["weight"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _row_to_source(self, row: aiosqlite.Row) -> Source:
        return Source(
            id=row["id"],
            kind=SourceKind(row["kind"]),
            origin=row["origin"],
            reliability=row["reliability"],
            independence_group=row["independence_group"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            raw_reference=row["raw_reference"],
        )

    def _log(self, mutation_type: str, target_id: str, evidence: dict[str, Any]) -> None:
        if self._audit is not None:
            self._audit.log(mutation_type, target_id, evidence)
