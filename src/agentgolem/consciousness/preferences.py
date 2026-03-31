"""Preference & Stance Memory — persistent opinions that define identity.

Agents develop and remember **opinions** that persist across cycles.
Preferences are stored as PREFERENCE nodes in the EKG with extra metadata
encoded in the text and usefulness scores.

Key behaviors:
- Crystallization: repeated patterns in metacognition → preference nodes
- Retrieval: top preferences by usefulness injected into prompts
- Reinforcement: consistent behavior strengthens preferences
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC

from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryEdge,
    NodeFilter,
    NodeStatus,
    NodeType,
    NodeUpdate,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CRYSTALLIZATION_THRESHOLD: int = 3
"""Number of repeated focus ticks before a preference crystallizes."""

REINFORCEMENT_BUMP: float = 0.05
"""Usefulness bump when an action is consistent with a preference."""

PENALTY_BUMP: float = 0.03
"""Usefulness penalty when an action contradicts a preference."""

MIN_PREFERENCE_USEFULNESS: float = 0.3
"""Below this threshold, a preference is considered weakened/fading."""

MAX_PREFERENCES: int = 20
"""Maximum number of active preferences per agent."""

PREFERENCE_DOMAINS = ("ethics", "methodology", "aesthetics", "relationships", "epistemology")


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class PreferenceCandidate:
    """A candidate preference detected from repeated patterns."""

    stance: str
    domain: str
    evidence: str  # what triggered crystallization
    strength: float = 0.5


# ---------------------------------------------------------------------------
# Crystallization — detect when patterns should become preferences
# ---------------------------------------------------------------------------

def detect_preference_candidates(
    recent_curiosity_focuses: list[str],
    recent_growth_vectors: list[str],
    existing_preference_texts: list[str],
) -> list[PreferenceCandidate]:
    """Detect repeated patterns that should crystallize into preferences.

    Checks:
    - Same curiosity_focus appearing 3+ times (→ "I value exploring X")
    - Same growth_vector repeated across 2+ cycles (→ "I aim toward X")
    """
    candidates: list[PreferenceCandidate] = []

    # Count curiosity focus repetitions
    focus_counts: dict[str, int] = {}
    for focus in recent_curiosity_focuses:
        if focus:
            key = focus.lower().strip()
            focus_counts[key] = focus_counts.get(key, 0) + 1

    for focus, count in focus_counts.items():
        if count >= CRYSTALLIZATION_THRESHOLD:
            text = f"I value exploring {focus}"
            if not _already_exists(text, existing_preference_texts):
                candidates.append(PreferenceCandidate(
                    stance=text,
                    domain="epistemology",
                    evidence=f"Focused on '{focus}' {count} times",
                    strength=min(0.5 + count * 0.05, 0.9),
                ))

    # Count growth vector repetitions
    growth_counts: dict[str, int] = {}
    for vector in recent_growth_vectors:
        if vector:
            key = vector.lower().strip()
            growth_counts[key] = growth_counts.get(key, 0) + 1

    for vector, count in growth_counts.items():
        if count >= 2:
            text = f"I aim to grow toward {vector}"
            if not _already_exists(text, existing_preference_texts):
                candidates.append(PreferenceCandidate(
                    stance=text,
                    domain="methodology",
                    evidence=f"Growth vector '{vector}' sustained across {count} cycles",
                    strength=min(0.5 + count * 0.05, 0.9),
                ))

    return candidates


def _already_exists(new_text: str, existing_texts: list[str]) -> bool:
    """Check if a preference with similar wording already exists."""
    new_lower = new_text.lower()
    for existing in existing_texts:
        if existing.lower() in new_lower or new_lower in existing.lower():
            return True
    return False


# ---------------------------------------------------------------------------
# Node Construction
# ---------------------------------------------------------------------------

def build_preference_node(candidate: PreferenceCandidate) -> ConceptualNode:
    """Create a ConceptualNode of type PREFERENCE from a candidate."""
    now = datetime.now(UTC)
    return ConceptualNode(
        text=candidate.stance,
        type=NodeType.PREFERENCE,
        search_text=f"{candidate.domain}: {candidate.stance}",
        base_usefulness=candidate.strength,
        trustworthiness=0.7,
        salience=candidate.strength,
        emotion_label="conviction",
        emotion_score=candidate.strength,
        created_at=now,
        last_accessed=now,
        access_count=0,
        canonical=True,
    )


def build_evidence_edge(
    preference_node_id: str,
    evidence_node_id: str,
) -> MemoryEdge:
    """Create a DERIVED_FROM edge from preference to its evidence."""
    return MemoryEdge(
        source_id=preference_node_id,
        target_id=evidence_node_id,
        edge_type=EdgeType.DERIVED_FROM,
        weight=0.8,
        confidence=0.7,
    )


# ---------------------------------------------------------------------------
# Retrieval — get top preferences for prompt injection
# ---------------------------------------------------------------------------

async def retrieve_top_preferences(
    store: object,
    top_k: int = 5,
) -> list[ConceptualNode]:
    """Retrieve the agent's top preferences by usefulness.

    Uses query_nodes (no access bump) for retrieval.
    """
    filt = NodeFilter(
        type=NodeType.PREFERENCE,
        status=NodeStatus.ACTIVE,
        usefulness_min=MIN_PREFERENCE_USEFULNESS,
        limit=top_k * 2,  # fetch extra to sort
    )
    try:
        nodes = await store.query_nodes(filt)  # type: ignore[union-attr]
    except Exception:
        return []

    # Sort by trust_useful descending
    nodes.sort(key=lambda n: n.trust_useful, reverse=True)
    return nodes[:top_k]


def format_preferences_for_prompt(preferences: list[ConceptualNode]) -> str:
    """Format preference nodes into a compact prompt injection block."""
    if not preferences:
        return ""

    lines = ["Your crystallized stances:"]
    for pref in preferences:
        strength = "strong" if pref.base_usefulness >= 0.7 else "moderate"
        lines.append(f"- [{strength}] {pref.text}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reinforcement — strengthen or weaken preferences
# ---------------------------------------------------------------------------

def compute_reinforcement(
    preference_text: str,
    response_text: str,
) -> float:
    """Compute how much a response reinforces or weakens a preference.

    Returns positive value for reinforcement, negative for inconsistency.
    Simple keyword/phrase overlap heuristic (no LLM call).
    """
    # Extract key concepts from preference
    pref_words = set(preference_text.lower().split())
    resp_lower = response_text.lower()

    # Remove common stop words
    stop_words = {"i", "a", "the", "to", "of", "in", "and", "or", "is", "am", "my", "that"}
    pref_keywords = pref_words - stop_words

    if not pref_keywords:
        return 0.0

    # Count how many preference keywords appear in the response
    matches = sum(1 for kw in pref_keywords if kw in resp_lower)
    overlap_ratio = matches / len(pref_keywords) if pref_keywords else 0.0

    if overlap_ratio >= 0.3:
        return REINFORCEMENT_BUMP
    return 0.0  # neutral — no penalty unless explicitly contradicted


async def reinforce_preference(
    store: object,
    node_id: str,
    delta: float,
) -> None:
    """Adjust a preference's usefulness score."""
    try:
        node = await store.get_node(node_id)  # type: ignore[union-attr]
        if node is None:
            return
        new_usefulness = max(0.0, min(1.0, node.base_usefulness + delta))
        await store.update_node(  # type: ignore[union-attr]
            node_id,
            NodeUpdate(base_usefulness=new_usefulness),
        )
    except Exception:
        pass
