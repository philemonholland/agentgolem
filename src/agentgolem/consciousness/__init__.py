"""Consciousness kernel — metacognitive self-awareness substrate."""
from __future__ import annotations

from agentgolem.consciousness.internal_state import InternalState
from agentgolem.consciousness.metacognitive_monitor import (
    MetacognitiveMonitor,
    MetacognitiveObservation,
    find_contradiction_clusters,
    find_neglected_topics,
)
from agentgolem.consciousness.attention_director import (
    AttentionDirective,
    AttentionDirector,
)
from agentgolem.consciousness.narrative_synthesizer import (
    NarrativeChapter,
    NarrativeSynthesizer,
    persist_chapter_to_graph,
)
from agentgolem.consciousness.self_model import (
    SelfModel,
    build_graph_context_for_self_model,
)

__all__ = [
    "InternalState",
    "MetacognitiveMonitor",
    "MetacognitiveObservation",
    "AttentionDirective",
    "AttentionDirector",
    "NarrativeChapter",
    "NarrativeSynthesizer",
    "SelfModel",
    # EKG graph integration
    "find_neglected_topics",
    "find_contradiction_clusters",
    "persist_chapter_to_graph",
    "build_graph_context_for_self_model",
]
