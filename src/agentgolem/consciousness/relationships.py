"""Relational depth — rich inter-agent relationship modeling.

Replaces the single `peer_resonance: dict[str, float]` with structured
relationship tracking per peer.  Updates are heuristic (no LLM calls) and
happen after each peer exchange.

Key behaviors:
- Trust accumulates through positive interactions
- Intellectual debt tracks idea exchange imbalance
- Shared experiences and disagreements shape relationship flavor
- Communication compatibility reflects how well agents mesh
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRUST_INITIAL: float = 0.5
TRUST_POSITIVE_BUMP: float = 0.03
TRUST_NEGATIVE_BUMP: float = 0.02
TRUST_DECAY_RATE: float = 0.01  # per tick of no interaction

COMPATIBILITY_INITIAL: float = 0.5
COMPATIBILITY_BUMP: float = 0.02

MAX_SHARED_EXPERIENCES: int = 20
MAX_DISAGREEMENTS: int = 10


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class PeerRelationship:
    """Rich relationship model between this agent and one peer."""

    peer_id: str = ""
    peer_name: str = ""
    aliases: list[str] = field(default_factory=list)
    trust: float = TRUST_INITIAL
    intellectual_debt: float = 0.0  # positive = I owe them, negative = they owe me
    shared_experiences: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    last_interaction_tick: int = 0
    interaction_count: int = 0
    communication_compatibility: float = COMPATIBILITY_INITIAL

    def __post_init__(self) -> None:
        if not self.peer_id and self.peer_name:
            self.peer_id = self.peer_name
        if not self.peer_name and self.peer_id:
            self.peer_name = self.peer_id
        if not self.aliases:
            self.aliases = [self.peer_name]
        elif self.peer_name.lower() not in {alias.lower() for alias in self.aliases}:
            self.aliases.append(self.peer_name)

    def resonance(self) -> float:
        """Compute a single resonance score (0–1) for this relationship.

        Used by emotional contagion and attention director.
        """
        # Weighted blend of trust + compatibility
        return 0.6 * self.trust + 0.4 * self.communication_compatibility

    def prompt_summary(self) -> str:
        """Compact summary for prompt injection."""
        trust_label = (
            "high" if self.trust >= 0.7 else "low" if self.trust < 0.4 else "moderate"
        )
        parts = [f"Trust: {trust_label}"]

        if self.shared_experiences:
            recent = self.shared_experiences[-2:]
            parts.append(f"Shared: {', '.join(recent)}")

        if self.disagreements:
            recent = self.disagreements[-2:]
            parts.append(f"Disagree about: {', '.join(recent)}")

        if abs(self.intellectual_debt) > 0.3:
            direction = "they've contributed more ideas" if self.intellectual_debt > 0 else (
                "you've contributed more ideas"
            )
            parts.append(direction)

        return " | ".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PeerRelationship:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def update_identity(
        self,
        peer_name: str | None = None,
        aliases: list[str] | None = None,
    ) -> None:
        """Refresh the current display name while preserving alias history."""
        if not peer_name:
            peer_name = self.peer_name
        self.peer_name = peer_name
        known = {alias.lower() for alias in self.aliases}
        for alias in [peer_name, *(aliases or [])]:
            if alias and alias.lower() not in known:
                self.aliases.append(alias)
                known.add(alias.lower())


# ---------------------------------------------------------------------------
# Relationship Store — manages all relationships for one agent
# ---------------------------------------------------------------------------

@dataclass
class RelationshipStore:
    """All peer relationships for one agent. Persisted as JSON."""

    relationships: dict[str, PeerRelationship] = field(default_factory=dict)

    def get_or_create(
        self,
        peer_id: str,
        peer_name: str | None = None,
        aliases: list[str] | None = None,
    ) -> PeerRelationship:
        key = self._resolve_key(peer_id, peer_name, aliases)
        if key is None:
            rel = PeerRelationship(peer_id=peer_id, peer_name=peer_name or peer_id)
            rel.update_identity(peer_name or peer_id, aliases)
            self.relationships[peer_id] = rel
            return rel

        rel = self.relationships[key]
        if key != peer_id:
            self.relationships.pop(key)
            self.relationships[peer_id] = rel
        rel.peer_id = peer_id or rel.peer_id
        rel.update_identity(peer_name or rel.peer_name, aliases)
        return rel

    def get(self, peer_key: str) -> PeerRelationship | None:
        """Return a relationship by stable id, current name, or alias."""
        resolved = self._resolve_key(peer_key)
        if resolved is None:
            return None
        return self.relationships.get(resolved)

    def get_resonance_dict(self) -> dict[str, float]:
        """Export resonance values for emotional contagion."""
        return {rel.peer_name: rel.resonance() for rel in self.relationships.values()}

    def prompt_context(self, peer_name: str) -> str:
        """Build prompt context for a specific peer relationship."""
        rel = self.get(peer_name)
        if rel is None:
            return f"You haven't interacted with {peer_name} yet."
        return f"Your relationship with {rel.peer_name}: {rel.prompt_summary()}"

    def all_relationships_summary(self) -> str:
        """Brief summary of all relationships for identity preamble."""
        if not self.relationships:
            return ""
        lines = []
        for _, rel in sorted(
            self.relationships.items(),
            key=lambda x: x[1].interaction_count,
            reverse=True,
        ):
            if rel.interaction_count > 0:
                lines.append(f"- {rel.peer_name}: {rel.prompt_summary()}")
        if not lines:
            return ""
        return "Peer relationships:\n" + "\n".join(lines[:5])

    def to_dict(self) -> dict:
        return {
            name: rel.to_dict() for name, rel in self.relationships.items()
        }

    @classmethod
    def from_dict(cls, data: dict) -> RelationshipStore:
        rels = {}
        for name, rel_data in data.items():
            if isinstance(rel_data, dict):
                rel = PeerRelationship.from_dict(rel_data)
                key = rel.peer_id or name
                if not rel.peer_id:
                    rel.peer_id = name
                if not rel.peer_name:
                    rel.peer_name = name
                if not rel.aliases:
                    rel.aliases = [rel.peer_name]
                rels[key] = rel
        return cls(relationships=rels)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> RelationshipStore:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return cls()

    def _resolve_key(
        self,
        peer_id: str,
        peer_name: str | None = None,
        aliases: list[str] | None = None,
    ) -> str | None:
        if peer_id in self.relationships:
            return peer_id
        target_names = {
            candidate.lower()
            for candidate in [peer_name or peer_id, *(aliases or [])]
            if candidate
        }
        for key, rel in self.relationships.items():
            if rel.peer_id == peer_id:
                return key
            aliases = [rel.peer_name, *rel.aliases, key]
            if target_names & {alias.lower() for alias in aliases if alias}:
                return key
        return None


# ---------------------------------------------------------------------------
# Heuristic Update Functions — no LLM calls needed
# ---------------------------------------------------------------------------

# Simple keyword sets for sentiment/tone detection
_AGREEMENT_SIGNALS = {
    "agree", "yes", "exactly", "resonates", "well said", "good point",
    "insightful", "true", "correct", "absolutely", "indeed",
}
_DISAGREEMENT_SIGNALS = {
    "disagree", "but", "however", "not sure", "question that",
    "challenge", "doubt", "counter", "wrong", "flawed", "mistake",
}
_IDEA_SIGNALS = {
    "propose", "suggest", "idea", "consider", "what if", "hypothesis",
    "theory", "approach", "framework", "model", "concept",
}


def update_after_exchange(
    rel: PeerRelationship,
    message_received: str,
    message_sent: str | None,
    tick: int,
    topic: str = "",
) -> None:
    """Update relationship heuristics after a peer exchange.

    This is lightweight — keyword/phrase matching, no LLM calls.
    """
    rel.last_interaction_tick = tick
    rel.interaction_count += 1

    received_lower = message_received.lower()
    sent_lower = (message_sent or "").lower()

    # Trust: adjust based on agreement/disagreement signals
    agreement_score = sum(1 for s in _AGREEMENT_SIGNALS if s in received_lower)
    disagreement_score = sum(1 for s in _DISAGREEMENT_SIGNALS if s in received_lower)

    if agreement_score > disagreement_score:
        rel.trust = min(1.0, rel.trust + TRUST_POSITIVE_BUMP)
        rel.communication_compatibility = min(
            1.0, rel.communication_compatibility + COMPATIBILITY_BUMP,
        )
    elif disagreement_score > agreement_score:
        rel.trust = max(0.0, rel.trust - TRUST_NEGATIVE_BUMP)
        # Disagreement doesn't necessarily hurt compatibility
        if topic:
            rel.disagreements.append(topic[:80])
            rel.disagreements = rel.disagreements[-MAX_DISAGREEMENTS:]

    # Intellectual debt: track idea exchange
    their_ideas = sum(1 for s in _IDEA_SIGNALS if s in received_lower)
    our_ideas = sum(1 for s in _IDEA_SIGNALS if s in sent_lower) if message_sent else 0
    if their_ideas > our_ideas:
        rel.intellectual_debt = min(1.0, rel.intellectual_debt + 0.05)
    elif our_ideas > their_ideas:
        rel.intellectual_debt = max(-1.0, rel.intellectual_debt - 0.05)

    # Shared experiences
    if topic:
        rel.shared_experiences.append(topic[:80])
        rel.shared_experiences = rel.shared_experiences[-MAX_SHARED_EXPERIENCES:]


def decay_relationships(store: RelationshipStore, current_tick: int) -> None:
    """Apply trust decay to relationships that haven't been active recently."""
    for rel in store.relationships.values():
        ticks_since = current_tick - rel.last_interaction_tick
        if ticks_since > 5:
            decay = TRUST_DECAY_RATE * (ticks_since // 5)
            rel.trust = max(0.3, rel.trust - decay)  # Floor at 0.3 (never fully distrust)
