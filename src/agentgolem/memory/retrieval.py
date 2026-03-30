"""Memory retrieval — query and neighborhood traversal."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any

from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryEdge,
    NodeFilter,
    NodeStatus,
)
from agentgolem.memory.store import SQLiteMemoryStore


class MemoryRetriever:
    def __init__(self, store: SQLiteMemoryStore) -> None:
        self._store = store

    async def retrieve(
        self, query: str, top_k: int = 10, status: NodeStatus = NodeStatus.ACTIVE
    ) -> list[ConceptualNode]:
        """Retrieve nodes with dynamic-attention-style ranking."""
        words = [
            word.strip()
            for word in query.split()
            if len(word.strip()) >= 3
        ]
        all_results: dict[str, ConceptualNode] = {}

        for word in words:
            nodes = await self._store.query_nodes(
                NodeFilter(text_contains=word, status=status, limit=top_k * 5)
            )
            for node in nodes:
                all_results[node.id] = node

        phrase_results = await self._store.query_nodes(
            NodeFilter(text_contains=query, status=status, limit=top_k)
        )
        for node in phrase_results:
            all_results[node.id] = node

        ranked = await self._rank_results(list(all_results.values()), query)
        return ranked[:top_k]

    async def _rank_results(
        self, nodes: list[ConceptualNode], query: str
    ) -> list[ConceptualNode]:
        """Rank retrieved nodes using query match + graph salience signals."""
        scored: list[tuple[float, ConceptualNode]] = []
        now = datetime.now(timezone.utc)
        query_words = {word.lower() for word in query.split() if len(word) >= 3}

        for node in nodes:
            searchable = f"{node.text} {node.search_text}".lower()
            keyword_hits = sum(1 for word in query_words if word in searchable)
            match_score = keyword_hits / max(len(query_words), 1)

            age_days = max(
                0.0,
                (now - node.last_accessed).total_seconds() / 86400.0,
            )
            recency_score = 1.0 / (1.0 + (age_days / 7.0))
            source_quality = await self._average_source_reliability(node.id)

            score = (
                (0.40 * match_score)
                + (0.25 * node.trust_useful)
                + (0.10 * node.centrality)
                + (0.05 * recency_score)
                + (0.10 * node.salience)
                + (0.05 * abs(node.emotion_score))
                + (0.05 * source_quality)
            )
            scored.append((score, node))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [node for _, node in scored]

    async def _average_source_reliability(self, node_id: str) -> float:
        sources = await self._store.get_node_sources(node_id)
        if not sources:
            return 0.5
        return sum(source.reliability for source in sources) / len(sources)

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
