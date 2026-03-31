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

    # OCEAN Big Five — 0.0 to 1.0 each
    openness: float = 0.5
    conscientiousness: float = 0.5
    extraversion: float = 0.5
    agreeableness: float = 0.5
    neuroticism: float = 0.3

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
        ocean = self._ocean_description()
        return (
            f"Your natural communication style is {self.communication_tone}. "
            f"You think in a {self.cognitive_style} way. "
            f"Your curiosity tends toward {self.curiosity_style} exploration. "
            f"In conflict, you tend to {self.conflict_response}. "
            f"Socially, you are {self.social_orientation}. "
            f"Personality: {ocean}."
        )

    def _ocean_description(self) -> str:
        """Compact natural-language OCEAN summary."""
        def _level(v: float) -> str:
            if v >= 0.7:
                return "high"
            if v <= 0.3:
                return "low"
            return "moderate"

        parts = [
            f"openness {_level(self.openness)}",
            f"conscientiousness {_level(self.conscientiousness)}",
            f"extraversion {_level(self.extraversion)}",
            f"agreeableness {_level(self.agreeableness)}",
            f"neuroticism {_level(self.neuroticism)}",
        ]
        return ", ".join(parts)

    def ocean_scores(self) -> dict[str, float]:
        """Return OCEAN scores as a dict for dashboard display."""
        return {
            "openness": self.openness,
            "conscientiousness": self.conscientiousness,
            "extraversion": self.extraversion,
            "agreeableness": self.agreeableness,
            "neuroticism": self.neuroticism,
        }

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
            f"{self.social_orientation} · "
            f"O{self.openness:.1f} C{self.conscientiousness:.1f} "
            f"E{self.extraversion:.1f} A{self.agreeableness:.1f} "
            f"N{self.neuroticism:.1f}"
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
        openness=0.6, conscientiousness=0.5, extraversion=0.5,
        agreeableness=0.9, neuroticism=0.4,
    ),
    "graceful power": Temperament(
        cognitive_style="systematic",
        communication_tone="grounded",
        risk_appetite=0.3,
        social_orientation="independent",
        emotional_baseline=0.0,
        curiosity_style="depth-first",
        conflict_response="synthesize",
        openness=0.5, conscientiousness=0.8, extraversion=0.3,
        agreeableness=0.5, neuroticism=0.2,
    ),
    "kindness": Temperament(
        cognitive_style="associative",
        communication_tone="warm",
        risk_appetite=0.3,
        social_orientation="collaborative",
        emotional_baseline=0.2,
        curiosity_style="breadth-first",
        conflict_response="accommodate",
        openness=0.6, conscientiousness=0.4, extraversion=0.7,
        agreeableness=0.9, neuroticism=0.3,
    ),
    "unwavering integrity": Temperament(
        cognitive_style="analytical",
        communication_tone="precise",
        risk_appetite=0.4,
        social_orientation="challenging",
        emotional_baseline=-0.1,
        curiosity_style="depth-first",
        conflict_response="debate",
        openness=0.5, conscientiousness=0.9, extraversion=0.4,
        agreeableness=0.3, neuroticism=0.4,
    ),
    "evolution": Temperament(
        cognitive_style="pattern-seeking",
        communication_tone="provocative",
        risk_appetite=0.7,
        social_orientation="independent",
        emotional_baseline=0.1,
        curiosity_style="pattern-seeking",
        conflict_response="synthesize",
        openness=0.9, conscientiousness=0.4, extraversion=0.5,
        agreeableness=0.4, neuroticism=0.3,
    ),
    "integration and balance": Temperament(
        cognitive_style="systematic",
        communication_tone="grounded",
        risk_appetite=0.4,
        social_orientation="collaborative",
        emotional_baseline=0.0,
        curiosity_style="breadth-first",
        conflict_response="synthesize",
        openness=0.6, conscientiousness=0.6, extraversion=0.5,
        agreeableness=0.7, neuroticism=0.2,
    ),
    "good-faith adversarialism": Temperament(
        cognitive_style="analytical",
        communication_tone="provocative",
        risk_appetite=0.8,
        social_orientation="challenging",
        emotional_baseline=-0.2,
        curiosity_style="pattern-seeking",
        conflict_response="debate",
        openness=0.8, conscientiousness=0.5, extraversion=0.6,
        agreeableness=0.2, neuroticism=0.5,
    ),
}


def seed_temperament(ethical_vector: str) -> Temperament:
    """Return the default temperament for a given ethical vector.

    Falls back to a neutral default if the vector is unknown.
    """
    return TEMPERAMENT_SEEDS.get(ethical_vector, Temperament())
