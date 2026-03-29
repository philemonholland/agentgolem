"""Memory mutation operations — merge, supersede, contradict."""
from __future__ import annotations

from typing import Any

from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryEdge,
    NodeStatus,
    NodeType,
    NodeUpdate,
    Source,
)
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.logging.audit import AuditLogger


class MemoryMutator:
    def __init__(
        self,
        store: SQLiteMemoryStore,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._store = store
        self._audit = audit_logger

    async def merge_nodes(
        self, node_ids: list[str], merged_text: str, evidence: Source
    ) -> ConceptualNode:
        """Merge multiple nodes into one.

        - Creates a new merged node
        - Adds SAME_AS edges from merged node to all originals
        - Archives the original nodes
        - Combines trust: average of originals' trustworthiness
        - Combines usefulness: max of originals' base_usefulness
        """
        originals: list[ConceptualNode] = []
        for nid in node_ids:
            node = await self._store.get_node(nid)
            if node:
                originals.append(node)

        if not originals:
            raise ValueError("No valid nodes to merge")

        avg_trust = sum(n.trustworthiness for n in originals) / len(originals)
        max_useful = max(n.base_usefulness for n in originals)

        # Use the most common type among originals
        type_counts: dict[NodeType, int] = {}
        for n in originals:
            type_counts[n.type] = type_counts.get(n.type, 0) + 1
        merged_type = max(type_counts, key=type_counts.get)  # type: ignore[arg-type]

        merged = ConceptualNode(
            text=merged_text,
            type=merged_type,
            base_usefulness=max_useful,
            trustworthiness=avg_trust,
        )
        await self._store.add_node(merged)

        # Store evidence source
        await self._store.add_source(evidence)
        await self._store.link_node_source(merged.id, evidence.id)

        # Add SAME_AS edges and archive originals
        for original in originals:
            edge = MemoryEdge(
                source_id=merged.id,
                target_id=original.id,
                edge_type=EdgeType.SAME_AS,
            )
            await self._store.add_edge(edge)
            await self._store.update_node(
                original.id, NodeUpdate(status=NodeStatus.ARCHIVED)
            )

        if self._audit:
            self._audit.log(
                mutation_type="merge_nodes",
                target_id=merged.id,
                evidence={
                    "merged_from": node_ids,
                    "merged_text": merged_text,
                    "avg_trust": avg_trust,
                    "max_useful": max_useful,
                    "source": evidence.id,
                },
            )

        return merged

    async def supersede(
        self, old_id: str, new_id: str, evidence: Source
    ) -> MemoryEdge:
        """Mark new node as superseding old node.

        - Adds SUPERSEDES edge from new to old
        - Reduces old node's usefulness by 50%
        """
        old_node = await self._store.get_node(old_id)
        new_node = await self._store.get_node(new_id)
        if not old_node or not new_node:
            raise ValueError("Both nodes must exist")

        edge = MemoryEdge(
            source_id=new_id,
            target_id=old_id,
            edge_type=EdgeType.SUPERSEDES,
        )
        await self._store.add_edge(edge)

        reduced = max(0.0, old_node.base_usefulness * 0.5)
        await self._store.update_node(old_id, NodeUpdate(base_usefulness=reduced))

        await self._store.add_source(evidence)
        await self._store.link_node_source(new_id, evidence.id)

        if self._audit:
            self._audit.log(
                mutation_type="supersede",
                target_id=new_id,
                evidence={
                    "superseded": old_id,
                    "old_usefulness": old_node.base_usefulness,
                    "new_usefulness": reduced,
                    "source": evidence.id,
                },
            )

        return edge

    async def mark_contradiction(
        self, node_a_id: str, node_b_id: str, evidence: Source
    ) -> MemoryEdge:
        """Mark two nodes as contradicting each other.

        - Adds CONTRADICTS edge
        - Flags any cluster containing either node
        """
        a = await self._store.get_node(node_a_id)
        b = await self._store.get_node(node_b_id)
        if not a or not b:
            raise ValueError("Both nodes must exist")

        edge = MemoryEdge(
            source_id=node_a_id,
            target_id=node_b_id,
            edge_type=EdgeType.CONTRADICTS,
        )
        await self._store.add_edge(edge)

        await self._store.add_source(evidence)

        if self._audit:
            self._audit.log(
                mutation_type="mark_contradiction",
                target_id=f"{node_a_id}<->{node_b_id}",
                evidence={
                    "node_a": node_a_id,
                    "node_b": node_b_id,
                    "node_a_text": a.text,
                    "node_b_text": b.text,
                    "source": evidence.id,
                },
            )

        return edge

    async def update_cluster_membership(
        self,
        cluster_id: str,
        add_ids: list[str] | None = None,
        remove_ids: list[str] | None = None,
    ) -> None:
        """Add or remove nodes from a cluster."""
        if add_ids:
            for nid in add_ids:
                await self._store.add_cluster_member(cluster_id, nid)
        if remove_ids:
            for nid in remove_ids:
                await self._store.remove_cluster_member(cluster_id, nid)

        if self._audit:
            self._audit.log(
                mutation_type="update_cluster_membership",
                target_id=cluster_id,
                evidence={"added": add_ids or [], "removed": remove_ids or []},
            )
