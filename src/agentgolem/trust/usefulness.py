"""Usefulness scoring for memory nodes."""
from __future__ import annotations

from typing import Any

from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import ConceptualNode, NodeUpdate
from agentgolem.memory.store import SQLiteMemoryStore


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class UsefulnessScorer:
    """Adjusts and computes usefulness scores for memory nodes."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        audit: AuditLogger | None = None,
    ) -> None:
        self._store = store
        self._audit = audit

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def bump_retrieval(self, node_id: str) -> float:
        """Bump base_usefulness by +0.01 on retrieval. Returns new value."""
        return await self._adjust(node_id, delta=0.01, mutation="usefulness_bump_retrieval")

    async def bump_task_success(self, node_id: str) -> float:
        """Bump base_usefulness by +0.05 on task success. Returns new value."""
        return await self._adjust(node_id, delta=0.05, mutation="usefulness_bump_task_success")

    async def penalize_misleading(self, node_id: str) -> float:
        """Penalize base_usefulness by -0.10 for misleading info. Returns new value."""
        return await self._adjust(node_id, delta=-0.10, mutation="usefulness_penalize_misleading")

    # ------------------------------------------------------------------
    # Read-only computations
    # ------------------------------------------------------------------

    def compute_trust_useful(self, node: ConceptualNode) -> float:
        """Return base_usefulness * trustworthiness."""
        return node.base_usefulness * node.trustworthiness

    async def batch_recompute(self, node_ids: list[str]) -> dict[str, float]:
        """Compute trust_useful for each node without mutating anything."""
        results: dict[str, float] = {}
        for nid in node_ids:
            node = await self._store.get_node(nid)
            if node is not None:
                results[nid] = self.compute_trust_useful(node)
        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _adjust(self, node_id: str, *, delta: float, mutation: str) -> float:
        node = await self._store.get_node(node_id)
        if node is None:
            raise ValueError(f"Node {node_id!r} not found")

        before = node.base_usefulness
        after = _clamp(before + delta)

        await self._store.update_node(node_id, NodeUpdate(base_usefulness=after))
        self._log(mutation, node_id, before, after)
        return after

    def _log(
        self,
        mutation_type: str,
        target_id: str,
        before: float,
        after: float,
    ) -> None:
        if self._audit is not None:
            self._audit.log(
                mutation_type,
                target_id,
                {"before": before, "after": after},
                diff=f"{before:.4f} -> {after:.4f}",
            )
