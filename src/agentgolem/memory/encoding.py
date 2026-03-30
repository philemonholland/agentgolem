"""Memory encoding — input → conceptual memories pipeline."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from agentgolem.llm.base import LLMClient, Message
from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryCluster,
    MemoryEdge,
    NodeFilter,
    NodeType,
    Source,
    SourceKind,
)
from agentgolem.memory.store import SQLiteMemoryStore

# Trust priors by node type
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

# Short words to skip when building keyword searches
_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should may might can could of in to for on with "
    "at by from as into through during before after above below and or "
    "but not no nor so yet both each every all any few more most other "
    "some such than too very it its this that these those i me my we "
    "our they them their he she him her who what which where when how".split()
)


def _extract_keywords(text: str, max_words: int = 5) -> list[str]:
    """Extract significant keywords from text for graph search."""
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return [w for w in words if w not in _STOP_WORDS][:max_words]


class DecomposedConcept(BaseModel):
    text: str
    type: str  # will be mapped to NodeType


class DecompositionResult(BaseModel):
    concepts: list[DecomposedConcept]


class ComparisonDecision(BaseModel):
    decision: str  # "new_node", "keep_exact", "keep_both", "merge_candidate", "supersedes", "contradicts"
    existing_node_id: str = ""
    reason: str = ""


class BatchComparisonResult(BaseModel):
    decisions: list[ComparisonDecision]


class MemoryEncoder:
    def __init__(
        self,
        store: SQLiteMemoryStore,
        llm: LLMClient,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._store = store
        self._llm = llm
        self._audit = audit_logger

    async def encode(self, input_text: str, source: Source) -> list[ConceptualNode]:
        """Full encoding pipeline: decompose → classify → compare → store → link."""
        # Step 1-2: Decompose and classify
        concepts = await self._decompose(input_text)

        # Store source
        await self._store.add_source(source)

        # Step 3: Map types
        node_types = [self._map_type(c.type) for c in concepts]

        # Step 4: Find related existing nodes for ALL concepts at once
        all_keywords: set[str] = set()
        for concept in concepts:
            all_keywords.update(_extract_keywords(concept.text))
        existing = await self._store.search_nodes_by_keywords(
            list(all_keywords), limit=20
        ) if all_keywords else []

        # Step 5: Batch-decide actions for all concepts in one LLM call
        decisions = await self._compare_batch(concepts, existing)

        # Step 6: Create or update based on decisions
        created_nodes: list[ConceptualNode] = []
        for concept, node_type, decision in zip(concepts, node_types, decisions):
            node = await self._apply_decision(concept, node_type, decision, source)
            if node:
                created_nodes.append(node)

        # Step 7: Link nodes from this batch to each other (RELATED_TO)
        await self._link_batch(created_nodes)

        # Step 8: Build cluster if multiple nodes
        if len(created_nodes) > 1:
            cluster = MemoryCluster(
                label=input_text[:60],
                node_ids=[n.id for n in created_nodes],
                source_ids=[source.id],
            )
            cluster_id = await self._store.add_cluster(cluster)
            for n in created_nodes:
                await self._store.add_cluster_member(cluster_id, n.id)
            await self._store.link_cluster_source(cluster_id, source.id)

        # Log
        if self._audit:
            self._audit.log(
                mutation_type="memory_encode",
                target_id=source.id,
                evidence={
                    "input_length": len(input_text),
                    "concepts_found": len(concepts),
                    "nodes_created": len(created_nodes),
                    "source_kind": source.kind.value,
                },
            )

        return created_nodes

    async def _decompose(self, text: str) -> list[DecomposedConcept]:
        """Use LLM to decompose text into atomic concepts."""
        prompt = (
            "Decompose the following text into atomic conceptual memories. "
            "Each concept should be 3-15 words. Classify each as one of: "
            "fact, preference, event, goal, risk, interpretation, identity, rule, association, procedure.\n\n"
            f"Text: {text}\n\n"
            'Respond with JSON: {{"concepts": [{{"text": "...", "type": "..."}}]}}'
        )
        result = await self._llm.complete_structured(
            [Message(role="user", content=prompt)],
            DecompositionResult,
            timeout=120.0,
        )
        return result.concepts

    def _map_type(self, type_str: str) -> NodeType:
        """Map a string type to NodeType enum."""
        try:
            return NodeType(type_str.lower())
        except ValueError:
            return NodeType.FACT  # default fallback

    async def _compare_batch(
        self,
        concepts: list[DecomposedConcept],
        existing: list[ConceptualNode],
    ) -> list[ComparisonDecision]:
        """Compare ALL candidate concepts against existing nodes in one LLM call."""
        if not existing:
            return [ComparisonDecision(decision="new_node") for _ in concepts]

        existing_descriptions = "\n".join(
            f"- [{n.id}] {n.text} (type={n.type.value}, trust={n.trustworthiness:.2f})"
            for n in existing
        )
        concept_list = "\n".join(
            f"  {i + 1}. \"{c.text}\" (type={c.type})"
            for i, c in enumerate(concepts)
        )
        prompt = (
            f"New concepts to store:\n{concept_list}\n\n"
            f"Existing nodes in memory:\n{existing_descriptions}\n\n"
            f"For EACH new concept, decide: new_node, keep_exact (identical exists), "
            f"keep_both (similar but different), merge_candidate (should merge), "
            f"supersedes (new replaces old), contradicts (conflicts).\n"
            f"Return one decision per concept in order.\n"
            f'Respond with JSON: {{"decisions": [{{"decision": "...", "existing_node_id": "...", "reason": "..."}}]}}'
        )
        try:
            result = await self._llm.complete_structured(
                [Message(role="user", content=prompt)],
                BatchComparisonResult,
                timeout=120.0,
            )
            decisions = result.decisions
            # Pad or trim to match concept count
            while len(decisions) < len(concepts):
                decisions.append(ComparisonDecision(decision="new_node"))
            return decisions[: len(concepts)]
        except Exception:
            # Fallback: treat everything as new
            return [ComparisonDecision(decision="new_node") for _ in concepts]

    async def _apply_decision(
        self,
        concept: DecomposedConcept,
        node_type: NodeType,
        decision: ComparisonDecision,
        source: Source,
    ) -> ConceptualNode | None:
        """Create/update nodes based on comparison decision."""
        trust_prior = TYPE_PRIORS.get(node_type, 0.5)

        if decision.decision == "keep_exact":
            # Node already exists, just link source
            if decision.existing_node_id:
                await self._store.link_node_source(decision.existing_node_id, source.id)
            return None

        # Create new node for: new_node, keep_both, merge_candidate, supersedes, contradicts
        node = ConceptualNode(
            text=concept.text,
            type=node_type,
            base_usefulness=trust_prior,
            trustworthiness=trust_prior,
        )
        await self._store.add_node(node)
        await self._store.link_node_source(node.id, source.id)

        # Add relationship edges based on decision
        if decision.existing_node_id:
            edge_type_map = {
                "supersedes": EdgeType.SUPERSEDES,
                "contradicts": EdgeType.CONTRADICTS,
                "merge_candidate": EdgeType.MERGE_CANDIDATE,
                "keep_both": EdgeType.RELATED_TO,
            }
            edge_type = edge_type_map.get(decision.decision)
            if edge_type:
                edge = MemoryEdge(
                    source_id=node.id,
                    target_id=decision.existing_node_id,
                    edge_type=edge_type,
                )
                await self._store.add_edge(edge)

        return node

    async def _link_batch(self, nodes: list[ConceptualNode]) -> None:
        """Connect nodes from the same encoding batch with RELATED_TO edges.

        Uses a lightweight sequential chain (A→B→C→D) rather than a full mesh
        to keep edge count linear while ensuring the batch is fully connected.
        """
        for i in range(len(nodes) - 1):
            edge = MemoryEdge(
                source_id=nodes[i].id,
                target_id=nodes[i + 1].id,
                edge_type=EdgeType.RELATED_TO,
                weight=0.5,
            )
            await self._store.add_edge(edge)
