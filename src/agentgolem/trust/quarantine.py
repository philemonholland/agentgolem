"""Quarantine / suspicion module for memory clusters.

Clusters with high emotion but low trust-useful scores are suspicious and
should be quarantined.  Quarantined memory remains stored but is NOT treated
as canonical knowledge.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentgolem.logging.audit import AuditLogger
    from agentgolem.memory.store import SQLiteMemoryStore

from agentgolem.memory.models import MemoryCluster, NodeStatus

from datetime import datetime


class QuarantineManager:
    """Evaluate, quarantine, and release suspicious memory clusters."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        audit: AuditLogger | None = None,
        *,
        emotion_threshold: float = 0.7,
        trust_useful_threshold: float = 0.3,
    ) -> None:
        self._store = store
        self._audit = audit
        self.emotion_threshold = emotion_threshold
        self.trust_useful_threshold = trust_useful_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate_cluster(self, cluster_id: str) -> bool:
        """Return *True* if the cluster is suspicious (high emotion, low trust)."""
        cluster = await self._store.get_cluster(cluster_id)
        if cluster is None:
            return False
        return (
            cluster.emotion_score > self.emotion_threshold
            and cluster.trust_useful < self.trust_useful_threshold
        )

    async def quarantine(self, cluster_id: str, reason: str) -> None:
        """Mark a cluster as quarantined."""
        await self._store.update_cluster(
            cluster_id, contradiction_status="quarantined"
        )
        self._log("quarantine", cluster_id, reason)

    async def release(self, cluster_id: str, reason: str) -> None:
        """Release a cluster from quarantine."""
        await self._store.update_cluster(
            cluster_id, contradiction_status="none"
        )
        self._log("quarantine_release", cluster_id, reason)

    async def get_quarantined(self) -> list[MemoryCluster]:
        """Return all clusters currently quarantined."""
        db = self._store._db  # noqa: SLF001 – internal access for query
        async with db.execute(
            "SELECT id FROM clusters WHERE contradiction_status = 'quarantined'"
        ) as cur:
            rows = await cur.fetchall()

        results: list[MemoryCluster] = []
        for row in rows:
            cluster = await self._store.get_cluster(row["id"])
            if cluster is not None:
                results.append(cluster)
        return results

    async def evaluate_and_quarantine(self, cluster_id: str) -> bool:
        """Evaluate and auto-quarantine if suspicious. Returns whether quarantined."""
        suspicious = await self.evaluate_cluster(cluster_id)
        if suspicious:
            await self.quarantine(cluster_id, "auto: high emotion, low trust")
        return suspicious

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _log(self, mutation_type: str, target_id: str, reason: str) -> None:
        if self._audit is not None:
            self._audit.log(mutation_type, target_id, {"reason": reason})
