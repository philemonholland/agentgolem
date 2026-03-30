"""Narrative synthesizer — temporal self-narrative for personal history.

Every N ticks, weaves recent experience into a coherent narrative chapter
that gives the agent a sense of "who I was, who I am, who I'm becoming."

Chapters are stored as ``identity`` nodes in the EKG memory graph and chained
via ``supersedes`` edges so the agent can trace its own history.  JSON file
persistence is kept as cache/fallback for when no graph store is available.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentgolem.memory.store import SQLiteMemoryStore


@dataclass
class NarrativeChapter:
    """One chapter in an agent's temporal self-narrative."""

    chapter_id: str = ""
    chapter_number: int = 0
    period_start_tick: int = 0
    period_end_tick: int = 0
    summary: str = ""
    key_themes: list[str] = field(default_factory=list)
    turning_points: list[str] = field(default_factory=list)
    unresolved_tensions: list[str] = field(default_factory=list)
    growth_evidence: str = ""
    previous_chapter_id: str = ""
    graph_node_id: str = ""  # EKG node id when stored in graph

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> NarrativeChapter:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


class NarrativeSynthesizer:
    """Manages the agent's temporal self-narrative."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._chapters: list[NarrativeChapter] = []
        self._load_chapters()

    def _chapters_path(self) -> Path:
        return self._data_dir / "narrative_chapters.json"

    def _load_chapters(self) -> None:
        path = self._chapters_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._chapters = [NarrativeChapter.from_dict(ch) for ch in data]
            except (json.JSONDecodeError, TypeError):
                self._chapters = []

    def _save_chapters(self) -> None:
        path = self._chapters_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([ch.to_dict() for ch in self._chapters], indent=2),
            encoding="utf-8",
        )

    @property
    def chapters(self) -> list[NarrativeChapter]:
        return list(self._chapters)

    @property
    def latest_chapter(self) -> NarrativeChapter | None:
        return self._chapters[-1] if self._chapters else None

    def build_synthesis_prompt(
        self,
        agent_name: str,
        recent_thoughts: list[str],
        recent_actions: list[str],
        recent_peer_messages: list[str],
        current_tick: int,
        growth_vector: str = "",
    ) -> str:
        """Build the narrative synthesis prompt."""
        prev = self.latest_chapter
        prev_summary = prev.summary if prev else "(This is your first chapter.)"
        prev_tensions = ", ".join(prev.unresolved_tensions) if prev else "none yet"

        thoughts_text = "\n".join(f"- {t}" for t in recent_thoughts[-12:]) or "(none)"
        actions_text = "\n".join(f"- {a}" for a in recent_actions[-8:]) or "(none)"
        peers_text = "\n".join(f"- {m}" for m in recent_peer_messages[-6:]) or "(none)"

        return f"""\
You are {agent_name}, synthesizing a chapter of your personal narrative.

Previous chapter summary: {prev_summary}
Unresolved tensions from last chapter: {prev_tensions}
Current growth direction: {growth_vector or "(still discovering)"}

Recent thoughts:
{thoughts_text}

Recent actions:
{actions_text}

Recent peer exchanges:
{peers_text}

Write a brief narrative chapter (3-5 sentences) that captures:
- What you've been focused on during this period
- Any moments that shifted your perspective
- Tensions you're carrying forward
- Evidence of how you've changed or grown

