"""Memory retrieval — query and neighborhood traversal."""
from __future__ import annotations

from collections import deque
from typing import Any

from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryEdge,
    NodeFilter,
    NodeStatus,
    NodeUpdate,
)
from agentgolem.memory.store import SQLiteMemoryStore


class MemoryRetriever:
    def __init__(self, store: SQLiteMemoryStore) -> None:
        self._store = store

    async def retrieve(
        self, query: str, top_k: int = 10, status: NodeStatus = NodeStatus.ACTIVE
    ) -> list[ConceptualNode]:
        """Retrieve nodes matching a text query, ranked by trust_useful.

        Uses keyword matching (text_contains) since we don't have embeddings yet.
        Ranking: sort by trust_useful descending (NOT emotion).
        """
        words = query.split()
        all_results: dict[str, ConceptualNode] = {}

        for word in words:
            if len(word) < 3:
                continue
            nodes = await self._store.query_nodes(
                NodeFilter(text_contains=word, status=status, limit=top_k * 3)
            )
            for node in nodes:
                all_results[node.id] = node

        # Also do a full-phrase search
        phrase_results = await self._store.query_nodes(
            NodeFilter(text_contains=query, status=status, limit=top_k)
        )
        for node in phrase_results:
            all_results[node.id] = node

        # Rank by trust_useful descending
        ranked = sorted(all_results.values(), key=lambda n: n.trust_useful, reverse=True)
        return ranked[:top_k]

    async def retrieve_neighborhood(
        self, node_id: str, depth: int = 2
    ) -> list[tuple[ConceptualNode, list[MemoryEdge]]]:
        """BFS neighborhood traversal up to given depth.

        Returns list of (node, edges_to_this_node) tuples.
        """
        visited: set[str] = set()
        result: list[tuple[ConceptualNode, list[MemoryEdge]]] = []
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])

        while queue:
            current_id, current_depth = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)

            node = await self._store.get_node(current_id)
            if node is None:
                continue

            # Get edges connecting to this node
            edges = await self._store.get_edges_from(current_id)
            edges += await self._store.get_edges_to(current_id)

            result.append((node, edges))

            if current_depth < depth:
                neighbors = await self._store.get_neighbors(current_id)
                for edge, neighbor in neighbors:
                    if neighbor.id not in visited:
                        queue.append((neighbor.id, current_depth + 1))

        return result

    async def retrieve_contradictions(
        self, node_id: str
    ) -> list[tuple[ConceptualNode, MemoryEdge]]:
        """Find all nodes connected by CONTRADICTS edges."""
        result: list[tuple[ConceptualNode, MemoryEdge]] = []

        outgoing = await self._store.get_edges_from(node_id, [EdgeType.CONTRADICTS])
        for edge in outgoing:
            node = await self._store.get_node(edge.target_id)
            if node:
                result.append((node, edge))

        incoming = await self._store.get_edges_to(node_id, [EdgeType.CONTRADICTS])
        for edge in incoming:
            node = await self._store.get_node(edge.source_id)
            if node:
                result.append((node, edge))

        return result

    async def retrieve_supersession_chain(
        self, node_id: str
    ) -> list[ConceptualNode]:
        """Follow the SUPERSEDES chain from a node."""
        chain: list[ConceptualNode] = []
        current_id = node_id
        visited: set[str] = set()

        while current_id and current_id not in visited:
            visited.add(current_id)
            edges = await self._store.get_edges_from(current_id, [EdgeType.SUPERSEDES])
            if not edges:
                break
            target = await self._store.get_node(edges[0].target_id)
            if target:
                chain.append(target)
                current_id = target.id
            else:
                break

        return chain
