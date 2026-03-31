"""Emotional dynamics — physics-like emotional system with momentum and gravity.

Replaces the current "LLM sets mood freely" approach with a system where
mood has **momentum** (can't swing wildly) and **gravity toward baseline**
(personality-linked resting valence).

Key behaviors:
- Emotional momentum smooths sudden swings
- Baseline gravity pulls valence toward temperament default over time
- Formative events leave permanent micro-shifts to the baseline
- Emotional contagion lets peer valence bleed through during discussions
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
import json


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MOMENTUM_WEIGHT: float = 0.7
"""Weight of the LLM-proposed valence vs previous (0.7 = 70% new, 30% old)."""

GRAVITY_RATE: float = 0.05
"""Per-tick drift rate toward emotional baseline (5%)."""

FORMATIVE_SHIFT: float = 0.02
"""Permanent baseline micro-shift from formative events."""

MAX_BASELINE_DRIFT: float = 0.5
"""Hard cap on how far the baseline can drift from its seed value."""

CONTAGION_FACTOR: float = 0.05
"""How much a peer's valence influences own valence per interaction."""


# ---------------------------------------------------------------------------
# Formative Event Tracking
# ---------------------------------------------------------------------------

@dataclass
class FormativeEvent:
    """A significant emotional experience that permanently shifts baseline."""
    tick: int
    description: str
    baseline_shift: float  # positive or negative micro-shift applied


@dataclass
class EmotionalDynamicsState:
    """Persistent emotional dynamics state (separate from InternalState)."""

    # Current emotional baseline (starts from temperament, drifts with events)
    effective_baseline: float = 0.0

    # Seed baseline from temperament (immutable reference)
    seed_baseline: float = 0.0

    # Formative events log
    formative_events: list[FormativeEvent] = field(default_factory=list)

    # Cumulative baseline drift from formative events
    cumulative_drift: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> EmotionalDynamicsState:
        events_raw = data.pop("formative_events", [])
        events = [
            FormativeEvent(**e) if isinstance(e, dict) else e
            for e in events_raw
        ]
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known and k != "formative_events"}
        return cls(formative_events=events, **filtered)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> EmotionalDynamicsState:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return cls()


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def apply_momentum(
    proposed_valence: float,
    previous_valence: float,
    momentum_weight: float = MOMENTUM_WEIGHT,
) -> float:
    """Smooth valence transitions with momentum.

    new = momentum_weight × proposed + (1 - momentum_weight) × previous
    Prevents wild mood swings between ticks.
    """
    blended = momentum_weight * proposed_valence + (1 - momentum_weight) * previous_valence
    return max(-1.0, min(1.0, blended))


def apply_gravity(
    current_valence: float,
    baseline: float,
    gravity_rate: float = GRAVITY_RATE,
) -> float:
    """Drift valence toward the emotional baseline.

    Each tick, valence moves `gravity_rate` fraction toward baseline.
    Cheerful agents recover from sadness; skeptical agents cool from enthusiasm.
    """
    delta = baseline - current_valence
    new_valence = current_valence + gravity_rate * delta
    return max(-1.0, min(1.0, new_valence))


def apply_contagion(
    own_valence: float,
    peer_valences: dict[str, float],
    peer_resonance: dict[str, float],
    contagion_factor: float = CONTAGION_FACTOR,
) -> float:
    """Apply emotional contagion from peers.

    During discussions, peer valence slightly influences own valence,
    weighted by resonance with each peer.
    """
    if not peer_valences:
        return own_valence

    total_influence = 0.0
    total_weight = 0.0

    for peer, valence in peer_valences.items():
        resonance = peer_resonance.get(peer, 0.5)
        weight = resonance * contagion_factor
        total_influence += weight * valence
        total_weight += weight

    if total_weight > 0:
        # Normalize: spread the contagion proportionally
        new_valence = own_valence + total_influence
        return max(-1.0, min(1.0, new_valence))

    return own_valence


def record_formative_event(
    state: EmotionalDynamicsState,
    tick: int,
    description: str,
    positive: bool = True,
) -> float:
    """Record a formative event that permanently micro-shifts the baseline.

    Returns the new effective baseline.
    """
    shift = FORMATIVE_SHIFT if positive else -FORMATIVE_SHIFT

    # Enforce max drift from seed
    proposed_drift = state.cumulative_drift + shift
    if abs(proposed_drift) > MAX_BASELINE_DRIFT:
        # Clamp to max drift
        shift = (MAX_BASELINE_DRIFT if proposed_drift > 0 else -MAX_BASELINE_DRIFT) - state.cumulative_drift

    if abs(shift) < 1e-6:
        return state.effective_baseline  # already at max drift

    state.cumulative_drift += shift
    state.effective_baseline = state.seed_baseline + state.cumulative_drift

    state.formative_events.append(
        FormativeEvent(tick=tick, description=description, baseline_shift=shift)
    )

    # Keep event log bounded (last 50 events)
    if len(state.formative_events) > 50:
        state.formative_events = state.formative_events[-50:]

    return state.effective_baseline


def full_emotional_update(
    proposed_valence: float,
    previous_valence: float,
    dynamics_state: EmotionalDynamicsState,
    peer_valences: dict[str, float] | None = None,
    peer_resonance: dict[str, float] | None = None,
) -> float:
    """Apply the full emotional dynamics pipeline.

    Order: momentum → gravity → contagion.
    Returns the final clamped valence.
    """
    # 1. Momentum: smooth the transition
    valence = apply_momentum(proposed_valence, previous_valence)

    # 2. Gravity: drift toward baseline
    valence = apply_gravity(valence, dynamics_state.effective_baseline)

    # 3. Contagion: peer influence (if peers are available)
    if peer_valences and peer_resonance is not None:
        valence = apply_contagion(
            valence, peer_valences, peer_resonance or {},
        )

    return max(-1.0, min(1.0, valence))


# ---------------------------------------------------------------------------
# Formative Event Detection Helpers
# ---------------------------------------------------------------------------

# These keywords in recent thoughts signal formative events
POSITIVE_FORMATIVE_SIGNALS = [
    "breakthrough",
    "resolved contradiction",
    "deep resonance",
    "peer praised",
    "human praised",
    "strong agreement",
    "discovered connection",
    "evolved understanding",
    "growth moment",
]

NEGATIVE_FORMATIVE_SIGNALS = [
    "rejected proposal",
    "contradiction unresolved",
    "human criticized",
    "peer disagreed strongly",
    "failed to understand",
    "lost confidence",
    "repeated mistake",
    "isolation deepened",
]


def detect_formative_event(
    recent_thoughts: list[str],
) -> tuple[bool, str, bool] | None:
    """Scan recent thoughts for formative event signals.

    Returns (detected, description, is_positive) or None if no event detected.
    """
    combined = " ".join(recent_thoughts).lower()

    for signal in POSITIVE_FORMATIVE_SIGNALS:
        if signal in combined:
            return (True, signal, True)

    for signal in NEGATIVE_FORMATIVE_SIGNALS:
        if signal in combined:
            return (True, signal, False)

    return None
