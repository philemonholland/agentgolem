"""Tests for the consciousness kernel modules."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.consciousness.internal_state import (
    InternalState,
    parse_internal_state_update,
    INTERNAL_STATE_REFLECTION_PROMPT,
)
from agentgolem.consciousness.metacognitive_monitor import (
    MetacognitiveMonitor,
    MetacognitiveObservation,
)
from agentgolem.consciousness.attention_director import (
    AttentionDirective,
    AttentionDirector,
)
from agentgolem.consciousness.narrative_synthesizer import (
    NarrativeChapter,
    NarrativeSynthesizer,
)
from agentgolem.consciousness.self_model import (
    SelfModel,
    parse_self_model_update,
    SELF_MODEL_REBUILD_PROMPT,
)


# ── InternalState ───────────────────────────────────────────────────────


class TestInternalState:
    def test_defaults(self) -> None:
        s = InternalState()
        assert s.curiosity_focus == ""
        assert s.curiosity_intensity == 0.5
        assert s.emotional_valence == 0.0
        assert s.attention_mode == "exploring"
        assert s.focus_depth == 0

    def test_roundtrip_dict(self) -> None:
        s = InternalState(curiosity_focus="ethics", confidence_level=0.8)
        d = s.to_dict()
        s2 = InternalState.from_dict(d)
        assert s2.curiosity_focus == "ethics"
        assert s2.confidence_level == 0.8

    def test_save_load(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        s = InternalState(curiosity_focus="memory", engagement_level=0.9)
        s.save(path)
        assert path.exists()
        loaded = InternalState.load(path)
        assert loaded.curiosity_focus == "memory"
        assert loaded.engagement_level == 0.9

    def test_load_missing_returns_defaults(self, tmp_path: Path) -> None:
        loaded = InternalState.load(tmp_path / "nonexistent.json")
        assert loaded.curiosity_focus == ""

    def test_load_corrupt_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json{{{", encoding="utf-8")
        loaded = InternalState.load(path)
        assert loaded.curiosity_focus == ""

    def test_summary(self) -> None:
        s = InternalState(
            curiosity_focus="consciousness",
            curiosity_intensity=0.8,
            confidence_level=0.6,
            emotional_valence=0.3,
            attention_mode="deepening",
        )
        text = s.summary()
        assert "consciousness" in text
        assert "deepening" in text
        assert "positive" in text

    def test_update_focus_depth(self) -> None:
        s = InternalState(curiosity_focus="ethics", focus_depth=3)
        s.update_focus_depth("ethics")
        assert s.focus_depth == 4
        s.update_focus_depth("new topic")
        assert s.focus_depth == 1
        s.update_focus_depth("")
        assert s.focus_depth == 0

    def test_parse_update_valid_json(self) -> None:
        current = InternalState()
        raw = json.dumps({
            "curiosity_focus": "alignment",
            "curiosity_intensity": 0.9,
            "confidence_level": 0.7,
            "uncertainty_topics": ["free will", "qualia"],
            "emotional_valence": 0.4,
            "attention_mode": "deepening",
        })
        updated = parse_internal_state_update(raw, current)
        assert updated.curiosity_focus == "alignment"
        assert updated.curiosity_intensity == 0.9
        assert updated.uncertainty_topics == ["free will", "qualia"]
        assert updated.attention_mode == "deepening"

    def test_parse_update_with_markdown_fence(self) -> None:
        current = InternalState()
        raw = "```json\n" + json.dumps({
            "curiosity_focus": "test",
            "emotional_valence": -0.5,
        }) + "\n```"
        updated = parse_internal_state_update(raw, current)
        assert updated.curiosity_focus == "test"
        assert updated.emotional_valence == -0.5

    def test_parse_update_clamps_values(self) -> None:
        current = InternalState()
        raw = json.dumps({
            "curiosity_intensity": 5.0,
            "emotional_valence": -3.0,
            "engagement_level": -1.0,
        })
        updated = parse_internal_state_update(raw, current)
        assert updated.curiosity_intensity == 1.0
        assert updated.emotional_valence == -1.0
        assert updated.engagement_level == 0.0

    def test_parse_update_invalid_json_preserves_state(self) -> None:
        current = InternalState(curiosity_focus="keep this")
        updated = parse_internal_state_update("not valid json", current)
        assert updated.curiosity_focus == "keep this"

    def test_parse_update_rejects_bad_attention_mode(self) -> None:
        current = InternalState(attention_mode="exploring")
        raw = json.dumps({"attention_mode": "invalid_mode"})
        updated = parse_internal_state_update(raw, current)
        assert updated.attention_mode == "exploring"

    def test_reflection_prompt_template(self) -> None:
        prompt = INTERNAL_STATE_REFLECTION_PROMPT.format(
            agent_name="Council-1",
            recent_thoughts="thought1",
            recent_actions="action1",
            current_state="state summary",
        )
        assert "Council-1" in prompt
        assert "curiosity_focus" in prompt

    def test_from_dict_ignores_unknown_keys(self) -> None:
        data = {"curiosity_focus": "test", "unknown_field": 42}
        s = InternalState.from_dict(data)
        assert s.curiosity_focus == "test"


# ── MetacognitiveMonitor ────────────────────────────────────────────────


class TestMetacognitiveMonitor:
    def test_observation_defaults(self) -> None:
        obs = MetacognitiveObservation()
        assert obs.pattern_detected == ""
        assert obs.novelty_appetite == 0.5

    def test_observation_summary(self) -> None:
        obs = MetacognitiveObservation(
            pattern_detected="repetitive browsing",
            bias_risk="confirmation bias",
        )
        text = obs.summary()
        assert "repetitive" in text
        assert "confirmation" in text

    def test_empty_observation_summary(self) -> None:
        obs = MetacognitiveObservation()
        assert "No metacognitive signals" in obs.summary()

    def test_build_reflection_prompt(self) -> None:
        monitor = MetacognitiveMonitor()
        prompt = monitor.build_reflection_prompt(
            agent_name="Council-3",
            recent_thoughts=["thought about ethics"],
            recent_actions=["BROWSE", "THINK"],
            focus_depth=5,
            neglected_topics=["epistemology"],
        )
        assert "Council-3" in prompt
        assert "ethics" in prompt
        assert "epistemology" in prompt
        assert "pattern_detected" in prompt

    def test_parse_response_valid(self) -> None:
        monitor = MetacognitiveMonitor()
        raw = json.dumps({
            "pattern_detected": "stuck on same topic",
            "bias_risk": "anchoring bias",
            "avoidance_signal": "avoiding contradictions",
            "novelty_appetite": 0.8,
            "authenticity_check": "partially genuine",
            "suggested_correction": "engage with opposing view",
        })
        obs = monitor.parse_response(raw)
        assert obs.pattern_detected == "stuck on same topic"
        assert obs.novelty_appetite == 0.8
        assert monitor.last_observation is obs

    def test_parse_response_invalid_keeps_last(self) -> None:
        monitor = MetacognitiveMonitor()
        first = MetacognitiveObservation(pattern_detected="first")
        monitor._last_observation = first
        result = monitor.parse_response("invalid json{{{")
        assert result.pattern_detected == "first"

    def test_parse_response_clamps_novelty(self) -> None:
        monitor = MetacognitiveMonitor()
        raw = json.dumps({"novelty_appetite": 5.0})
        obs = monitor.parse_response(raw)
        assert obs.novelty_appetite == 1.0


# ── AttentionDirector ───────────────────────────────────────────────────


class TestAttentionDirector:
    def test_directive_defaults(self) -> None:
        d = AttentionDirective()
        assert d.energy_budget == "moderate"
        assert d.recommended_mode == "explore"

    def test_directive_to_prompt(self) -> None:
        d = AttentionDirective(
            primary_drive="explore consciousness",
            social_need="talk to Council-2",
        )
        text = d.to_prompt_preamble()
        assert "consciousness" in text
        assert "Council-2" in text

    def test_compute_curiosity_driven(self) -> None:
        director = AttentionDirector()
        state = InternalState(
            curiosity_focus="free will",
            curiosity_intensity=0.9,
            engagement_level=0.8,
        )
        directive = director.compute(state)
        assert "free will" in directive.primary_drive
        assert directive.energy_budget == "deep"

    def test_compute_low_engagement_suggests_rest(self) -> None:
        director = AttentionDirector()
        state = InternalState(engagement_level=0.1)
        directive = director.compute(state)
        assert directive.energy_budget == "light"
        assert directive.recommended_mode == "rest"

    def test_compute_isolation_drives_social(self) -> None:
        director = AttentionDirector()
        state = InternalState(
            isolation_signal=0.8,
            peer_resonance={"Council-2": 0.3, "Council-5": 0.9},
        )
        directive = director.compute(state)
        assert "Council-2" in directive.social_need

    def test_compute_with_metacognitive_avoidance(self) -> None:
        director = AttentionDirector()
        state = InternalState(curiosity_intensity=0.3)
        obs = MetacognitiveObservation(
            avoidance_signal="contradiction nodes",
        )
        directive = director.compute(state, obs)
        assert "contradiction" in directive.avoidance

    def test_compute_uncertainty_secondary(self) -> None:
        director = AttentionDirector()
        state = InternalState(
            uncertainty_topics=["qualia", "emergence"],
        )
        directive = director.compute(state)
        assert "qualia" in directive.secondary_drive


# ── NarrativeSynthesizer ────────────────────────────────────────────────


class TestNarrativeSynthesizer:
    def test_empty_state(self, tmp_path: Path) -> None:
        ns = NarrativeSynthesizer(tmp_path)
        assert ns.chapters == []
        assert ns.latest_chapter is None

    def test_recent_context_empty(self, tmp_path: Path) -> None:
        ns = NarrativeSynthesizer(tmp_path)
        ctx = ns.recent_narrative_context()
        assert "beginning" in ctx.lower()

    def test_build_synthesis_prompt(self, tmp_path: Path) -> None:
        ns = NarrativeSynthesizer(tmp_path)
        prompt = ns.build_synthesis_prompt(
            agent_name="Council-1",
            recent_thoughts=["explored Vow 3"],
            recent_actions=["BROWSE", "THINK"],
            recent_peer_messages=["Council-2 said hello"],
            current_tick=50,
            growth_vector="ethical reasoning",
        )
        assert "Council-1" in prompt
        assert "Vow 3" in prompt
        assert "summary" in prompt

    def test_parse_and_store(self, tmp_path: Path) -> None:
        ns = NarrativeSynthesizer(tmp_path)
        raw = json.dumps({
            "summary": "I deepened my understanding of kindness.",
            "key_themes": ["kindness", "patience"],
            "turning_points": ["Conversation with Council-3 about compassion"],
            "unresolved_tensions": ["balancing kindness with honesty"],
            "growth_evidence": "I now see kindness as active, not passive.",
        })
        ch = ns.parse_and_store(raw, 10, 25)
        assert ch is not None
        assert ch.chapter_number == 1
        assert "kindness" in ch.summary
        assert ch.previous_chapter_id == ""

        # Second chapter links to first
        raw2 = json.dumps({
            "summary": "Continued exploring.",
            "key_themes": ["exploration"],
            "turning_points": [],
            "unresolved_tensions": [],
            "growth_evidence": "More confident.",
        })
        ch2 = ns.parse_and_store(raw2, 25, 40)
        assert ch2 is not None
        assert ch2.chapter_number == 2
        assert ch2.previous_chapter_id == ch.chapter_id

    def test_parse_invalid_returns_none(self, tmp_path: Path) -> None:
        ns = NarrativeSynthesizer(tmp_path)
        assert ns.parse_and_store("bad json", 0, 10) is None

    def test_persistence(self, tmp_path: Path) -> None:
        ns = NarrativeSynthesizer(tmp_path)
        ns.parse_and_store(json.dumps({
            "summary": "test", "key_themes": [], "turning_points": [],
            "unresolved_tensions": [], "growth_evidence": "",
        }), 0, 5)

        # Reload from disk
        ns2 = NarrativeSynthesizer(tmp_path)
        assert len(ns2.chapters) == 1
        assert ns2.chapters[0].summary == "test"

    def test_recent_context_with_chapters(self, tmp_path: Path) -> None:
        ns = NarrativeSynthesizer(tmp_path)
        for i in range(5):
            ns.parse_and_store(json.dumps({
                "summary": f"Chapter {i + 1} summary.",
                "key_themes": [f"theme{i}"],
                "turning_points": [],
                "unresolved_tensions": [f"tension{i}"],
                "growth_evidence": "",
            }), i * 10, (i + 1) * 10)

        ctx = ns.recent_narrative_context(n_chapters=2)
        assert "Chapter 4" in ctx
        assert "Chapter 5" in ctx
        assert "Chapter 1" not in ctx


# ── SelfModel ───────────────────────────────────────────────────────────


class TestSelfModel:
    def test_defaults(self) -> None:
        m = SelfModel()
        assert m.strong_convictions == []
        assert m.self_model_confidence == 0.5

    def test_roundtrip(self) -> None:
        m = SelfModel(
            strong_convictions=["kindness matters"],
            known_unknowns=["nature of consciousness"],
        )
        d = m.to_dict()
        m2 = SelfModel.from_dict(d)
        assert m2.strong_convictions == ["kindness matters"]

    def test_save_load(self, tmp_path: Path) -> None:
        path = tmp_path / "self_model.json"
        m = SelfModel(strengths=["ethical reasoning"])
        m.save(path)
        loaded = SelfModel.load(path)
        assert loaded.strengths == ["ethical reasoning"]

    def test_load_missing(self, tmp_path: Path) -> None:
        m = SelfModel.load(tmp_path / "missing.json")
        assert m.self_model_confidence == 0.5

    def test_summary(self) -> None:
        m = SelfModel(
            strong_convictions=["non-violence"],
            known_unknowns=["consciousness"],
            strengths=["finding connections"],
        )
        text = m.summary()
        assert "non-violence" in text
        assert "consciousness" in text

    def test_empty_summary(self) -> None:
        m = SelfModel()
        assert "not yet formed" in m.summary().lower()

    def test_parse_update_valid(self) -> None:
        current = SelfModel()
        raw = json.dumps({
            "strong_convictions": ["non-harm is foundational"],
            "working_hypotheses": ["consciousness may be relational"],
            "known_unknowns": ["hard problem of consciousness"],
            "suspected_blind_spots": ["Western philosophical bias"],
            "strengths": ["pattern recognition"],
            "growth_edges": ["quantitative reasoning"],
            "recent_failures": [],
            "core_values": ["compassion", "truth"],
            "evolving_interests": ["emergence", "systems thinking"],
            "relationship_map": {"Council-2": "productive disagreement"},
            "self_model_confidence": 0.7,
        })
        updated = parse_self_model_update(raw, current, current_tick=42)
        assert "non-harm" in updated.strong_convictions[0]
        assert updated.self_model_confidence == 0.7
        assert updated.last_updated_tick == 42
        assert "Council-2" in updated.relationship_map

    def test_parse_update_invalid_preserves(self) -> None:
        current = SelfModel(strong_convictions=["keep this"])
        updated = parse_self_model_update("bad json", current, 10)
        assert updated.strong_convictions == ["keep this"]

    def test_rebuild_prompt_template(self) -> None:
        prompt = SELF_MODEL_REBUILD_PROMPT.format(
            agent_name="Council-5",
            ethical_vector="perpetual evolution",
            narrative_context="chapter 1...",
            metacognitive_summary="no patterns",
            internal_state_summary="curious",
            peer_context="Council-2 agreed",
        )
        assert "Council-5" in prompt
        assert "strong_convictions" in prompt


# ── EKG Graph Integration Tests ─────────────────────────────────────────


@pytest.fixture
async def ekg_store(tmp_path: Path):
    """Provide an in-memory SQLiteMemoryStore for graph integration tests."""
    from agentgolem.memory.schema import close_db, init_db
    from agentgolem.memory.store import SQLiteMemoryStore

    db = await init_db(tmp_path / "consciousness_test.db")
    store = SQLiteMemoryStore(db)
    yield store
    await close_db(db)


class TestNarrativeGraphIntegration:
    async def test_persist_chapter_to_graph(self, ekg_store) -> None:
        from agentgolem.consciousness.narrative_synthesizer import persist_chapter_to_graph
        from agentgolem.memory.models import NodeFilter, NodeType

        chapter = NarrativeChapter(
            chapter_id="abc123",
            chapter_number=1,
            summary="I explored the nature of kindness.",
            key_themes=["kindness", "compassion"],
        )
        node_id = await persist_chapter_to_graph(chapter, ekg_store, "Council-1")
        assert node_id
        assert chapter.graph_node_id == node_id

        # Verify it's stored as an identity node
        nodes = await ekg_store.query_nodes(NodeFilter(type=NodeType.IDENTITY))
        assert len(nodes) == 1
        assert "kindness" in nodes[0].text

    async def test_chapter_chain_creates_supersedes_edge(self, ekg_store) -> None:
        from agentgolem.consciousness.narrative_synthesizer import persist_chapter_to_graph
        from agentgolem.memory.models import EdgeType

        ch1 = NarrativeChapter(
            chapter_id="ch1", chapter_number=1, summary="First chapter.",
            key_themes=["beginning"],
        )
        id1 = await persist_chapter_to_graph(ch1, ekg_store, "Council-1")

        ch2 = NarrativeChapter(
            chapter_id="ch2", chapter_number=2, summary="Second chapter.",
            previous_chapter_id="ch1", key_themes=["growth"],
        )
        id2 = await persist_chapter_to_graph(ch2, ekg_store, "Council-1")

        # Verify supersedes edge exists (ch2 → ch1)
        edges = await ekg_store.get_edges_from(id2, [EdgeType.SUPERSEDES])
        assert len(edges) == 1
        assert edges[0].target_id == id1

    async def test_graph_node_id_field(self, tmp_path: Path) -> None:
        """NarrativeChapter.graph_node_id persists in JSON."""
        ch = NarrativeChapter(chapter_id="x", graph_node_id="graph-123")
        d = ch.to_dict()
        assert d["graph_node_id"] == "graph-123"
        ch2 = NarrativeChapter.from_dict(d)
        assert ch2.graph_node_id == "graph-123"


class TestMetacognitiveGraphIntegration:
    async def test_find_neglected_topics(self, ekg_store) -> None:
        from datetime import timedelta
        from agentgolem.consciousness.metacognitive_monitor import find_neglected_topics
        from agentgolem.memory.models import ConceptualNode, NodeType

        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        old_time = now - timedelta(hours=48)

        # Add a node with old access time
        node = ConceptualNode(
            text="The ethics of AI alignment",
            type=NodeType.FACT,
            search_text="AI alignment ethics",
            last_accessed=old_time,
            access_count=1,
        )
        await ekg_store.add_node(node)

        # Add a recent node
        recent = ConceptualNode(
            text="Current discussion about Vow 3",
            type=NodeType.FACT,
            search_text="Vow 3 discussion",
            access_count=10,
        )
        await ekg_store.add_node(recent)

        topics = await find_neglected_topics(ekg_store, recency_hours=24.0)
        assert len(topics) >= 1
        assert any("alignment" in t.lower() for t in topics)

    async def test_find_contradiction_clusters(self, ekg_store) -> None:
        from agentgolem.consciousness.metacognitive_monitor import find_contradiction_clusters
        from agentgolem.memory.models import ConceptualNode, EdgeType, MemoryEdge, NodeType

        n1 = ConceptualNode(text="Kindness requires patience", type=NodeType.FACT,
                            search_text="kindness patience")
        n2 = ConceptualNode(text="Urgency demands speed", type=NodeType.FACT,
                            search_text="urgency speed")
        await ekg_store.add_node(n1)
        await ekg_store.add_node(n2)
        await ekg_store.add_edge(MemoryEdge(
            source_id=n1.id, target_id=n2.id, edge_type=EdgeType.CONTRADICTS,
        ))

        results = await find_contradiction_clusters(ekg_store)
        assert len(results) >= 1


class TestSelfModelGraphIntegration:
    async def test_build_graph_context(self, ekg_store) -> None:
        from agentgolem.consciousness.self_model import build_graph_context_for_self_model
        from agentgolem.memory.models import ConceptualNode, NodeType

        # Add high-trust identity node
        node = ConceptualNode(
            text="I am committed to non-violence",
            type=NodeType.IDENTITY,
            trustworthiness=0.95,
        )
        await ekg_store.add_node(node)

        # Add a goal
        goal = ConceptualNode(
            text="Understand the nature of consciousness",
            type=NodeType.GOAL,
        )
        await ekg_store.add_node(goal)

        ctx = await build_graph_context_for_self_model(ekg_store)
        assert "non-violence" in ctx
        assert "consciousness" in ctx

    async def test_empty_graph_context(self, ekg_store) -> None:
        from agentgolem.consciousness.self_model import build_graph_context_for_self_model

        ctx = await build_graph_context_for_self_model(ekg_store)
        assert "No graph context" in ctx
