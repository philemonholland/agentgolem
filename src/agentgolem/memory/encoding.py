"""Memory encoding — EKG-inspired input → multi-level memory graph pipeline."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from agentgolem.llm.base import LLMClient, Message
from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryCluster,
    MemoryEdge,
    NodeType,
    NodeUpdate,
    Source,
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

_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should may might can could of in to for on with "
    "at by from as into through during before after above below and or "
    "but not no nor so yet both each every all any few more most other "
    "some such than too very it its this that these those i me my we "
    "our they them their he she him her who what which where when how".split()
)

_DEFAULT_RELATION_WEIGHT = 0.7


def _extract_keywords(text: str, max_words: int = 6) -> list[str]:
    """Extract significant keywords from text for graph search."""
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return [w for w in words if w not in _STOP_WORDS][:max_words]


def _normalize_text_key(text: str) -> str:
    """Normalize free text into a stable merge/matching key."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return " ".join(words[:12])


@dataclass
class _AppliedConcept:
    concept: DecomposedConcept
    node: ConceptualNode
    created: bool


class DecomposedConcept(BaseModel):
    text: str
    type: str
    search_text: str = ""
    salience: float = 0.5
    emotion_label: str = "neutral"
    emotion_score: float = 0.0


class DecompositionRelation(BaseModel):
    source_text: str
    target_text: str
    edge_type: str
    weight: float = _DEFAULT_RELATION_WEIGHT


class DecompositionView(BaseModel):
    label: str = ""
    concepts: list[DecomposedConcept] = Field(default_factory=list)
    relations: list[DecompositionRelation] = Field(default_factory=list)


class DecompositionResult(BaseModel):
    grounded_view: DecompositionView = Field(default_factory=DecompositionView)
    semantic_view: DecompositionView = Field(default_factory=DecompositionView)


