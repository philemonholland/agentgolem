"""Archive / purge / promote retention pipeline for the memory graph."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from agentgolem.memory.models import NodeStatus, NodeUpdate

if TYPE_CHECKING:
    from agentgolem.logging.audit import AuditLogger
    from agentgolem.memory.store import SQLiteMemoryStore


class RetentionManager:
    """Lifecycle manager that archives stale nodes, purges old archives,
    and promotes high-value nodes to canonical status."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        audit: AuditLogger | None = None,
        *,
        archive_days: int = 30,
        purge_days: int = 90,
        min_trust_useful: float = 0.1,
        min_centrality: float = 0.05,
        promote_min_accesses: int = 10,
        promote_min_trust_useful: float = 0.5,
    ) -> None:
        self._store = store
        self._audit = audit
        self.archive_days = archive_days
        self.purge_days = purge_days
        self.min_trust_useful = min_trust_useful
        self.min_centrality = min_centrality
        self.promote_min_accesses = promote_min_accesses
        self.promote_min_trust_useful = promote_min_trust_useful

    # ------------------------------------------------------------------
    # Archive
    # ------------------------------------------------------------------

    async def archive_candidates(self) -> list[str]:
        """Return node ids eligible for archiving."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.archive_days)).isoformat()
        sql = """
            SELECT id FROM nodes
            WHERE status = ?
              AND canonical = 0
              AND centrality < ?
              AND (base_usefulness * trustworthiness) < ?
              AND last_accessed < ?
              AND access_count < ?
        """
        params = (
            NodeStatus.ACTIVE.value,
            self.min_centrality,
            self.min_trust_useful,
            cutoff,
            self.promote_min_accesses,
        )
        async with self._store._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [row["id"] for row in rows]

    async def archive(self, node_ids: list[str]) -> int:
        """Set nodes to ARCHIVED status. Returns count of archived nodes."""
        count = 0
        for nid in node_ids:
            await self._store.update_node(nid, NodeUpdate(status=NodeStatus.ARCHIVED))
            self._log("retention_archive", nid, {"reason": "low_value_stale"})
            count += 1
        return count

    # ------------------------------------------------------------------
    # Purge
    # ------------------------------------------------------------------

    async def purge_candidates(self) -> list[str]:
        """Return archived node ids eligible for purging (soft-delete)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.purge_days)).isoformat()

        # Archived nodes old enough to consider
        base_sql = """
            SELECT id FROM nodes
            WHERE status = ?
              AND canonical = 0
              AND last_accessed < ?
        """
        async with self._store._db.execute(
            base_sql, (NodeStatus.ARCHIVED.value, cutoff)
        ) as cur:
            rows = await cur.fetchall()
        candidate_ids = [row["id"] for row in rows]

        if not candidate_ids:
            return []

        result: list[str] = []
        for nid in candidate_ids:
            if await self._is_protected(nid):
                continue
            result.append(nid)
        return result

    async def purge(self, node_ids: list[str]) -> int:
        """Soft-delete nodes by setting status to PURGED. Returns count."""
        count = 0
        for nid in node_ids:
            await self._store.update_node(nid, NodeUpdate(status=NodeStatus.PURGED))
            self._log("retention_purge", nid, {"reason": "expired_archive"})
            count += 1
        return count

    # ------------------------------------------------------------------
    # Promote
    # ------------------------------------------------------------------

    async def promote_candidates(self) -> list[str]:
        """Return active non-canonical node ids that deserve promotion."""
        sql = """
            SELECT id FROM nodes
            WHERE status = ?
              AND canonical = 0
              AND access_count >= ?
              AND (base_usefulness * trustworthiness) >= ?
              AND centrality >= ?
        """
        params = (
            NodeStatus.ACTIVE.value,
            self.promote_min_accesses,
            self.promote_min_trust_useful,
            self.min_centrality,
        )
        async with self._store._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [row["id"] for row in rows]

    async def promote(self, node_ids: list[str]) -> int:
        """Set nodes to canonical. Returns count of promoted nodes."""
        count = 0
        for nid in node_ids:
            await self._store.update_node(nid, NodeUpdate(canonical=True))
            self._log("retention_promote", nid, {"reason": "high_value"})
            count += 1
        return count

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _is_protected(self, node_id: str) -> bool:
        """Check whether a node must be shielded from purging."""
        # 1. Nodes involved in unresolved contradictions
        contra_sql = """
            SELECT 1 FROM edges
            WHERE edge_type = 'contradicts'
              AND (source_id = ? OR target_id = ?)
            LIMIT 1
        """
        async with self._store._db.execute(contra_sql, (node_id, node_id)) as cur:
            if await cur.fetchone() is not None:
                return True

        # 2. Nodes with a niscalajyoti source (ethical anchor)
        nj_sql = """
            SELECT 1 FROM node_sources ns
            JOIN sources s ON ns.source_id = s.id
            WHERE ns.node_id = ? AND s.kind = ?
            LIMIT 1
        """
        async with self._store._db.execute(
            nj_sql, (node_id, "niscalajyoti")
        ) as cur:
            if await cur.fetchone() is not None:
                return True

        # 3. Nodes whose sources are recent (within purge window)
        recent_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self.purge_days)
        ).isoformat()
        recent_sql = """
            SELECT 1 FROM node_sources ns
            JOIN sources s ON ns.source_id = s.id
            WHERE ns.node_id = ? AND s.timestamp > ?
            LIMIT 1
        """
        async with self._store._db.execute(
            recent_sql, (node_id, recent_cutoff)
        ) as cur:
            if await cur.fetchone() is not None:
                return True

        return False

    def _log(self, mutation_type: str, target_id: str, evidence: dict) -> None:
        if self._audit is not None:
            self._audit.log(mutation_type, target_id, evidence)
