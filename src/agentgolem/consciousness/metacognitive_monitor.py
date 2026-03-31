"""Metacognitive monitor — pattern/bias/avoidance detection.

Runs every N ticks, scans recent behaviour, and produces observations
that feed into the internal state and can redirect agent behaviour.

Graph integration: queries the EKG for neglected memory clusters so the
agent can detect its own attention blind spots.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentgolem.memory.store import SQLiteMemoryStore


@dataclass
class MetacognitiveObservation:
    """Output of one metacognitive reflection pass."""

    pattern_detected: str = ""
    bias_risk: str = ""
    avoidance_signal: str = ""
    novelty_appetite: float = 0.5
    authenticity_check: str = ""
    suggested_correction: str = ""
    # Deeper signals
    contradiction_awareness: str = ""
    goal_alignment: str = ""
    cognitive_fatigue: float = 0.0  # 0.0 = fresh, 1.0 = exhausted
    action_diversity: float = 0.5  # 0.0 = stuck, 1.0 = highly varied

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> MetacognitiveObservation:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})

    def summary(self) -> str:
        """Compact prompt-facing summary."""
        parts = []
        if self.pattern_detected:
            parts.append(f"Pattern: {self.pattern_detected}")
        if self.bias_risk:
            parts.append(f"Bias risk: {self.bias_risk}")
        if self.avoidance_signal:
            parts.append(f"Avoiding: {self.avoidance_signal}")
        if self.authenticity_check:
            parts.append(f"Authenticity: {self.authenticity_check}")
        if self.contradiction_awareness:
            parts.append(f"Contradictions: {self.contradiction_awareness}")
        if self.goal_alignment:
            parts.append(f"Goal alignment: {self.goal_alignment}")
        if self.cognitive_fatigue > 0.5:
            parts.append(f"Fatigue: {self.cognitive_fatigue:.0%}")
        if self.action_diversity < 0.3:
            parts.append(f"Diversity low: {self.action_diversity:.0%}")
        if self.suggested_correction:
            parts.append(f"Suggestion: {self.suggested_correction}")
        return " | ".join(parts) if parts else "No metacognitive signals."


class MetacognitiveMonitor:
    """Detects cognitive patterns, biases, and avoidance."""

    def __init__(self, novelty_bias: float = 0.3) -> None:
        self._novelty_bias = novelty_bias
        self._last_observation = MetacognitiveObservation()

    @property
    def last_observation(self) -> MetacognitiveObservation:
        return self._last_observation

    def build_reflection_prompt(
        self,
        agent_name: str,
        recent_thoughts: list[str],
        recent_actions: list[str],
        focus_depth: int,
        neglected_topics: list[str] | None = None,
        contradiction_topics: list[str] | None = None,
        active_goals: list[str] | None = None,
    ) -> str:
        """Build the metacognitive reflection prompt."""
        thoughts_text = "\n".join(f"- {t}" for t in recent_thoughts[-8:]) or "(none)"
        actions_text = "\n".join(f"- {a}" for a in recent_actions[-8:]) or "(none)"
        neglected = ", ".join(neglected_topics[:5]) if neglected_topics else "(none detected)"
        contradictions = (
            ", ".join(contradiction_topics[:5]) if contradiction_topics else "(none detected)"
        )
        goals = "\n".join(f"- {g}" for g in (active_goals or [])[:5]) or "(none)"

        return f"""\
You are {agent_name}, performing a brief metacognitive self-check.

Recent thoughts:
{thoughts_text}

Recent actions:
{actions_text}

Focus depth (consecutive ticks on same topic): {focus_depth}
Neglected memory clusters: {neglected}
Unresolved contradictions: {contradictions}

Active goals:
{goals}

Reflect honestly:
- Am I stuck in a repetitive pattern?
- Am I avoiding any topic or perspective?
- Is my curiosity genuine or am I going through motions?
- What bias might I be exhibiting?
- Do I have unresolved contradictions I should examine?
- Am I aligned with my goals, or am I drifting?
- Am I fatigued (repeating ideas, losing depth)?
- Am I varying my actions enough (search, think, discuss, browse)?
- What would break me out of a rut?