class ComparisonDecision(BaseModel):
    decision: str  # new_node, keep_exact, keep_both, merge_candidate, supersedes, contradicts
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
        """Encode text into a richer multi-level memory graph."""
        concepts, relations, cluster_label = await self._decompose(input_text)
        if not concepts:
            return []

        await self._store.add_source(source)

        node_types = [self._map_type(c.type) for c in concepts]
        existing = await self._collect_existing_candidates(concepts)
        decisions = await self._compare_batch(concepts, existing)

        applied: list[_AppliedConcept] = []
        for concept, node_type, decision in zip(concepts, node_types, decisions):
            result = await self._apply_decision(concept, node_type, decision, source)
            if result is not None:
                applied.append(result)

        resolved_nodes = [entry.node for entry in applied]
        if not resolved_nodes:
            return []

        await self._apply_relations(applied, relations)
        if len(resolved_nodes) > 1 and not relations:
            await self._ensure_batch_connectivity(resolved_nodes)

        if len(resolved_nodes) > 1:
            cluster = self._build_cluster(
                label=cluster_label or input_text[:60],
                nodes=resolved_nodes,
                source_id=source.id,
            )
            cluster_id = await self._store.add_cluster(cluster)
            for node in resolved_nodes:
                await self._store.add_cluster_member(cluster_id, node.id)
            await self._store.link_cluster_source(cluster_id, source.id)

        created_nodes = [entry.node for entry in applied if entry.created]
        if self._audit:
            self._audit.log(
                mutation_type="memory_encode",
                target_id=source.id,
                evidence={
                    "input_length": len(input_text),
                    "concepts_found": len(concepts),
                    "nodes_created": len(created_nodes),
                    "nodes_resolved": len(resolved_nodes),
                    "relations_created": len(relations),
                    "source_origin": source.origin,
                },
            )

        return created_nodes

    async def _decompose(
        self, text: str
    ) -> tuple[list[DecomposedConcept], list[DecompositionRelation], str]:
        """Build two graph views of the same input, then reconcile them."""
        prompt = (
            "Build a robust memory graph for the following text using TWO complementary views.\n\n"
            "View 1 (grounded_view): extract direct source-grounded memory claims.\n"
            "View 2 (semantic_view): extract thematic/relational memory claims and relations.\n\n"
            "Rules:\n"
            "- Each claim must express one clean idea.\n"
            "- Claims may be longer than 15 words if needed.\n"
            "- search_text should be a short retrieval projection (keywords or compact paraphrase).\n"
            "- salience is a float from 0.0 to 1.0.\n"
            "- emotion_score is a float from -1.0 to 1.0.\n"
            "- relation edge_type must be one of: related_to, part_of, supports, contradicts, supersedes, same_as, merge_candidate, derived_from.\n"
            "- relation source_text and target_text must reference claims from the same view.\n\n"
            f"Text:\n{text}"
        )
        result = await self._llm.complete_structured(
            [Message(role="user", content=prompt)],
            DecompositionResult,
            timeout=120.0,
        )
        return self._reconcile_views(result, text)

    def _reconcile_views(
        self, result: DecompositionResult, original_text: str
    ) -> tuple[list[DecomposedConcept], list[DecompositionRelation], str]:
        """Merge grounded + semantic views into one consistent candidate graph."""
        merged: dict[str, DecomposedConcept] = {}
        alias_to_key: dict[str, str] = {}
        counts: dict[str, int] = {}
        labels: list[str] = []

        for view in (result.grounded_view, result.semantic_view):
            if view.label:
                labels.append(view.label.strip())
            for concept in view.concepts:
                key = self._concept_key(concept)
                if not key:
                    continue
                alias_to_key[_normalize_text_key(concept.text)] = key
                if concept.search_text:
                    alias_to_key[_normalize_text_key(concept.search_text)] = key

                existing = merged.get(key)
                if existing is None:
                    merged[key] = concept.model_copy(deep=True)
                else:
                    merged[key] = self._merge_concepts(existing, concept)
                counts[key] = counts.get(key, 0) + 1

        for key, concept in merged.items():
            if counts.get(key, 0) > 1:
                concept.salience = min(1.0, concept.salience + 0.15)
            if not concept.search_text:
                concept.search_text = " ".join(_extract_keywords(concept.text))

        relation_map: dict[tuple[str, str, str], float] = {}
        relation_counts: dict[tuple[str, str, str], int] = {}
        for view in (result.grounded_view, result.semantic_view):
            for relation in view.relations:
                source_key = alias_to_key.get(_normalize_text_key(relation.source_text))
                target_key = alias_to_key.get(_normalize_text_key(relation.target_text))
                if not source_key or not target_key or source_key == target_key:
                    continue

                edge_type = self._map_edge_type(relation.edge_type)
                rel_key = (source_key, target_key, edge_type.value)
                relation_map[rel_key] = max(
                    relation_map.get(rel_key, 0.0),
                    min(max(relation.weight, 0.1), 1.0),
                )
                relation_counts[rel_key] = relation_counts.get(rel_key, 0) + 1

        resolved_relations: list[DecompositionRelation] = []
        for (source_key, target_key, edge_type), weight in relation_map.items():
            count = relation_counts[(source_key, target_key, edge_type)]
            boosted_weight = min(1.0, weight + 0.1 if count > 1 else weight)
            resolved_relations.append(
                DecompositionRelation(
                    source_text=merged[source_key].text,
                    target_text=merged[target_key].text,
                    edge_type=edge_type,
                    weight=boosted_weight,
                )
            )

        cluster_label = next((label for label in labels if label), original_text[:60])
        return list(merged.values()), resolved_relations, cluster_label[:120]

    def _merge_concepts(
        self, current: DecomposedConcept, incoming: DecomposedConcept
    ) -> DecomposedConcept:
        """Merge duplicate ideas from multiple extraction views."""
        chosen_text = incoming.text if len(incoming.text) > len(current.text) else current.text
        chosen_search = current.search_text or incoming.search_text
        salience = max(current.salience, incoming.salience)

        chosen_type = current.type
        if incoming.salience > current.salience:
            chosen_type = incoming.type

        emotion = current
        if abs(incoming.emotion_score) > abs(current.emotion_score):
            emotion = incoming

        return DecomposedConcept(
            text=chosen_text,
            type=chosen_type,
            search_text=chosen_search,
            salience=salience,
            emotion_label=emotion.emotion_label,
            emotion_score=emotion.emotion_score,
        )

    def _concept_key(self, concept: DecomposedConcept) -> str:
        basis = concept.search_text or concept.text
        return _normalize_text_key(basis)

    async def _collect_existing_candidates(
        self, concepts: list[DecomposedConcept]
    ) -> list[ConceptualNode]:
        all_keywords: set[str] = set()
        for concept in concepts:
            all_keywords.update(_extract_keywords(concept.text))
            all_keywords.update(_extract_keywords(concept.search_text))

        if not all_keywords:
            return []

        existing = await self._store.search_nodes_by_keywords(
            list(all_keywords), limit=60
        )
        return await self._rank_existing_candidates(existing)

    async def _rank_existing_candidates(
        self, candidates: list[ConceptualNode]
    ) -> list[ConceptualNode]:
        """Apply dynamic-attention-style ranking to candidate memory nodes."""
        now = datetime.now(timezone.utc)
        scored: list[tuple[float, ConceptualNode]] = []

        for node in candidates:
            age_days = max(
                0.0,
                (now - node.last_accessed).total_seconds() / 86400.0,
            )
            recency_score = 1.0 / (1.0 + (age_days / 7.0))
            source_quality = await self._average_source_reliability(node.id)
            score = (
                (0.35 * node.trust_useful)
                + (0.20 * node.centrality)
                + (0.15 * recency_score)
                + (0.10 * abs(node.emotion_score))
                + (0.10 * node.salience)
                + (0.10 * source_quality)
            )
            scored.append((score, node))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [node for _, node in scored[:20]]

    async def _average_source_reliability(self, node_id: str) -> float:
        sources = await self._store.get_node_sources(node_id)
        if not sources:
            return 0.5
        return sum(source.reliability for source in sources) / len(sources)

    def _map_type(self, type_str: str) -> NodeType:
        """Map a string type to NodeType enum."""
        try:
            return NodeType(type_str.lower())
        except ValueError:
            return NodeType.FACT

    def _map_edge_type(self, type_str: str) -> EdgeType:
        """Map relation text to a valid edge type."""
        try:
            return EdgeType(type_str.lower())
        except ValueError:
            return EdgeType.RELATED_TO

    async def _compare_batch(
        self,
        concepts: list[DecomposedConcept],
        existing: list[ConceptualNode],
    ) -> list[ComparisonDecision]:
        """Compare all candidate concepts against existing nodes in one call."""
        if not existing:
            return [ComparisonDecision(decision="new_node") for _ in concepts]

        existing_descriptions = "\n".join(
            (
                f"- [{node.id}] {node.text} "
                f"(search={node.search_text or '(none)'}, "
                f"type={node.type.value}, trust={node.trustworthiness:.2f}, "
                f"salience={node.salience:.2f})"
            )
            for node in existing
        )
        concept_list = "\n".join(
            (
                f'  {i + 1}. "{concept.text}" '
                f"(search={concept.search_text or '(none)'}, "
                f"type={concept.type}, salience={concept.salience:.2f})"
            )
            for i, concept in enumerate(concepts)
        )
        prompt = (
            f"New memory claims to store:\n{concept_list}\n\n"
            f"Existing nodes in memory:\n{existing_descriptions}\n\n"
            "For EACH new claim, decide one of: "
            "new_node, keep_exact, keep_both, merge_candidate, supersedes, contradicts.\n"
            "Use keep_exact only for materially identical claims.\n"
            "Use keep_both when both claims should coexist.\n"
            "Return one decision per claim in order."
        )
        try:
            result = await self._llm.complete_structured(
                [Message(role="user", content=prompt)],
                BatchComparisonResult,
                timeout=120.0,
            )
            decisions = result.decisions
            while len(decisions) < len(concepts):
                decisions.append(ComparisonDecision(decision="new_node"))
            return decisions[: len(concepts)]
        except Exception:
            return [ComparisonDecision(decision="new_node") for _ in concepts]

    async def _apply_decision(
        self,
        concept: DecomposedConcept,
        node_type: NodeType,
        decision: ComparisonDecision,
        source: Source,
    ) -> _AppliedConcept | None:
        """Create/update nodes based on comparison decision."""
        trust_prior = TYPE_PRIORS.get(node_type, 0.5)

        if decision.decision == "keep_exact" and decision.existing_node_id:
            existing = await self._store.get_node(decision.existing_node_id)
            if existing is None:
                return None

            await self._store.link_node_source(existing.id, source.id)
            updates = NodeUpdate()
            dirty = False
            if concept.search_text and not existing.search_text:
                updates.search_text = concept.search_text
                dirty = True
            if concept.salience > existing.salience:
                updates.salience = concept.salience
                dirty = True
            if abs(concept.emotion_score) > abs(existing.emotion_score):
                updates.emotion_label = concept.emotion_label
                updates.emotion_score = concept.emotion_score
                dirty = True
            if dirty:
                await self._store.update_node(existing.id, updates)
                refreshed = await self._store.get_node(existing.id)
                if refreshed is not None:
                    existing = refreshed
            return _AppliedConcept(concept=concept, node=existing, created=False)

        node = ConceptualNode(
            text=concept.text,
            search_text=concept.search_text,
            type=node_type,
            base_usefulness=trust_prior,
            trustworthiness=trust_prior,
            salience=concept.salience,
            emotion_label=concept.emotion_label,
            emotion_score=concept.emotion_score,
        )
        await self._store.add_node(node)
        await self._store.link_node_source(node.id, source.id)

        if decision.existing_node_id:
            existing_node = await self._store.get_node(decision.existing_node_id)
            if existing_node is not None:
                edge_type_map = {
                    "supersedes": EdgeType.SUPERSEDES,
                    "contradicts": EdgeType.CONTRADICTS,
                    "merge_candidate": EdgeType.MERGE_CANDIDATE,
                    "keep_both": EdgeType.RELATED_TO,
                }
                edge_type = edge_type_map.get(decision.decision)
                if edge_type is not None:
                    await self._store.add_edge(
                        MemoryEdge(
                            source_id=node.id,
                            target_id=decision.existing_node_id,
                            edge_type=edge_type,
                        )
                    )

        return _AppliedConcept(concept=concept, node=node, created=True)

    async def _apply_relations(
        self,
        applied: list[_AppliedConcept],
        relations: list[DecompositionRelation],
    ) -> None:
        """Persist relation-aware edges from the reconciled batch graph."""
        node_by_alias: dict[str, ConceptualNode] = {}
        for entry in applied:
            node_by_alias[_normalize_text_key(entry.concept.text)] = entry.node
            if entry.concept.search_text:
                node_by_alias[_normalize_text_key(entry.concept.search_text)] = entry.node

        for relation in relations:
            source_node = node_by_alias.get(_normalize_text_key(relation.source_text))
            target_node = node_by_alias.get(_normalize_text_key(relation.target_text))
            if source_node is None or target_node is None or source_node.id == target_node.id:
                continue

            await self._store.add_edge(
                MemoryEdge(
                    source_id=source_node.id,
                    target_id=target_node.id,
                    edge_type=self._map_edge_type(relation.edge_type),
                    weight=min(max(relation.weight, 0.1), 1.0),
                )
            )

    async def _ensure_batch_connectivity(self, nodes: list[ConceptualNode]) -> None:
        """Fallback connectivity when the model produces no explicit relations."""
        for i in range(len(nodes) - 1):
            await self._store.add_edge(
                MemoryEdge(
                    source_id=nodes[i].id,
                    target_id=nodes[i + 1].id,
                    edge_type=EdgeType.RELATED_TO,
                    weight=0.5,
                )
            )

    def _build_cluster(
        self, label: str, nodes: list[ConceptualNode], source_id: str
    ) -> MemoryCluster:
        """Create a cluster representing one encoding batch."""
        trustworthiness = sum(node.trustworthiness for node in nodes) / len(nodes)
        usefulness = sum(node.base_usefulness for node in nodes) / len(nodes)
        emotion_node = max(nodes, key=lambda node: abs(node.emotion_score))
        emotion_score = sum(node.emotion_score for node in nodes) / len(nodes)
        return MemoryCluster(
            label=label,
            cluster_type="encoding_batch",
            node_ids=[node.id for node in nodes],
            source_ids=[source_id],
            emotion_label=emotion_node.emotion_label,
            emotion_score=emotion_score,
            base_usefulness=usefulness,
            trustworthiness=trustworthiness,
        )
