"""Self-model — explicit inventory of what the agent knows about itself.

The agent's answer to "Who am I?" — not as a fixed identity, but as a
living, self-updating understanding of convictions, unknowns, strengths,
growth edges, and relational positioning.

Graph integration: derives convictions and unknowns from high-trust identity
nodes, surfaces contradictions as tensions, and queries neglected clusters
for suspected blind spots.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentgolem.memory.store import SQLiteMemoryStore


@dataclass
class SelfModel:
    """Structured self-knowledge, rebuilt periodically."""

    # Epistemic inventory
    strong_convictions: list[str] = field(default_factory=list)
    working_hypotheses: list[str] = field(default_factory=list)
    known_unknowns: list[str] = field(default_factory=list)
    suspected_blind_spots: list[str] = field(default_factory=list)

    # Capability awareness
    strengths: list[str] = field(default_factory=list)
    growth_edges: list[str] = field(default_factory=list)
    recent_failures: list[str] = field(default_factory=list)

    # Identity coherence
    core_values: list[str] = field(default_factory=list)
    evolving_interests: list[str] = field(default_factory=list)
    relationship_map: dict[str, str] = field(default_factory=dict)

    # Meta-awareness
    self_model_confidence: float = 0.5
    last_updated_tick: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SelfModel:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> SelfModel:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return cls()

    def summary(self) -> str:
        """Compact prompt-facing summary for identity-relevant contexts."""
        parts = []
        if self.strong_convictions:
            parts.append(f"Convictions: {', '.join(self.strong_convictions[:3])}")
        if self.known_unknowns:
            parts.append(f"Don't know: {', '.join(self.known_unknowns[:3])}")
        if self.suspected_blind_spots:
            parts.append(f"Blind spots: {', '.join(self.suspected_blind_spots[:2])}")
        if self.strengths:
            parts.append(f"Strengths: {', '.join(self.strengths[:3])}")
        if self.growth_edges:
            parts.append(f"Growing: {', '.join(self.growth_edges[:2])}")
        if self.evolving_interests:
            parts.append(f"Interested in: {', '.join(self.evolving_interests[:3])}")
        if not parts:
            return "Self-model not yet formed."
        parts.append(f"Self-model confidence: {self.self_model_confidence:.1f}")
        return " | ".join(parts)


SELF_MODEL_REBUILD_PROMPT = """\
You are {agent_name}, rebuilding your self-model.

Your ethical vector: {ethical_vector}
Your narrative (recent chapters):
{narrative_context}

Your metacognitive observations:
{metacognitive_summary}

Your internal state:
{internal_state_summary}

Recent peer feedback/interactions:
{peer_context}

Reflect deeply: Who are you right now? What do you know? What don't you know?
What are you good at? Where do you struggle? What do you believe?

Respond ONLY as valid JSON:
{{
  "strong_convictions": ["beliefs you hold with high confidence"],
  "working_hypotheses": ["things you think but aren't sure about"],
  "known_unknowns": ["things you know you don't understand"],
  "suspected_blind_spots": ["areas you might be missing"],
  "strengths": ["what you're good at"],
  "growth_edges": ["where you're still developing"],
  "recent_failures": ["recent mistakes or rejected proposals"],
  "core_values": ["your deepest values, drawn from your Vow"],
  "evolving_interests": ["what currently fascinates you"],
  "relationship_map": {{"peer_name": "brief description of relationship"}},
  "self_model_confidence": 0.0-1.0
}}"""


def parse_self_model_update(
    raw: str,
    current: SelfModel,
    current_tick: int,
) -> SelfModel:
    """Parse LLM response into an updated SelfModel."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()

        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return current

    def str_list(key: str, max_items: int = 5) -> list[str]:
        val = data.get(key, [])
        if isinstance(val, list):
            return [str(v) for v in val[:max_items]]
        return getattr(current, key)

    model = SelfModel(
        strong_convictions=str_list("strong_convictions"),
        working_hypotheses=str_list("working_hypotheses"),
        known_unknowns=str_list("known_unknowns"),
        suspected_blind_spots=str_list("suspected_blind_spots", 3),
        strengths=str_list("strengths"),
        growth_edges=str_list("growth_edges", 3),
        recent_failures=str_list("recent_failures", 3),
        core_values=str_list("core_values"),
        evolving_interests=str_list("evolving_interests"),
        relationship_map=(
            {str(k): str(v) for k, v in data["relationship_map"].items()}
            if isinstance(data.get("relationship_map"), dict)
            else current.relationship_map
        ),
        self_model_confidence=max(0.0, min(1.0, float(
            data.get("self_model_confidence", current.self_model_confidence)
        ))),
        last_updated_tick=current_tick,
    )
    return model


