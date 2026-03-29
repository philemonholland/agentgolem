"""Bayesian trust‑update engine for memory nodes."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agentgolem.memory.models import NodeType, NodeUpdate

if TYPE_CHECKING:
    from agentgolem.logging.audit import AuditLogger
    from agentgolem.memory.models import Source
    from agentgolem.memory.store import SQLiteMemoryStore

logger = logging.getLogger(__name__)

TYPE_PRIORS: dict[NodeType, float] = {
    NodeType.FACT: 0.5,
    NodeType.PREFERENCE: 0.8,
    NodeType.EVENT: 0.6,
    NodeType.GOAL: 0.7,
    NodeType.RISK: 0.4,
    NodeType.INTERPRETATION: 0.35,
    NodeType.IDENTITY: 0.9,
    NodeType.RULE: 0.5,
    NodeType.ASSOCIATION: 0.3,
    NodeType.PROCEDURE: 0.6,
}

_CLAMP_LO = 0.01
_CLAMP_HI = 0.99


class BayesianTrustUpdater:
    """Applies Bayesian odds‑ratio updates to node trustworthiness."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        audit: AuditLogger | None = None,
    ) -> None:
        self._store = store
        self._audit = audit

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def update_trust(
        self,
        node_id: str,
        source: Source,
        confirms: bool,
    ) -> float:
        """Update trustworthiness of *node_id* given evidence from *source*.

        Returns the new (clamped) trustworthiness value.
        """
        node = await self._store.get_node(node_id)
        if node is None:
            raise ValueError(f"Node {node_id!r} not found")

        p_old = node.trustworthiness
        r = source.reliability

        # Odds form of Bayes' rule
        odds = p_old / (1.0 - p_old)
        lr = r / (1.0 - r) if confirms else (1.0 - r) / r

        discount = await self.get_independence_discount(node_id, source)
        lr_adj = lr ** discount

        odds_new = odds * lr_adj
        p_new = odds_new / (1.0 + odds_new)
        p_new = max(_CLAMP_LO, min(_CLAMP_HI, p_new))

        # Persist
        await self._store.update_node(
            node_id, NodeUpdate(trustworthiness=p_new)
        )

        # Compute trust_useful for logging (base_usefulness * new trustworthiness)
        trust_useful = node.base_usefulness * p_new
        logger.debug(
            "trust_update node=%s p=%.4f→%.4f trust_useful=%.4f",
            node_id, p_old, p_new, trust_useful,
        )

        # Audit trail
        if self._audit is not None:
            self._audit.log(
                "trust_update",
                node_id,
                {
                    "source_id": source.id,
                    "confirms": confirms,
                    "reliability": r,
                    "discount": discount,
                    "p_old": p_old,
                    "p_new": p_new,
                    "trust_useful": trust_useful,
                },
                diff=f"trustworthiness: {p_old:.4f} → {p_new:.4f}",
            )

        return p_new

    async def get_independence_discount(
        self,
        node_id: str,
        source: Source,
    ) -> float:
        """Return an exponential‑decay discount for correlated sources.

        If *source.independence_group* is empty the source is treated as
        fully independent and the discount is ``1.0``.  Otherwise the
        discount is ``0.5 ** n`` where *n* is the number of sources
        already linked to the node that share the same independence group.
        """
        group = source.independence_group
        if not group:
            return 1.0

        existing = await self._store.get_node_sources(node_id)
        n = sum(1 for s in existing if s.independence_group == group)
        return 0.5 ** n
