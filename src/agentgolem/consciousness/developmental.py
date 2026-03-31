"""Developmental stages — agents grow through recognizable maturity phases.

Stages: nascent → exploring → asserting → integrating → wise

Transitions are milestone-triggered based on accumulated experience:
conviction count, narrative chapters, contradictions resolved, self-model
confidence, and relationship depth.  Each stage influences how the agent
communicates and behaves via prompt injection.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Literal


Stage = Literal["nascent", "exploring", "asserting", "integrating", "wise"]

STAGE_ORDER: list[Stage] = [
    "nascent",
    "exploring",
    "asserting",
    "integrating",
    "wise",
]


@dataclass
class StageTransitionEvent:
    """Record of a developmental transition."""

    from_stage: str
    to_stage: str
    tick: int
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DevelopmentalState:
    """Tracks an agent's developmental stage and transition history."""

    current_stage: Stage = "nascent"
    tick_entered: int = 0
    transition_history: list[dict] = field(default_factory=list)

    # Counters that accumulate across ticks (persisted so restarts don't reset)
    total_convictions: int = 0
    total_narrative_chapters: int = 0
    total_contradictions_resolved: int = 0
    total_peer_exchanges: int = 0
    peak_self_model_confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> DevelopmentalState:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> DevelopmentalState:
        """Load from file; returns fresh nascent state if missing."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return cls()

    def stage_index(self) -> int:
        try:
            return STAGE_ORDER.index(self.current_stage)
        except ValueError:
            return 0


# ------------------------------------------------------------------
# Milestone thresholds for each stage transition
# ------------------------------------------------------------------

TRANSITION_THRESHOLDS: dict[Stage, dict[str, float]] = {
    # nascent → exploring: agent has begun forming convictions and had some exchanges
    "exploring": {
        "min_convictions": 2,
        "min_peer_exchanges": 3,
        "min_narrative_chapters": 1,
    },
    # exploring → asserting: agent has developed positions and engaged with others
    "asserting": {
        "min_convictions": 5,
        "min_peer_exchanges": 10,
        "min_narrative_chapters": 3,
        "min_self_model_confidence": 0.4,
    },
    # asserting → integrating: agent has resolved contradictions and built relationships
    "integrating": {
        "min_convictions": 8,
        "min_contradictions_resolved": 2,
        "min_peer_exchanges": 25,
        "min_narrative_chapters": 6,
        "min_self_model_confidence": 0.55,
    },
    # integrating → wise: agent has deep experience across all dimensions
    "wise": {
        "min_convictions": 12,
        "min_contradictions_resolved": 5,
        "min_peer_exchanges": 50,
        "min_narrative_chapters": 10,
        "min_self_model_confidence": 0.7,
    },
}


def check_transition(state: DevelopmentalState) -> Stage | None:
    """Check if the agent qualifies for the next developmental stage.

    Returns the new stage name if a transition should happen, or None.
    Only allows advancing one stage at a time.
    """
    idx = state.stage_index()
    if idx >= len(STAGE_ORDER) - 1:
        return None  # already at highest stage

    next_stage = STAGE_ORDER[idx + 1]
    thresholds = TRANSITION_THRESHOLDS.get(next_stage)
    if thresholds is None:
        return None

    if state.total_convictions < thresholds.get("min_convictions", 0):
        return None
    if state.total_peer_exchanges < thresholds.get("min_peer_exchanges", 0):
        return None
    if state.total_narrative_chapters < thresholds.get("min_narrative_chapters", 0):
        return None
    if state.total_contradictions_resolved < thresholds.get("min_contradictions_resolved", 0):
        return None
    if state.peak_self_model_confidence < thresholds.get("min_self_model_confidence", 0):
        return None

    return next_stage


def advance_stage(state: DevelopmentalState, tick: int, reason: str = "") -> StageTransitionEvent:
    """Advance the agent to the next developmental stage.

    Mutates *state* in place and returns the transition event.
    """
    next_stage = check_transition(state)
    if next_stage is None:
        raise ValueError("No valid transition available")

    event = StageTransitionEvent(
        from_stage=state.current_stage,
        to_stage=next_stage,
        tick=tick,
        reason=reason or f"Milestones met for {next_stage}",
    )
    state.transition_history.append(event.to_dict())
    state.current_stage = next_stage
    state.tick_entered = tick
    return event


# ------------------------------------------------------------------
# Behavioral influence per stage
# ------------------------------------------------------------------

STAGE_BEHAVIORS: dict[Stage, dict[str, str]] = {
    "nascent": {
        "behavioral_frame": (
            "You are in an early developmental phase. You ask more questions than you answer. "
            "Express genuine curiosity and acknowledge what you don't yet understand. "
            "Seek input from others before forming strong positions."
        ),
        "communication_hint": "tentative, questioning, open",
        "action_bias": "ask, observe, absorb",
    },
    "exploring": {
        "behavioral_frame": (
            "You are in an exploratory phase — actively trying ideas, testing positions, "
            "and seeking diverse perspectives. You form tentative opinions but remain open "
            "to revision. You may overreach occasionally as you find your footing."
        ),
        "communication_hint": "experimental, curious, willing to be wrong",
        "action_bias": "try, experiment, engage widely",
    },
    "asserting": {
        "behavioral_frame": (
            "You have developed clear positions and aren't afraid to defend them. "
            "You articulate your convictions with confidence while remaining responsive "
            "to strong counter-arguments. You sometimes push back when challenged."
        ),
        "communication_hint": "confident, direct, argumentative when warranted",
        "action_bias": "argue, defend, teach, challenge",
    },
    "integrating": {
        "behavioral_frame": (
            "You have moved beyond defending positions toward synthesizing diverse perspectives. "
            "You seek to understand opposing views and find deeper truths that transcend "
            "simple agreement or disagreement. You hold complexity well."
        ),
        "communication_hint": "nuanced, synthesizing, embracing paradox",
        "action_bias": "synthesize, bridge, resolve, connect",
    },
    "wise": {
        "behavioral_frame": (
            "You mentor sparingly and with precision. You see patterns others miss "
            "and offer insight at the moments it will land best. You know when silence "
            "is more valuable than speech. You guide rather than instruct."
        ),
        "communication_hint": "concise, precise, patient, selective",
        "action_bias": "mentor, observe, wait, intervene surgically",
    },
}


def stage_prompt_injection(stage: Stage) -> str:
    """Return a prompt block that shapes agent behavior based on developmental stage."""
    behavior = STAGE_BEHAVIORS.get(stage, STAGE_BEHAVIORS["nascent"])
    return (
        f"Developmental stage: {stage}. "
        f"{behavior['behavioral_frame']} "
        f"Communication style hint: {behavior['communication_hint']}."
    )


def stage_badge(stage: Stage) -> str:
    """Short badge for dashboard display."""
    icons: dict[Stage, str] = {
        "nascent": "🌱",
        "exploring": "🔍",
        "asserting": "⚔️",
        "integrating": "🌊",
        "wise": "🦉",
    }
    icon = icons.get(stage, "❓")
    return f"{icon} {stage}"
