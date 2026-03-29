"""Contradiction detection and resolution for the memory graph."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryEdge,
    NodeStatus,
    NodeType,
    NodeUpdate,
)

if TYPE_CHECKING:
    from agentgolem.llm.base import LLMClient
    from agentgolem.logging.audit import AuditLogger
    from agentgolem.memory.store import SQLiteMemoryStore


@dataclass
class ContradictionPair:
    """Two nodes linked by a CONTRADICTS edge."""

    node_a_id: str
    node_b_id: str
    edge_id: str
    severity: float = 0.5
    status: str = "unresolved"  # unresolved, resolved, deferred


class ContradictionResolver:
    """Detect and resolve contradictions in the memory graph."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        audit: AuditLogger,
        llm: LLMClient | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._llm = llm

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    async def detect_contradictions(self, node_id: str) -> list[ContradictionPair]:
        """Get all CONTRADICTS edges from/to *node_id* and return pairs."""
        ct = [EdgeType.CONTRADICTS]
        edges_out = await self._store.get_edges_from(node_id, ct)
        edges_in = await self._store.get_edges_to(node_id, ct)

        seen_edge_ids: set[str] = set()
        pairs: list[ContradictionPair] = []

        for edge in [*edges_out, *edges_in]:
            if edge.id in seen_edge_ids:
                continue
            seen_edge_ids.add(edge.id)

            node_a = await self._store.get_node(edge.source_id)
            node_b = await self._store.get_node(edge.target_id)
            if node_a is None or node_b is None:
                continue

            severity = 1.0 - min(node_a.trustworthiness, node_b.trustworthiness)
            pairs.append(
                ContradictionPair(
                    node_a_id=edge.source_id,
                    node_b_id=edge.target_id,
                    edge_id=edge.id,
                    severity=severity,
                )
            )

        return pairs

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    async def resolve(
        self,
        pair: ContradictionPair,
        resolution: Literal["keep_both", "supersede", "merge", "defer"],
    ) -> None:
        """Apply a resolution strategy to a contradiction pair."""
        if resolution == "keep_both":
            await self._resolve_keep_both(pair)
        elif resolution == "supersede":
            await self._resolve_supersede(pair)
        elif resolution == "merge":
            await self._resolve_merge(pair)
        elif resolution == "defer":
            await self._resolve_defer(pair)
        else:
            msg = f"Unknown resolution strategy: {resolution}"
            raise ValueError(msg)

    async def _resolve_keep_both(self, pair: ContradictionPair) -> None:
        pair.status = "resolved"
        self._audit.log(
            "contradiction_resolved",
            pair.edge_id,
            {
                "strategy": "keep_both",
                "node_a": pair.node_a_id,
                "node_b": pair.node_b_id,
            },
        )

    async def _resolve_supersede(self, pair: ContradictionPair) -> None:
        node_a = await self._store.get_node(pair.node_a_id)
        node_b = await self._store.get_node(pair.node_b_id)
        if node_a is None or node_b is None:
            msg = "Cannot supersede: one or both nodes missing"
            raise ValueError(msg)

        if node_a.trust_useful >= node_b.trust_useful:
            winner, loser = node_a, node_b
        else:
            winner, loser = node_b, node_a

        # Add SUPERSEDES edge from winner to loser
        await self._store.add_edge(
            MemoryEdge(
                source_id=winner.id,
                target_id=loser.id,
                edge_type=EdgeType.SUPERSEDES,
            )
        )

        # Reduce loser's usefulness by 50%
        await self._store.update_node(
            loser.id,
            NodeUpdate(base_usefulness=loser.base_usefulness * 0.5),
        )

        pair.status = "resolved"
        self._audit.log(
            "contradiction_resolved",
            pair.edge_id,
            {
                "strategy": "supersede",
                "winner": winner.id,
                "loser": loser.id,
                "node_a": pair.node_a_id,
                "node_b": pair.node_b_id,
            },
        )

    async def _resolve_merge(self, pair: ContradictionPair) -> None:
        node_a = await self._store.get_node(pair.node_a_id)
        node_b = await self._store.get_node(pair.node_b_id)
        if node_a is None or node_b is None:
            msg = "Cannot merge: one or both nodes missing"
            raise ValueError(msg)

        # Create a merged node combining text from both
        merged = ConceptualNode(
            text=f"{node_a.text} | {node_b.text}",
            type=node_a.type,
            base_usefulness=max(node_a.base_usefulness, node_b.base_usefulness),
            trustworthiness=max(node_a.trustworthiness, node_b.trustworthiness),
        )
        await self._store.add_node(merged)

        # Add SAME_AS edges from merged to both originals
        await self._store.add_edge(
            MemoryEdge(
                source_id=merged.id,
                target_id=node_a.id,
                edge_type=EdgeType.SAME_AS,
            )
        )
        await self._store.add_edge(
            MemoryEdge(
                source_id=merged.id,
                target_id=node_b.id,
                edge_type=EdgeType.SAME_AS,
            )
        )

        # Archive both originals
        await self._store.update_node(
            node_a.id, NodeUpdate(status=NodeStatus.ARCHIVED)
        )
        await self._store.update_node(
            node_b.id, NodeUpdate(status=NodeStatus.ARCHIVED)
        )

        pair.status = "resolved"
        self._audit.log(
            "contradiction_resolved",
            pair.edge_id,
            {
                "strategy": "merge",
                "merged_node": merged.id,
                "node_a": pair.node_a_id,
                "node_b": pair.node_b_id,
            },
        )

    async def _resolve_defer(self, pair: ContradictionPair) -> None:
        pair.status = "deferred"
        self._audit.log(
            "contradiction_deferred",
            pair.edge_id,
            {
                "strategy": "defer",
                "node_a": pair.node_a_id,
                "node_b": pair.node_b_id,
            },
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_unresolved(self) -> list[ContradictionPair]:
        """Return contradiction pairs where both nodes are still ACTIVE."""
        # Query ALL edges of type CONTRADICTS via a full-table approach:
        # get edges from every node that has outgoing CONTRADICTS edges.
        # We use the store's DB directly to avoid needing a "list all edges" API.
        db = self._store._db  # noqa: SLF001
        async with db.execute(
            "SELECT * FROM edges WHERE edge_type = ?",
            (EdgeType.CONTRADICTS.value,),
        ) as cur:
            rows = await cur.fetchall()

        pairs: list[ContradictionPair] = []
        for row in rows:
            node_a = await self._store.get_node(row["source_id"])
            node_b = await self._store.get_node(row["target_id"])
            if node_a is None or node_b is None:
                continue
            if node_a.status != NodeStatus.ACTIVE or node_b.status != NodeStatus.ACTIVE:
                continue

            severity = 1.0 - min(node_a.trustworthiness, node_b.trustworthiness)
            pairs.append(
                ContradictionPair(
                    node_a_id=row["source_id"],
                    node_b_id=row["target_id"],
                    edge_id=row["id"],
                    severity=severity,
                )
            )

        return pairs

    async def surface_chains(self) -> list[list[ContradictionPair]]:
        """Find chains of connected contradictions using BFS.

        If A contradicts B and B contradicts C, that forms a chain [A-B, B-C].
        Returns a list of connected components, each as a list of pairs.
        """
        all_pairs = await self.get_unresolved()
        if not all_pairs:
            return []

        # Build adjacency: node_id -> list of pairs involving that node
        adj: dict[str, list[ContradictionPair]] = defaultdict(list)
        for pair in all_pairs:
            adj[pair.node_a_id].append(pair)
            adj[pair.node_b_id].append(pair)

        visited_nodes: set[str] = set()
        components: list[list[ContradictionPair]] = []

        for pair in all_pairs:
            start = pair.node_a_id
            if start in visited_nodes:
                continue

            # BFS from start
            component_pairs: list[ContradictionPair] = []
            component_pair_ids: set[str] = set()
            queue = [start]
            visited_nodes.add(start)

            while queue:
                current = queue.pop(0)
                for p in adj[current]:
                    if p.edge_id not in component_pair_ids:
                        component_pair_ids.add(p.edge_id)
                        component_pairs.append(p)
                    other = p.node_b_id if p.node_a_id == current else p.node_a_id
                    if other not in visited_nodes:
                        visited_nodes.add(other)
                        queue.append(other)

            if component_pairs:
                components.append(component_pairs)

        return components