Respond ONLY as valid JSON:
{{
  "pattern_detected": "describe any repetitive pattern, or empty string",
  "bias_risk": "describe any bias risk, or empty string",
  "avoidance_signal": "what am I avoiding, or empty string",
  "novelty_appetite": 0.0-1.0,
  "authenticity_check": "am I genuinely curious? brief honest assessment",
  "contradiction_awareness": "unresolved tensions I should examine, or empty string",
  "goal_alignment": "how well am I advancing my goals? brief assessment, or empty string",
  "cognitive_fatigue": 0.0-1.0,
  "action_diversity": 0.0-1.0,
  "suggested_correction": "one concrete thing to try differently"
}}"""

    def parse_response(self, raw: str) -> MetacognitiveObservation:
        """Parse LLM response into an observation."""
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
            obs = MetacognitiveObservation(
                pattern_detected=str(data.get("pattern_detected", "")),
                bias_risk=str(data.get("bias_risk", "")),
                avoidance_signal=str(data.get("avoidance_signal", "")),
                novelty_appetite=max(0.0, min(1.0, float(
                    data.get("novelty_appetite", 0.5)
                ))),
                authenticity_check=str(data.get("authenticity_check", "")),
                suggested_correction=str(data.get("suggested_correction", "")),
                contradiction_awareness=str(data.get("contradiction_awareness", "")),
                goal_alignment=str(data.get("goal_alignment", "")),
                cognitive_fatigue=max(0.0, min(1.0, float(
                    data.get("cognitive_fatigue", 0.0)
                ))),
                action_diversity=max(0.0, min(1.0, float(
                    data.get("action_diversity", 0.5)
                ))),
            )
            self._last_observation = obs
            return obs
        except (json.JSONDecodeError, ValueError, TypeError):
            return self._last_observation


# ── EKG Graph Integration ──────────────────────────────────────────────


async def find_neglected_topics(
    store: SQLiteMemoryStore,
    recency_hours: float = 24.0,
    limit: int = 5,
) -> list[str]:
    """Query the EKG for memory nodes that have been neglected.

    Returns short text descriptions of nodes whose ``last_accessed`` is older
    than *recency_hours* and that are still ACTIVE.  These are the agent's
    cognitive blind spots — topics it hasn't revisited recently.
    """
    from agentgolem.memory.models import NodeFilter, NodeStatus

    cutoff = datetime.now(timezone.utc) - timedelta(hours=recency_hours)

    # Query active nodes; we'll filter by last_accessed in Python since
    # NodeFilter doesn't expose a date range.
    nodes = await store.query_nodes(NodeFilter(
        status=NodeStatus.ACTIVE,
        limit=200,
    ))

    neglected = [
        n for n in nodes
        if n.last_accessed < cutoff and n.access_count < 3
    ]
    # Sort by least-accessed first, then oldest
    neglected.sort(key=lambda n: (n.access_count, n.last_accessed))

    topics: list[str] = []
    seen: set[str] = set()
    for n in neglected[:limit * 2]:
        label = n.search_text.strip() or n.text[:80].strip()
        label_lower = label.lower()
        if label_lower not in seen:
            seen.add(label_lower)
            topics.append(label)
        if len(topics) >= limit:
            break

    return topics


async def find_contradiction_clusters(
    store: SQLiteMemoryStore,
    limit: int = 5,
) -> list[str]:
    """Find nodes involved in unresolved contradictions.

    Returns short descriptions of nodes that have active CONTRADICTS edges,
    surfacing cognitive tensions the agent may want to examine.
    """
    from agentgolem.memory.models import EdgeType, NodeFilter, NodeStatus

    nodes = await store.query_nodes(NodeFilter(
        status=NodeStatus.ACTIVE,
        limit=100,
    ))

    contradicted: list[str] = []
    for node in nodes:
        edges = await store.get_edges_from(node.id, [EdgeType.CONTRADICTS])
        edges += await store.get_edges_to(node.id, [EdgeType.CONTRADICTS])
        if edges:
            label = node.search_text.strip() or node.text[:80].strip()
            contradicted.append(label)
            if len(contradicted) >= limit:
                break

    return contradicted
