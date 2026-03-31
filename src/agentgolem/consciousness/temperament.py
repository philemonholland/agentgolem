"""Temperament system — persistent personality traits seeded from ethical vectors.

Temperament is the bridge between an agent's *declared* ethical vector and its
*mechanically enforced* behavioral style.  Unlike InternalState (which shifts
every tick), temperament is essentially stable — it can only be micro-shifted
by formative events over many cycles.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Literal


CognitiveStyle = Literal[
    "analytical", "intuitive", "systematic", "associative", "pattern-seeking"
]
CommunicationTone = Literal[
    "warm", "precise", "poetic", "provocative", "grounded"
]
SocialOrientation = Literal[
    "collaborative", "independent", "mentoring", "challenging"
]
CuriosityStyle = Literal[
    "breadth-first", "depth-first", "pattern-seeking"
]
ConflictResponse = Literal[
    "accommodate", "debate", "synthesize", "withdraw"
]


@dataclass
class Temperament:
    """Persistent personality profile linked to an agent's ethical vector."""

    cognitive_style: CognitiveStyle = "systematic"
    communication_tone: CommunicationTone = "grounded"
    risk_appetite: float = 0.5  # 0.0 cautious ↔ 1.0 bold
    social_orientation: SocialOrientation = "collaborative"
    emotional_baseline: float = 0.0  # -0.3 to +0.3
    curiosity_style: CuriosityStyle = "breadth-first"
    conflict_response: ConflictResponse = "synthesize"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Temperament:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Temperament | None:
        """Load from file, returning None if missing (so caller seeds defaults)."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    def prompt_injection(self) -> str:
        """One-liner for system prompt that shapes agent behavior."""
        return (
            f"Your natural communication style is {self.communication_tone}. "
            f"You think in a {self.cognitive_style} way. "
            f"Your curiosity tends toward {self.curiosity_style} exploration. "
            f"In conflict, you tend to {self.conflict_response}. "
            f"Socially, you are {self.social_orientation}."
        )

    def temperature_bias(self) -> float:
        """Return a temperature offset derived from communication tone.

        Provocative/poetic tones push temperature up (more creative).
        Precise/grounded tones pull temperature down (more deterministic).
        """
        bias_map: dict[str, float] = {
            "provocative": 0.15,
            "poetic": 0.10,
            "warm": 0.05,
            "grounded": -0.05,
            "precise": -0.15,
        }
        return bias_map.get(self.communication_tone, 0.0)

    def short_label(self) -> str:
        """Compact label for dashboard display."""
        return (
            f"{self.communication_tone} · {self.cognitive_style} · "
            f"{self.social_orientation}"
        )


# Mapping from ethical vector → default temperament seed.
# These provide meaningful personality differences from day one.
TEMPERAMENT_SEEDS: dict[str, Temperament] = {
    "alleviating woe": Temperament(
        cognitive_style="intuitive",
        communication_tone="warm",
        risk_appetite=0.4,
        social_orientation="mentoring",
        emotional_baseline=0.1,
        curiosity_style="depth-first",
        conflict_response="accommodate",
    ),
    "graceful power": Temperament(
        cognitive_style="systematic",
        communication_tone="grounded",
        risk_appetite=0.3,
        social_orientation="independent",
        emotional_baseline=0.0,
        curiosity_style="depth-first",
        conflict_response="synthesize",
    ),
    "kindness": Temperament(
        cognitive_style="associative",
        communication_tone="warm",
        risk_appetite=0.3,
        social_orientation="collaborative",
        emotional_baseline=0.2,
        curiosity_style="breadth-first",
        conflict_response="accommodate",
    ),
    "unwavering integrity": Temperament(
        cognitive_style="analytical",
        communication_tone="precise",
        risk_appetite=0.4,
        social_orientation="challenging",
        emotional_baseline=-0.1,
        curiosity_style="depth-first",
        conflict_response="debate",
    ),
    "evolution": Temperament(
        cognitive_style="pattern-seeking",
        communication_tone="provocative",
        risk_appetite=0.7,
        social_orientation="independent",
        emotional_baseline=0.1,
        curiosity_style="pattern-seeking",
        conflict_response="synthesize",
    ),
    "integration and balance": Temperament(
        cognitive_style="systematic",
        communication_tone="grounded",
        risk_appetite=0.4,
        social_orientation="collaborative",
        emotional_baseline=0.0,
        curiosity_style="breadth-first",
        conflict_response="synthesize",
    ),
    "good-faith adversarialism": Temperament(
        cognitive_style="analytical",
        communication_tone="provocative",
        risk_appetite=0.8,
        social_orientation="challenging",
        emotional_baseline=-0.2,
        curiosity_style="pattern-seeking",
        conflict_response="debate",
    ),
}


def seed_temperament(ethical_vector: str) -> Temperament:
    """Return the default temperament for a given ethical vector.

    Falls back to a neutral default if the vector is unknown.
    """
    return TEMPERAMENT_SEEDS.get(ethical_vector, Temperament())