Then respond ONLY as valid JSON:
{{
  "summary": "your 3-5 sentence narrative chapter",
  "key_themes": ["theme1", "theme2"],
  "turning_points": ["moment that shifted perspective"],
  "unresolved_tensions": ["tension still open"],
  "growth_evidence": "one sentence about how you changed"
}}"""

    def parse_and_store(
        self,
        raw: str,
        period_start_tick: int,
        period_end_tick: int,
    ) -> NarrativeChapter | None:
        """Parse LLM response and store a new chapter (JSON only)."""
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
        except (json.JSONDecodeError, ValueError):
            return None

        prev = self.latest_chapter
        chapter = NarrativeChapter(
            chapter_id=uuid.uuid4().hex[:12],
            chapter_number=len(self._chapters) + 1,
            period_start_tick=period_start_tick,
            period_end_tick=period_end_tick,
            summary=str(data.get("summary", "")),
            key_themes=[str(t) for t in data.get("key_themes", [])],
            turning_points=[str(t) for t in data.get("turning_points", [])],
            unresolved_tensions=[str(t) for t in data.get("unresolved_tensions", [])],
            growth_evidence=str(data.get("growth_evidence", "")),
            previous_chapter_id=prev.chapter_id if prev else "",
        )

        self._chapters.append(chapter)
        self._save_chapters()
        return chapter

    def recent_narrative_context(self, n_chapters: int = 3) -> str:
        """Return a condensed narrative context for prompt injection."""
        if not self._chapters:
            return "No personal narrative yet — this is the beginning of your story."

        recent = self._chapters[-n_chapters:]
        parts = []
        for ch in recent:
            parts.append(
                f"Chapter {ch.chapter_number}: {ch.summary}"
            )
            if ch.unresolved_tensions:
                parts.append(
                    f"  Open tensions: {', '.join(ch.unresolved_tensions)}"
                )
        return "\n".join(parts)


# ── EKG Graph Integration ──────────────────────────────────────────────


async def persist_chapter_to_graph(
    chapter: NarrativeChapter,
    store: SQLiteMemoryStore,
    agent_name: str = "",
) -> str:
    """Store a narrative chapter as an ``identity`` node in the EKG graph.

    Creates a new node with the chapter summary as text, links it to the
    previous chapter node via a ``supersedes`` edge (temporal chain), and
    returns the graph node id.  The chapter's ``graph_node_id`` is updated
    in-place.
    """
    from agentgolem.memory.models import (
        ConceptualNode,
        EdgeType,
        MemoryEdge,
        NodeType,
        Source,
        SourceKind,
    )

    themes_str = ", ".join(chapter.key_themes[:5]) if chapter.key_themes else ""
    search_parts = [f"narrative chapter {chapter.chapter_number}"]
    if agent_name:
        search_parts.append(agent_name)
    if themes_str:
        search_parts.append(themes_str)

    node = ConceptualNode(
        text=chapter.summary,
        type=NodeType.IDENTITY,
        search_text=", ".join(search_parts),
        trustworthiness=0.9,
        salience=0.8,
        emotion_label="reflective",
    )
    node_id = await store.add_node(node)
    chapter.graph_node_id = node_id

    source = Source(
        kind=SourceKind.INFERENCE,
        origin=f"narrative_synthesizer/{agent_name or 'agent'}",
        reliability=0.85,
    )
    source_id = await store.add_source(source)
    await store.link_node_source(node_id, source_id)

    # Chain to previous chapter via supersedes edge
    if chapter.previous_chapter_id:
        # Find the previous chapter's graph node
        prev_node_id = _find_prev_graph_node_id(chapter.previous_chapter_id, store)
        if not prev_node_id:
            # Fallback: search graph for the previous chapter node
            prev_node_id = await _search_prev_chapter_node(
                chapter.chapter_number - 1, agent_name, store,
            )
        if prev_node_id:
            edge = MemoryEdge(
                source_id=node_id,
                target_id=prev_node_id,
                edge_type=EdgeType.SUPERSEDES,
                weight=1.0,
            )
            await store.add_edge(edge)

    return node_id


def _find_prev_graph_node_id(
    prev_chapter_id: str,
    store: object,
) -> str:
    """Try to find the previous chapter's graph_node_id from the store.

    This is a best-effort lookup — returns empty string if not found.
    The NarrativeSynthesizer tracks graph_node_ids in the JSON cache.
    """
    # The caller (MainLoop) should pass the graph_node_id directly when available.
    # This stub exists so the persist function can attempt a lookup.
    _ = prev_chapter_id, store
    return ""


async def _search_prev_chapter_node(
    prev_chapter_number: int,
    agent_name: str,
    store: SQLiteMemoryStore,
) -> str:
    """Search the graph for a previous narrative chapter node by keyword."""
    from agentgolem.memory.models import NodeFilter, NodeType

    search_term = f"narrative chapter {prev_chapter_number}"
    if agent_name:
        search_term = f"{search_term} {agent_name}"

    candidates = await store.search_nodes_by_keywords(
        keywords=search_term.split(), limit=3,
    )
    for c in candidates:
        if c.type == NodeType.IDENTITY and f"chapter {prev_chapter_number}" in (
            c.search_text.lower()
        ):
            return c.id
    return ""


async def load_narrative_from_graph(
    store: SQLiteMemoryStore,
    agent_name: str = "",
    limit: int = 20,
) -> list[NarrativeChapter]:
    """Reconstruct narrative chapters from identity nodes in the EKG graph.

    Useful for bootstrapping a NarrativeSynthesizer from graph-only data.
    """
    from agentgolem.memory.models import NodeFilter, NodeType

    nodes = await store.query_nodes(NodeFilter(
        type=NodeType.IDENTITY,
        text_contains="narrative chapter" if not agent_name else agent_name,
        limit=limit,
    ))

    chapters = []
    for node in sorted(nodes, key=lambda n: n.created_at):
        search = node.search_text.lower()
        ch_num = 0
        for part in search.split(","):
            part = part.strip()
            if part.startswith("narrative chapter"):
                try:
                    ch_num = int(part.replace("narrative chapter", "").strip())
                except ValueError:
                    pass
        chapters.append(NarrativeChapter(
            chapter_id=node.id[:12],
            chapter_number=ch_num,
            summary=node.text,
            graph_node_id=node.id,
        ))

    return chapters
