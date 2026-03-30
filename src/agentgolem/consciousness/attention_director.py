"""Attention director — translates internal state into behavioural bias.

Sits between the internal state / metacognitive monitor and the action
selection prompt, creating a gravitational pull toward internally-driven
behaviour rather than mechanical action selection.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from agentgolem.consciousness.internal_state import InternalState
from agentgolem.consciousness.metacognitive_monitor import MetacognitiveObservation


@dataclass
class AttentionDirective:
    """Behavioural guidance injected into the action selection prompt."""

    primary_drive: str = ""
    secondary_drive: str = ""
    avoidance: str = ""
    social_need: str = ""
    energy_budget: str = "moderate"  # light | moderate | deep
    recommended_mode: str = "explore"  # explore | reflect | discuss | rest | create

    def to_dict(self) -> dict:
        return asdict(self)

    def to_prompt_preamble(self) -> str:
        """Format as natural-language prompt preamble."""
        parts = []
        if self.primary_drive:
            parts.append(f"You feel drawn to: {self.primary_drive}.")
        if self.secondary_drive:
            parts.append(f"A secondary pull: {self.secondary_drive}.")
        if self.avoidance:
            parts.append(f"Your metacognition suggests: {self.avoidance}.")
        if self.social_need:
            parts.append(f"Relationally: {self.social_need}.")
        parts.append(f"Energy budget: {self.energy_budget}.")
        parts.append(f"Suggested mode: {self.recommended_mode}.")
        return " ".join(parts)


class AttentionDirector:
    """Computes an AttentionDirective from internal state + metacognition."""

    def __init__(self, influence_weight: float = 0.7) -> None:
        self._influence_weight = influence_weight

    def compute(
        self,
        state: InternalState,
        observation: MetacognitiveObservation | None = None,
    ) -> AttentionDirective:
        """Derive a directive from current cognitive state."""
        directive = AttentionDirective()

        # Primary drive: curiosity or growth
        if state.curiosity_intensity > 0.6 and state.curiosity_focus:
            directive.primary_drive = (
                f"satisfy your curiosity about {state.curiosity_focus}"
            )
        elif state.growth_vector:
            directive.primary_drive = (
                f"continue developing toward {state.growth_vector}"
            )

        # Secondary drive: reduce uncertainty
        if state.uncertainty_topics:
            topic = state.uncertainty_topics[0]
            directive.secondary_drive = f"reduce uncertainty about {topic}"

        # Metacognitive avoidance correction
        if observation:
            if observation.avoidance_signal:
                directive.avoidance = (
                    f"break avoidance pattern — engage with "
                    f"{observation.avoidance_signal}"
                )
            elif observation.pattern_detected and observation.novelty_appetite > 0.6:
                directive.avoidance = (
                    f"break repetitive pattern — {observation.suggested_correction}"
                )

        # Social need from isolation signal
        if state.isolation_signal > 0.6:
            # Find least-resonant peer
            if state.peer_resonance:
                least = min(state.peer_resonance, key=state.peer_resonance.get)
                directive.social_need = (
                    f"reach out to {least} — you haven't connected recently"
                )
            else:
                directive.social_need = "seek peer connection"
        elif state.isolation_signal > 0.3:
            directive.social_need = "consider sharing a recent insight with a peer"

        # Energy budget from engagement
        if state.engagement_level < 0.3:
            directive.energy_budget = "light"
            directive.recommended_mode = "rest"
        elif state.engagement_level > 0.7 and state.cognitive_load < 0.7:
            directive.energy_budget = "deep"
        else:
            directive.energy_budget = "moderate"

        # Recommended mode
        if directive.recommended_mode != "rest":
            if state.attention_mode == "exploring":
                directive.recommended_mode = "explore"
            elif state.attention_mode == "deepening":
                if state.focus_depth > 5:
                    directive.recommended_mode = "discuss"
                else:
                    directive.recommended_mode = "reflect"
            elif state.attention_mode == "integrating":
                directive.recommended_mode = "create"
            else:
                directive.recommended_mode = "rest"

        return directive
