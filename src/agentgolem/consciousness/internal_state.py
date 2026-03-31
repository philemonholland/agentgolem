"""Internal state model — the dynamic felt-sense of an agent.

This is NOT a personality (that lives in soul/heartbeat).  It is a
constantly shifting cognitive-emotional weather system that determines
what the agent naturally gravitates toward doing next.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class InternalState:
    """Dynamic cognitive-emotional state, updated every tick."""

    # Motivational drives
    curiosity_focus: str = ""
    curiosity_intensity: float = 0.5
    growth_vector: str = ""

    # Confidence & uncertainty
    confidence_level: float = 0.5
    uncertainty_topics: list[str] = field(default_factory=list)
    epistemic_humility: float = 0.5

    # Affective tone
    emotional_valence: float = 0.0
    engagement_level: float = 0.5

    # Cognitive dynamics
    cognitive_load: float = 0.3
    focus_depth: int = 0
    attention_mode: str = "exploring"  # exploring | deepening | integrating | resting

    # Relational awareness
    peer_resonance: dict[str, float] = field(default_factory=dict)
    isolation_signal: float = 0.0

    # Tick tracking
    last_updated_tick: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> InternalState:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def save(self, path: Path) -> None:
        """Persist state to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> InternalState:
        """Load state from JSON file, returning defaults if missing."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return cls()

    def summary(self) -> str:
        """Compact human-readable summary for prompt injection."""
        parts = []
        if self.curiosity_focus:
            parts.append(f"Curious about: {self.curiosity_focus} "
                         f"(intensity {self.curiosity_intensity:.1f})")
        parts.append(f"Confidence: {self.confidence_level:.1f}")
        if self.uncertainty_topics:
            topics = ", ".join(self.uncertainty_topics[:3])
            parts.append(f"Uncertain about: {topics}")
        valence_word = "positive" if self.emotional_valence > 0.2 else (
            "negative" if self.emotional_valence < -0.2 else "neutral"
        )
        parts.append(f"Mood: {valence_word} ({self.emotional_valence:+.1f})")
        parts.append(f"Engagement: {self.engagement_level:.1f}")
        parts.append(f"Mode: {self.attention_mode}")
        if self.isolation_signal > 0.5:
            parts.append("Feeling isolated — seeking connection")
        if self.growth_vector:
            parts.append(f"Growing toward: {self.growth_vector}")
        return " | ".join(parts)

    def update_focus_depth(self, current_focus: str) -> None:
        """Track consecutive ticks on the same topic."""
        if current_focus and current_focus == self.curiosity_focus:
            self.focus_depth += 1
        else:
            self.focus_depth = 0 if not current_focus else 1


INTERNAL_STATE_REFLECTION_PROMPT = """\
You are {agent_name}, reflecting on your internal state.

Recent thoughts: {recent_thoughts}
Recent actions: {recent_actions}
Current state summary: {current_state}

Reflect briefly and update your internal state. Respond ONLY as valid JSON:
{{
  "curiosity_focus": "what draws your attention most right now",
  "curiosity_intensity": 0.0-1.0,
  "growth_vector": "direction you feel yourself developing",
  "confidence_level": 0.0-1.0,
  "uncertainty_topics": ["things you know you don't know"],
  "epistemic_humility": 0.0-1.0,
  "emotional_valence": -1.0 to 1.0,
  "engagement_level": 0.0-1.0,
  "cognitive_load": 0.0-1.0,
  "attention_mode": "exploring" or "deepening" or "integrating" or "resting"
}}

Be honest. This is your private self-awareness, not a performance."""


def parse_internal_state_update(raw: str, current: InternalState) -> InternalState:
    """Parse LLM JSON response into an updated InternalState.

    Preserves fields the LLM didn't mention and clamps numeric values.
    """
    try:
        # Strip markdown fences if present
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
        return current  # keep existing state if parse fails

    def clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, val))

    if "curiosity_focus" in data and isinstance(data["curiosity_focus"], str):
        current.update_focus_depth(data["curiosity_focus"])
        current.curiosity_focus = data["curiosity_focus"]
    if "curiosity_intensity" in data:
        current.curiosity_intensity = clamp(float(data["curiosity_intensity"]))
    if "growth_vector" in data and isinstance(data["growth_vector"], str):
        current.growth_vector = data["growth_vector"]
    if "confidence_level" in data:
        current.confidence_level = clamp(float(data["confidence_level"]))
    if "uncertainty_topics" in data and isinstance(data["uncertainty_topics"], list):
        current.uncertainty_topics = [str(t) for t in data["uncertainty_topics"][:5]]
    if "epistemic_humility" in data:
        current.epistemic_humility = clamp(float(data["epistemic_humility"]))
    if "emotional_valence" in data:
        # Store raw proposed valence — momentum/gravity applied externally
        current.emotional_valence = clamp(float(data["emotional_valence"]), -1.0, 1.0)
    if "engagement_level" in data:
        current.engagement_level = clamp(float(data["engagement_level"]))
    if "cognitive_load" in data:
        current.cognitive_load = clamp(float(data["cognitive_load"]))
    if "attention_mode" in data and data["attention_mode"] in (
        "exploring", "deepening", "integrating", "resting",
    ):
        current.attention_mode = data["attention_mode"]

    return current