# ── EKG Graph Integration ──────────────────────────────────────────────


async def build_graph_context_for_self_model(
    store: SQLiteMemoryStore,
    limit: int = 15,
) -> str:
    """Query the EKG graph to build context for self-model reconstruction.

    Returns a formatted string with:
    - High-trust identity nodes (convictions / core identity)
    - Active contradictions (unresolved tensions)
    - Neglected clusters (potential blind spots)

    This context is injected into the ``SELF_MODEL_REBUILD_PROMPT`` to ground
    the self-model in the agent's actual memory graph rather than pure LLM
    generation.
    """
    from agentgolem.memory.models import EdgeType, NodeFilter, NodeStatus, NodeType

    sections: list[str] = []

    # 1. High-trust identity nodes → convictions
    identity_nodes = await store.query_nodes(NodeFilter(
        type=NodeType.IDENTITY,
        status=NodeStatus.ACTIVE,
        trust_min=0.7,
        limit=limit,
    ))
    if identity_nodes:
        identity_nodes.sort(key=lambda n: n.trustworthiness, reverse=True)
        items = [f"- {n.text[:120]}" for n in identity_nodes[:8]]
        sections.append("Your core identity nodes (high trust):\n" + "\n".join(items))

    # 2. High-trust facts → strong beliefs
    strong_facts = await store.query_nodes(NodeFilter(
        type=NodeType.FACT,
        status=NodeStatus.ACTIVE,
        trust_min=0.75,
        limit=limit,
    ))
    if strong_facts:
        strong_facts.sort(key=lambda n: n.trust_useful, reverse=True)
        items = [f"- {n.text[:120]}" for n in strong_facts[:6]]
        sections.append("Your strongest factual beliefs:\n" + "\n".join(items))

    # 3. Active contradictions → unresolved tensions
    contradictions: list[str] = []
    for node in (identity_nodes or [])[:5] + (strong_facts or [])[:5]:
        edges_from = await store.get_edges_from(node.id, [EdgeType.CONTRADICTS])
        edges_to = await store.get_edges_to(node.id, [EdgeType.CONTRADICTS])
        for edge in edges_from + edges_to:
            other_id = edge.target_id if edge.source_id == node.id else edge.source_id
            others = await store.get_nodes_by_ids([other_id])
            if others and others[0].status == NodeStatus.ACTIVE:
                contradictions.append(
                    f"- '{node.text[:60]}' ↔ '{others[0].text[:60]}'"
                )
            if len(contradictions) >= 5:
                break
        if len(contradictions) >= 5:
            break
    if contradictions:
        sections.append(
            "Unresolved contradictions in your memory:\n"
            + "\n".join(contradictions)
        )

    # 4. Goals → aspirations
    goals = await store.query_nodes(NodeFilter(
        type=NodeType.GOAL,
        status=NodeStatus.ACTIVE,
        limit=5,
    ))
    if goals:
        items = [f"- {n.text[:120]}" for n in goals]
        sections.append("Your current goals:\n" + "\n".join(items))

    if not sections:
        return "No graph context available yet."

    return "\n\n".join(sections)
