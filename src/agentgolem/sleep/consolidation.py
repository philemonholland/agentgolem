"""Consolidation engine — proposes merges, abstractions, and contradiction chains.

All proposals are *advisory*; the engine never auto-applies mutations.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentgolem.llm.base import LLMClient
from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import EdgeType, NodeType
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.sleep.walker import WalkResult


# ------------------------------------------------------------------ data models


@dataclass
class MergeProposal:
    node_ids: list[str]
    proposed_text: str
    reason: str
    confidence: float = 0.5


@dataclass
class AbstractionProposal:
    source_node_ids: list[str]
    proposed_text: str
    proposed_type: str  # NodeType value
    reason: str
    confidence: float = 0.5


@dataclass
class ContradictionChain:
    pairs: list[tuple[str, str]]  # (node_a_id, node_b_id)
    severity: float = 0.5


# ------------------------------------------------------------------ engine

_MERGE_EDGE_TYPES = [EdgeType.SAME_AS, EdgeType.MERGE_CANDIDATE]
_CLUSTER_EDGE_TYPES = [EdgeType.RELATED_TO, EdgeType.SUPPORTS]


class ConsolidationEngine:
    """Propose memory-graph consolidations without applying them."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        audit: AuditLogger,
        llm: LLMClient | None = None,
        state_path: Path | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._llm = llm
        self._state_path = state_path or Path(".")

    def process(self, walk_results: list[WalkResult]) -> list[dict[str, Any]]:
        """Synchronous pass-through: collect proposed actions from walk results."""
        actions: list[dict[str, Any]] = []
        for wr in walk_results:
            actions.extend(wr.proposed_actions)
        return actions

    # -------------------------------------------------------------- merges

    async def propose_merges(self, walk_result: WalkResult) -> list[MergeProposal]:
        """Find SAME_AS / MERGE_CANDIDATE edges in the walk and propose merges."""
        proposals: list[MergeProposal] = []
        seen_pairs: set[tuple[str, str]] = set()

        for node_id in walk_result.visited_node_ids:
            edges = await self._store.get_edges_from(node_id, _MERGE_EDGE_TYPES)
            edges += await self._store.get_edges_to(node_id, _MERGE_EDGE_TYPES)

            for edge in edges:
                # Only consider edges that appear in the walk's edge_activations
                if edge.id not in walk_result.edge_activations:
                    continue

                pair = tuple(sorted([edge.source_id, edge.target_id]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                src_node = await self._store.get_node(edge.source_id)
                tgt_node = await self._store.get_node(edge.target_id)
                if src_node is None or tgt_node is None:
                    continue

                proposed_text = f"{src_node.text} | {tgt_node.text}"
                proposal = MergeProposal(
                    node_ids=[edge.source_id, edge.target_id],
                    proposed_text=proposed_text,
                    reason=f"Linked by {edge.edge_type.value} edge",
                    confidence=edge.weight,
                )
                proposals.append(proposal)

                self._audit.log(
                    "consolidation_merge_proposal",
                    edge.id,
                    {
                        "node_ids": proposal.node_ids,
                        "edge_type": edge.edge_type.value,
                        "proposed_text": proposed_text,
                    },
                )

        return proposals

    # -------------------------------------------------------------- abstractions

    async def propose_abstractions(
        self, walk_result: WalkResult
    ) -> list[AbstractionProposal]:
        """Propose higher-level abstractions when 3+ related nodes cluster."""
        proposals: list[AbstractionProposal] = []
        visited = set(walk_result.visited_node_ids)

        # Build adjacency among visited nodes using RELATED_TO / SUPPORTS edges
        adj: dict[str, set[str]] = {nid: set() for nid in visited}
        for node_id in visited:
            edges = await self._store.get_edges_from(node_id, _CLUSTER_EDGE_TYPES)
            for edge in edges:
                if edge.target_id in visited:
                    adj[node_id].add(edge.target_id)
                    adj[edge.target_id].add(node_id)

        # Greedily collect connected components of size >= 3
        remaining = set(visited)
        while remaining:
            seed = remaining.pop()
            component: list[str] = [seed]
            queue = list(adj.get(seed, set()) & remaining)
            while queue:
                nid = queue.pop()
                if nid in remaining:
                    remaining.discard(nid)
                    component.append(nid)
                    queue.extend(adj.get(nid, set()) & remaining)

            if len(component) < 3:
                continue

            texts: list[str] = []
            for nid in component:
                node = await self._store.get_node(nid)
                if node:
                    texts.append(node.text)

            proposed_text = "Abstraction of: " + ", ".join(texts)
            proposal = AbstractionProposal(
                source_node_ids=component,
                proposed_text=proposed_text,
                proposed_type=NodeType.ASSOCIATION.value,
                reason=f"Cluster of {len(component)} related nodes",
                confidence=0.5,
            )
            proposals.append(proposal)

            self._audit.log(
                "consolidation_abstraction_proposal",
                component[0],
                {
                    "source_node_ids": component,
                    "proposed_text": proposed_text,
                },
            )

        return proposals

    # -------------------------------------------------------------- contradictions

    async def surface_contradictions(
        self, walk_result: WalkResult
    ) -> list[ContradictionChain]:
        """Surface chains of CONTRADICTS edges among walk-visited nodes."""
        visited = set(walk_result.visited_node_ids)
        pairs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for node_id in visited:
            edges = await self._store.get_edges_from(
                node_id, [EdgeType.CONTRADICTS]
            )
            edges += await self._store.get_edges_to(
                node_id, [EdgeType.CONTRADICTS]
            )
            for edge in edges:
                if edge.source_id in visited and edge.target_id in visited:
                    key = tuple(sorted([edge.source_id, edge.target_id]))
                    if key not in seen:
                        seen.add(key)
                        pairs.append((edge.source_id, edge.target_id))

        if not pairs:
            return []

        # Build chains via union-find on contradicting pairs
        chains = self._build_chains(pairs)

        results: list[ContradictionChain] = []
        for chain_pairs in chains:
            severity = min(1.0, len(chain_pairs) * 0.3 + 0.2)
            chain = ContradictionChain(pairs=chain_pairs, severity=severity)
            results.append(chain)

            self._audit.log(
                "consolidation_contradiction_surfaced",
                chain_pairs[0][0],
                {"pairs": chain_pairs, "severity": severity},
            )

        return results

    # -------------------------------------------------------------- queue helpers

    def queue_for_heartbeat(
        self,
        items: list[MergeProposal | AbstractionProposal | ContradictionChain],
    ) -> None:
        """Append items to the consolidation queue on disk."""
        queue_path = self._state_path / "consolidation_queue.json"
        existing: list[dict[str, Any]] = []
        if queue_path.exists():
            existing = json.loads(queue_path.read_text(encoding="utf-8"))

        for item in items:
            entry = asdict(item)
            entry["_type"] = type(item).__name__
            existing.append(entry)

        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text(
            json.dumps(existing, default=str), encoding="utf-8"
        )

    def get_queue(self) -> list[dict[str, Any]]:
        """Read the consolidation queue."""
        queue_path = self._state_path / "consolidation_queue.json"
        if not queue_path.exists():
            return []
        return json.loads(queue_path.read_text(encoding="utf-8"))

    def clear_queue(self) -> None:
        """Clear the consolidation queue."""
        queue_path = self._state_path / "consolidation_queue.json"
        if queue_path.exists():
            queue_path.unlink()

    # -------------------------------------------------------------- internals

    @staticmethod
    def _build_chains(
        pairs: list[tuple[str, str]],
    ) -> list[list[tuple[str, str]]]:
        """Group contradiction pairs into connected chains (union-find)."""
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for a, b in pairs:
            parent.setdefault(a, a)
            parent.setdefault(b, b)
            union(a, b)

        groups: dict[str, list[tuple[str, str]]] = {}
        for a, b in pairs:
            root = find(a)
            groups.setdefault(root, []).append((a, b))

        return list(groups.values())
