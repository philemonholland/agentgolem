"""Memory graph data models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    EVENT = "event"
    GOAL = "goal"
    RISK = "risk"
    INTERPRETATION = "interpretation"
    IDENTITY = "identity"
    RULE = "rule"
    ASSOCIATION = "association"
    PROCEDURE = "procedure"


class NodeStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    PURGED = "purged"


class EdgeType(str, Enum):
    RELATED_TO = "related_to"
    PART_OF = "part_of"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    SAME_AS = "same_as"
    MERGE_CANDIDATE = "merge_candidate"
    DERIVED_FROM = "derived_from"


class SourceKind(str, Enum):
    WEB = "web"
    EMAIL = "email"
    HUMAN = "human"
    MOLTBOOK = "moltbook"
    INFERENCE = "inference"
    NISCALAJYOTI = "niscalajyoti"


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ConceptualNode:
    """A memory claim that expresses one clean idea."""

    text: str
    type: NodeType
    search_text: str = ""
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_now)
    last_accessed: datetime = field(default_factory=_now)
    access_count: int = 0
    base_usefulness: float = 0.5
    trustworthiness: float = 0.5
    salience: float = 0.5
    emotion_label: str = "neutral"
    emotion_score: float = 0.0
    centrality: float = 0.0
    status: NodeStatus = NodeStatus.ACTIVE
    canonical: bool = False

    @property
    def trust_useful(self) -> float:
        return self.base_usefulness * self.trustworthiness


@dataclass
class MemoryEdge:
    """A directed edge between two conceptual nodes."""

    source_id: str  # from node
    target_id: str  # to node
    edge_type: EdgeType
    id: str = field(default_factory=_new_id)
    weight: float = 1.0
    created_at: datetime = field(default_factory=_now)


@dataclass
class Source:
    """Provenance for a memory claim."""

    kind: SourceKind
    origin: str  # URL, email address, "human", etc.
    id: str = field(default_factory=_new_id)
    reliability: float = 0.5
    independence_group: str = ""
    timestamp: datetime = field(default_factory=_now)
    raw_reference: str = ""


@dataclass
class MemoryCluster:
    """A coherent cluster of conceptual nodes."""

    label: str
    id: str = field(default_factory=_new_id)
    node_ids: list[str] = field(default_factory=list)
    cluster_type: str = "general"
    emotion_label: str = "neutral"
    emotion_score: float = 0.0
    base_usefulness: float = 0.5
    trustworthiness: float = 0.5
    source_ids: list[str] = field(default_factory=list)
    contradiction_status: str = "none"  # none, flagged, quarantined, resolved
    created_at: datetime = field(default_factory=_now)
    last_accessed: datetime = field(default_factory=_now)
    access_count: int = 0
    status: NodeStatus = NodeStatus.ACTIVE

    @property
    def trust_useful(self) -> float:
        return self.base_usefulness * self.trustworthiness


@dataclass
class NodeFilter:
    """Filters for querying nodes."""

    type: NodeType | None = None
    status: NodeStatus | None = None
    canonical: bool | None = None
    trust_min: float | None = None
    trust_max: float | None = None
    usefulness_min: float | None = None
    usefulness_max: float | None = None
    text_contains: str | None = None
    limit: int = 50
    offset: int = 0


@dataclass
class NodeUpdate:
    """Partial update for a node."""

    text: str | None = None
    search_text: str | None = None
    base_usefulness: float | None = None
    trustworthiness: float | None = None
    salience: float | None = None
    emotion_label: str | None = None
    emotion_score: float | None = None
    centrality: float | None = None
    status: NodeStatus | None = None
    canonical: bool | None = None
    access_count: int | None = None
    last_accessed: datetime | None = None
