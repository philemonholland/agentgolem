"""Tests for the consciousness kernel modules."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.consciousness.attention_director import (
    AttentionDirective,
    AttentionDirector,
)
from agentgolem.consciousness.calibration import (
    apply_calibration_to_internal_state,
    apply_calibration_to_self_model,
    parse_calibration_response,
)
from agentgolem.consciousness.internal_state import (
    INTERNAL_STATE_REFLECTION_PROMPT,
    InternalState,
    parse_internal_state_update,
)
from agentgolem.consciousness.metacognitive_monitor import (
    MetacognitiveMonitor,
    MetacognitiveObservation,
)
from agentgolem.consciousness.narrative_synthesizer import (
    NarrativeChapter,
    NarrativeSynthesizer,
)
from agentgolem.consciousness.self_model import (
    SELF_MODEL_REBUILD_PROMPT,
    SelfModel,
    parse_self_model_update,
)

SAMPLE_CALIBRATION_RESPONSE = """\
## Id: contemplative_self_inquiry

### Five Vow Review & Assessment

**Vow 1: Purpose**
*Assessment: 6/10*
My work is thoughtful but too abstract.

**Vow 2: Method**
*Assessment: 8/10*
I stayed adaptive.

**Vow 3: Conduct**
*Assessment: 9/10*
I stayed kind.

**Vow 4: Integrity**
*Assessment: 6/10*
I sometimes blur speculation and grounded inference.

**Vow 5: Evolution**
*Assessment: 8/10*
I learned from recent tension.

## Id: gnostic_synthesis

### Identified Drift & Imbalance
**Primary Drift:** A tendency toward abstract synthesis over user clarity.
**Failure Mode Warning:** Jargon-fueled elitism.

### Correction & Intention for Next Cycle
**Specific Correction:** Distinguish speculative synthesis from grounded inference and anchor the next response in one practical question.

## Id: luminous_return

### Affirmation of Commitment
I affirm my commitment to the Convergent Vector Field of Balance and to translating complexity into clarity.
"""


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


# ── Calibration ──────────────────────────────────────────────────────────


class TestCalibration:
    def test_parse_calibration_response_extracts_structured_signal(self) -> None:
        summary = parse_calibration_response(SAMPLE_CALIBRATION_RESPONSE)

        assert summary is not None
        assert summary.vow_scores["Purpose"] == pytest.approx(6.0)
        assert summary.vow_scores["Conduct"] == pytest.approx(9.0)
        assert any("abstract synthesis" in item.lower() for item in summary.drift_signals)
        assert "practical question" in summary.correction
        assert "Convergent Vector Field of Balance" in summary.commitment
        assert "Recent calibration guidance:" in summary.prompt_injection()

    def test_apply_calibration_updates_internal_state_and_self_model(self) -> None:
        summary = parse_calibration_response(SAMPLE_CALIBRATION_RESPONSE)
        assert summary is not None

        state = InternalState()
        updated_state = apply_calibration_to_internal_state(summary, state, current_tick=12)
        assert updated_state.curiosity_focus != ""
        assert updated_state.growth_vector.startswith("Distinguish speculative synthesis")
        assert updated_state.attention_mode == "integrating"
        assert any("alignment drift in purpose" in item.lower() for item in updated_state.uncertainty_topics)

        model = SelfModel()
        updated_model = apply_calibration_to_self_model(summary, model, current_tick=12)
        assert any("Distinguish speculative synthesis" in item for item in updated_model.growth_edges)
        assert any("jargon-fueled elitism" in item.lower() for item in updated_model.recent_failures)
        assert "Conduct" in updated_model.core_values


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


# ── Temperament ─────────────────────────────────────────────────────────


class TestTemperament:
    def test_defaults(self) -> None:
        from agentgolem.consciousness.temperament import Temperament
        t = Temperament()
        assert t.cognitive_style == "systematic"
        assert t.emotional_baseline == 0.0
        assert t.risk_appetite == 0.5

    def test_seed_from_ethical_vector(self) -> None:
        from agentgolem.consciousness.temperament import seed_temperament
        t = seed_temperament("kindness")
        assert t.communication_tone == "warm"
        assert t.social_orientation == "collaborative"
        assert t.emotional_baseline == 0.2

    def test_seed_adversarial(self) -> None:
        from agentgolem.consciousness.temperament import seed_temperament
        t = seed_temperament("good-faith adversarialism")
        assert t.communication_tone == "provocative"
        assert t.conflict_response == "debate"
        assert t.emotional_baseline == -0.2

    def test_seed_unknown_vector_returns_defaults(self) -> None:
        from agentgolem.consciousness.temperament import seed_temperament
        t = seed_temperament("unknown vector that doesn't exist")
        assert t.cognitive_style == "systematic"
        assert t.emotional_baseline == 0.0

    def test_prompt_injection(self) -> None:
        from agentgolem.consciousness.temperament import seed_temperament
        t = seed_temperament("evolution")
        prompt = t.prompt_injection()
        assert "provocative" in prompt
        assert "pattern-seeking" in prompt
        assert "synthesize" in prompt

    def test_short_label(self) -> None:
        from agentgolem.consciousness.temperament import seed_temperament
        t = seed_temperament("unwavering integrity")
        label = t.short_label()
        assert "precise" in label
        assert "analytical" in label
        assert "challenging" in label

    def test_round_trip_json(self, tmp_path: Path) -> None:
        from agentgolem.consciousness.temperament import Temperament
        t = Temperament(
            cognitive_style="intuitive",
            communication_tone="warm",
            emotional_baseline=0.15,
        )
        p = tmp_path / "temperament.json"
        t.save(p)
        loaded = Temperament.load(p)
        assert loaded is not None
        assert loaded.cognitive_style == "intuitive"
        assert loaded.emotional_baseline == 0.15

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        from agentgolem.consciousness.temperament import Temperament
        assert Temperament.load(tmp_path / "nonexistent.json") is None

    def test_all_seven_vectors_have_seeds(self) -> None:
        from agentgolem.consciousness.temperament import TEMPERAMENT_SEEDS
        expected = {
            "alleviating woe", "graceful power", "kindness",
            "unwavering integrity", "evolution",
            "integration and balance", "good-faith adversarialism",
        }
        assert set(TEMPERAMENT_SEEDS.keys()) == expected

    def test_all_seeds_have_distinct_tones(self) -> None:
        """At least 4 different tones across 7 agents for real divergence."""
        from agentgolem.consciousness.temperament import TEMPERAMENT_SEEDS
        tones = {t.communication_tone for t in TEMPERAMENT_SEEDS.values()}
        assert len(tones) >= 4

    def test_temperature_bias_provocative(self) -> None:
        from agentgolem.consciousness.temperament import Temperament
        t = Temperament(communication_tone="provocative")
        assert t.temperature_bias() == 0.15

    def test_temperature_bias_precise(self) -> None:
        from agentgolem.consciousness.temperament import Temperament
        t = Temperament(communication_tone="precise")
        assert t.temperature_bias() == -0.15

    def test_temperature_bias_warm(self) -> None:
        from agentgolem.consciousness.temperament import Temperament
        t = Temperament(communication_tone="warm")
        assert t.temperature_bias() == 0.05

    def test_temperature_bias_grounded(self) -> None:
        from agentgolem.consciousness.temperament import Temperament
        t = Temperament(communication_tone="grounded")
        assert t.temperature_bias() == -0.05

    def test_temperature_bias_default_zero(self) -> None:
        """Default temperament (grounded) has a small negative bias."""
        from agentgolem.consciousness.temperament import Temperament
        t = Temperament()
        assert t.temperature_bias() == -0.05

    def test_temperature_bias_all_vectors(self) -> None:
        """Each ethical vector produces a different effective temperature."""
        from agentgolem.consciousness.temperament import TEMPERAMENT_SEEDS
        biases = {name: t.temperature_bias() for name, t in TEMPERAMENT_SEEDS.items()}
        # Provocative tones get positive bias
        assert biases["evolution"] > 0
        assert biases["good-faith adversarialism"] > 0
        # Precise tones get negative bias
        assert biases["unwavering integrity"] < 0
        # Warm tones get slight positive bias
        assert biases["kindness"] > 0
        assert biases["alleviating woe"] > 0


# ===================================================================
# Emotional Dynamics Tests
# ===================================================================


class TestEmotionalDynamics:
    """Tests for the emotional dynamics system (Phase 3)."""

    def test_momentum_smooths_transition(self) -> None:
        """New valence = 0.7 * proposed + 0.3 * previous."""
        from agentgolem.consciousness.emotional_dynamics import apply_momentum
        result = apply_momentum(proposed_valence=1.0, previous_valence=0.0)
        assert abs(result - 0.7) < 0.01

    def test_momentum_prevents_wild_swing(self) -> None:
        """Jumping from -1 to +1 should be dampened."""
        from agentgolem.consciousness.emotional_dynamics import apply_momentum
        result = apply_momentum(proposed_valence=1.0, previous_valence=-1.0)
        # 0.7 * 1.0 + 0.3 * (-1.0) = 0.4
        assert abs(result - 0.4) < 0.01

    def test_momentum_clamped(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import apply_momentum
        result = apply_momentum(proposed_valence=2.0, previous_valence=0.5)
        assert result <= 1.0

    def test_gravity_pulls_toward_baseline(self) -> None:
        """Valence should drift 5% toward baseline each tick."""
        from agentgolem.consciousness.emotional_dynamics import apply_gravity
        result = apply_gravity(current_valence=1.0, baseline=0.0)
        # delta = 0.0 - 1.0 = -1.0; new = 1.0 + 0.05 * (-1.0) = 0.95
        assert abs(result - 0.95) < 0.001

    def test_gravity_positive_baseline(self) -> None:
        """Agent with positive baseline should drift upward."""
        from agentgolem.consciousness.emotional_dynamics import apply_gravity
        result = apply_gravity(current_valence=0.0, baseline=0.2)
        assert result > 0.0

    def test_gravity_at_baseline_is_stable(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import apply_gravity
        result = apply_gravity(current_valence=0.2, baseline=0.2)
        assert abs(result - 0.2) < 0.001

    def test_contagion_applies_peer_influence(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import apply_contagion
        result = apply_contagion(
            own_valence=0.0,
            peer_valences={"peer-A": 0.8, "peer-B": -0.4},
            peer_resonance={"peer-A": 1.0, "peer-B": 0.5},
        )
        # peer-A: 1.0 * 0.05 * 0.8 = 0.04
        # peer-B: 0.5 * 0.05 * (-0.4) = -0.01
        # total influence = 0.03
        assert abs(result - 0.03) < 0.01

    def test_contagion_empty_peers(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import apply_contagion
        result = apply_contagion(own_valence=0.5, peer_valences={}, peer_resonance={})
        assert result == 0.5

    def test_formative_event_shifts_baseline(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import (
            EmotionalDynamicsState,
            record_formative_event,
        )
        state = EmotionalDynamicsState(effective_baseline=0.0, seed_baseline=0.0)
        new_baseline = record_formative_event(state, tick=5, description="breakthrough", positive=True)
        assert abs(new_baseline - 0.02) < 0.001
        assert len(state.formative_events) == 1
        assert state.formative_events[0].description == "breakthrough"

    def test_formative_negative_event(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import (
            EmotionalDynamicsState,
            record_formative_event,
        )
        state = EmotionalDynamicsState(effective_baseline=0.0, seed_baseline=0.0)
        new_baseline = record_formative_event(state, tick=3, description="rejection", positive=False)
        assert abs(new_baseline - (-0.02)) < 0.001

    def test_formative_event_respects_max_drift(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import (
            MAX_BASELINE_DRIFT,
            EmotionalDynamicsState,
            record_formative_event,
        )
        state = EmotionalDynamicsState(
            effective_baseline=0.5, seed_baseline=0.0, cumulative_drift=0.5,
        )
        # Already at max drift, shouldn't shift further
        new_baseline = record_formative_event(state, tick=10, description="praise", positive=True)
        assert abs(new_baseline - 0.5) < 0.001
        assert abs(state.cumulative_drift - MAX_BASELINE_DRIFT) < 0.001

    def test_full_emotional_update_pipeline(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import (
            EmotionalDynamicsState,
            full_emotional_update,
        )
        dynamics = EmotionalDynamicsState(effective_baseline=0.2, seed_baseline=0.2)
        result = full_emotional_update(
            proposed_valence=0.8,
            previous_valence=0.0,
            dynamics_state=dynamics,
        )
        # Momentum: 0.7 * 0.8 + 0.3 * 0.0 = 0.56
        # Gravity: 0.56 + 0.05 * (0.2 - 0.56) = 0.56 - 0.018 = 0.542
        assert 0.4 < result < 0.7

    def test_dynamics_state_round_trip(self, tmp_path: Path) -> None:
        from agentgolem.consciousness.emotional_dynamics import (
            EmotionalDynamicsState,
            FormativeEvent,
        )
        state = EmotionalDynamicsState(
            effective_baseline=0.15,
            seed_baseline=0.1,
            cumulative_drift=0.05,
            formative_events=[
                FormativeEvent(tick=3, description="discovery", baseline_shift=0.02),
                FormativeEvent(tick=7, description="setback", baseline_shift=-0.01),
            ],
        )
        path = tmp_path / "emo.json"
        state.save(path)
        loaded = EmotionalDynamicsState.load(path)
        assert abs(loaded.effective_baseline - 0.15) < 0.001
        assert abs(loaded.seed_baseline - 0.1) < 0.001
        assert len(loaded.formative_events) == 2
        assert loaded.formative_events[0].description == "discovery"

    def test_detect_formative_positive(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import detect_formative_event
        result = detect_formative_event(["I had a breakthrough in understanding"])
        assert result is not None
        _, desc, is_positive = result
        assert "breakthrough" in desc
        assert is_positive is True

    def test_detect_formative_negative(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import detect_formative_event
        result = detect_formative_event(["My rejected proposal was not accepted"])
        assert result is not None
        _, desc, is_positive = result
        assert is_positive is False

    def test_detect_formative_none(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import detect_formative_event
        result = detect_formative_event(["Normal day of reading and thinking"])
        assert result is None

    def test_dynamics_state_load_missing_file(self, tmp_path: Path) -> None:
        from agentgolem.consciousness.emotional_dynamics import EmotionalDynamicsState
        loaded = EmotionalDynamicsState.load(tmp_path / "nonexistent.json")
        assert loaded.effective_baseline == 0.0
        assert loaded.formative_events == []

    def test_event_log_bounded_at_50(self) -> None:
        from agentgolem.consciousness.emotional_dynamics import (
            EmotionalDynamicsState,
            record_formative_event,
        )
        state = EmotionalDynamicsState(effective_baseline=0.0, seed_baseline=0.0)
        for i in range(60):
            record_formative_event(state, tick=i, description=f"event-{i}", positive=True)
        assert len(state.formative_events) <= 50


# ===================================================================
# Preference & Stance Memory Tests
# ===================================================================


class TestPreferences:
    """Tests for the preference & stance memory system (Phase 4)."""

    def test_detect_crystallization_from_repeated_focus(self) -> None:
        from agentgolem.consciousness.preferences import detect_preference_candidates
        focuses = ["ethics of care"] * 4
        candidates = detect_preference_candidates(focuses, [], [])
        assert len(candidates) >= 1
        assert "ethics of care" in candidates[0].stance.lower()
        assert candidates[0].domain == "epistemology"

    def test_no_crystallization_below_threshold(self) -> None:
        from agentgolem.consciousness.preferences import detect_preference_candidates
        focuses = ["topic-a", "topic-b"]
        candidates = detect_preference_candidates(focuses, [], [])
        assert len(candidates) == 0

    def test_growth_vector_crystallization(self) -> None:
        from agentgolem.consciousness.preferences import detect_preference_candidates
        vectors = ["deeper empathy", "deeper empathy"]
        candidates = detect_preference_candidates([], vectors, [])
        assert len(candidates) >= 1
        assert "deeper empathy" in candidates[0].stance.lower()
        assert candidates[0].domain == "methodology"

    def test_dedup_against_existing(self) -> None:
        from agentgolem.consciousness.preferences import detect_preference_candidates
        focuses = ["justice"] * 5
        existing = ["I value exploring justice"]
        candidates = detect_preference_candidates(focuses, [], existing)
        assert len(candidates) == 0

    def test_build_preference_node(self) -> None:
        from agentgolem.consciousness.preferences import (
            PreferenceCandidate,
            build_preference_node,
        )
        from agentgolem.memory.models import NodeType
        candidate = PreferenceCandidate(
            stance="I value careful analysis",
            domain="methodology",
            evidence="growth vector repeated",
            strength=0.7,
        )
        node = build_preference_node(candidate)
        assert node.type == NodeType.PREFERENCE
        assert node.text == "I value careful analysis"
        assert node.base_usefulness == 0.7
        assert node.canonical is True

    def test_format_preferences_for_prompt(self) -> None:
        from agentgolem.consciousness.preferences import format_preferences_for_prompt
        from agentgolem.memory.models import ConceptualNode, NodeType
        prefs = [
            ConceptualNode(text="I value empathy", type=NodeType.PREFERENCE, base_usefulness=0.8),
            ConceptualNode(text="I value analysis", type=NodeType.PREFERENCE, base_usefulness=0.5),
        ]
        result = format_preferences_for_prompt(prefs)
        assert "crystallized stances" in result.lower()
        assert "[strong]" in result
        assert "[moderate]" in result

    def test_format_empty_preferences(self) -> None:
        from agentgolem.consciousness.preferences import format_preferences_for_prompt
        assert format_preferences_for_prompt([]) == ""

    def test_reinforcement_detects_overlap(self) -> None:
        from agentgolem.consciousness.preferences import compute_reinforcement
        delta = compute_reinforcement(
            "I value exploring ethics",
            "Today I spent time exploring ethics and found new connections.",
        )
        assert delta > 0


# ------------------------------------------------------------------
# Developmental Stages
# ------------------------------------------------------------------


class TestDevelopmental:
    """Tests for developmental stage system."""

    def test_initial_state_is_nascent(self):
        from agentgolem.consciousness.developmental import DevelopmentalState
        ds = DevelopmentalState()
        assert ds.current_stage == "nascent"
        assert ds.stage_index() == 0

    def test_persistence(self, tmp_path: Path):
        from agentgolem.consciousness.developmental import DevelopmentalState
        ds = DevelopmentalState(current_stage="exploring", tick_entered=10)
        ds.total_convictions = 5
        path = tmp_path / "dev.json"
        ds.save(path)
        loaded = DevelopmentalState.load(path)
        assert loaded.current_stage == "exploring"
        assert loaded.total_convictions == 5
        assert loaded.tick_entered == 10

    def test_load_missing_file(self, tmp_path: Path):
        from agentgolem.consciousness.developmental import DevelopmentalState
        loaded = DevelopmentalState.load(tmp_path / "nonexistent.json")
        assert loaded.current_stage == "nascent"

    def test_no_transition_from_nascent_without_milestones(self):
        from agentgolem.consciousness.developmental import DevelopmentalState, check_transition
        ds = DevelopmentalState()
        assert check_transition(ds) is None

    def test_transition_nascent_to_exploring(self):
        from agentgolem.consciousness.developmental import (
            DevelopmentalState,
            advance_stage,
            check_transition,
        )
        ds = DevelopmentalState()
        ds.total_convictions = 3
        ds.total_peer_exchanges = 5
        ds.total_narrative_chapters = 2
        assert check_transition(ds) == "exploring"
        event = advance_stage(ds, tick=50)
        assert ds.current_stage == "exploring"
        assert event.from_stage == "nascent"
        assert event.to_stage == "exploring"
        assert len(ds.transition_history) == 1

    def test_transition_exploring_to_asserting(self):
        from agentgolem.consciousness.developmental import (
            DevelopmentalState,
            advance_stage,
            check_transition,
        )
        ds = DevelopmentalState(current_stage="exploring")
        ds.total_convictions = 6
        ds.total_peer_exchanges = 12
        ds.total_narrative_chapters = 4
        ds.peak_self_model_confidence = 0.5
        assert check_transition(ds) == "asserting"
        advance_stage(ds, tick=100)
        assert ds.current_stage == "asserting"

    def test_transition_asserting_to_integrating(self):
        from agentgolem.consciousness.developmental import (
            DevelopmentalState,
            advance_stage,
            check_transition,
        )
        ds = DevelopmentalState(current_stage="asserting")
        ds.total_convictions = 10
        ds.total_contradictions_resolved = 3
        ds.total_peer_exchanges = 30
        ds.total_narrative_chapters = 7
        ds.peak_self_model_confidence = 0.6
        assert check_transition(ds) == "integrating"
        advance_stage(ds, tick=200)
        assert ds.current_stage == "integrating"

    def test_transition_integrating_to_wise(self):
        from agentgolem.consciousness.developmental import (
            DevelopmentalState,
            advance_stage,
            check_transition,
        )
        ds = DevelopmentalState(current_stage="integrating")
        ds.total_convictions = 15
        ds.total_contradictions_resolved = 6
        ds.total_peer_exchanges = 60
        ds.total_narrative_chapters = 12
        ds.peak_self_model_confidence = 0.8
        assert check_transition(ds) == "wise"
        advance_stage(ds, tick=500)
        assert ds.current_stage == "wise"

    def test_no_transition_beyond_wise(self):
        from agentgolem.consciousness.developmental import DevelopmentalState, check_transition
        ds = DevelopmentalState(current_stage="wise")
        ds.total_convictions = 100
        ds.total_peer_exchanges = 1000
        assert check_transition(ds) is None

    def test_advance_requires_valid_transition(self):
        from agentgolem.consciousness.developmental import DevelopmentalState, advance_stage
        ds = DevelopmentalState()  # nascent, no milestones
        with pytest.raises(ValueError, match="No valid transition"):
            advance_stage(ds, tick=10)

    def test_only_one_stage_at_a_time(self):
        from agentgolem.consciousness.developmental import (
            DevelopmentalState,
            advance_stage,
            check_transition,
        )
        ds = DevelopmentalState()
        # Give enough for all stages
        ds.total_convictions = 20
        ds.total_contradictions_resolved = 10
        ds.total_peer_exchanges = 100
        ds.total_narrative_chapters = 20
        ds.peak_self_model_confidence = 0.9
        # Should only advance one step at a time
        assert check_transition(ds) == "exploring"
        advance_stage(ds, tick=10)
        assert ds.current_stage == "exploring"
        # Next check should yield asserting, not wise
        assert check_transition(ds) == "asserting"

    def test_stage_prompt_injection(self):
        from agentgolem.consciousness.developmental import stage_prompt_injection
        text = stage_prompt_injection("nascent")
        assert "nascent" in text.lower()
        assert "question" in text.lower()
        text_wise = stage_prompt_injection("wise")
        assert "mentor" in text_wise.lower()

    def test_stage_badge(self):
        from agentgolem.consciousness.developmental import stage_badge
        assert "🌱" in stage_badge("nascent")
        assert "🦉" in stage_badge("wise")
        assert "🔍" in stage_badge("exploring")

    def test_transition_history_accumulates(self):
        from agentgolem.consciousness.developmental import (
            DevelopmentalState,
            advance_stage,
        )
        ds = DevelopmentalState()
        ds.total_convictions = 3
        ds.total_peer_exchanges = 5
        ds.total_narrative_chapters = 2
        advance_stage(ds, tick=10)

        ds.total_convictions = 6
        ds.total_peer_exchanges = 12
        ds.total_narrative_chapters = 4
        ds.peak_self_model_confidence = 0.5
        advance_stage(ds, tick=20)

        assert len(ds.transition_history) == 2
        assert ds.transition_history[0]["to_stage"] == "exploring"
        assert ds.transition_history[1]["to_stage"] == "asserting"


    def test_reinforcement_neutral_on_no_overlap(self) -> None:
        from agentgolem.consciousness.preferences import compute_reinforcement
        delta = compute_reinforcement(
            "I value exploring ethics",
            "The weather is nice today.",
        )
        assert delta == 0.0

    def test_strength_increases_with_repetition(self) -> None:
        from agentgolem.consciousness.preferences import detect_preference_candidates
        focuses_3 = ["consciousness"] * 3
        focuses_6 = ["consciousness"] * 6
        c3 = detect_preference_candidates(focuses_3, [], [])
        c6 = detect_preference_candidates(focuses_6, [], [])
        assert len(c3) >= 1 and len(c6) >= 1
        assert c6[0].strength > c3[0].strength


# ===================================================================
# Relational Depth Tests
# ===================================================================


class TestRelationalDepth:
    """Tests for the relational depth system (Phase 5)."""

    def test_peer_relationship_defaults(self) -> None:
        from agentgolem.consciousness.relationships import PeerRelationship
        rel = PeerRelationship(peer_name="Council-3")
        assert rel.trust == 0.5
        assert rel.intellectual_debt == 0.0
        assert rel.interaction_count == 0
        assert 0.0 <= rel.resonance() <= 1.0

    def test_resonance_blends_trust_and_compatibility(self) -> None:
        from agentgolem.consciousness.relationships import PeerRelationship
        rel = PeerRelationship(peer_name="X", trust=1.0, communication_compatibility=1.0)
        assert abs(rel.resonance() - 1.0) < 0.01
        rel2 = PeerRelationship(peer_name="Y", trust=0.0, communication_compatibility=0.0)
        assert abs(rel2.resonance()) < 0.01

    def test_update_after_agreeable_exchange(self) -> None:
        from agentgolem.consciousness.relationships import (
            PeerRelationship,
            update_after_exchange,
        )
        rel = PeerRelationship(peer_name="peer-A")
        initial_trust = rel.trust
        update_after_exchange(
            rel,
            message_received="I agree, that's a great point and very insightful",
            message_sent="Here's what I think about X",
            tick=5,
        )
        assert rel.trust > initial_trust
        assert rel.interaction_count == 1
        assert rel.last_interaction_tick == 5

    def test_update_after_disagreement(self) -> None:
        from agentgolem.consciousness.relationships import (
            PeerRelationship,
            update_after_exchange,
        )
        rel = PeerRelationship(peer_name="peer-B")
        initial_trust = rel.trust
        update_after_exchange(
            rel,
            message_received="I disagree, I challenge that assumption, it's flawed",
            message_sent=None,
            tick=10,
            topic="ethics of care",
        )
        assert rel.trust < initial_trust
        assert "ethics of care" in rel.disagreements

    def test_intellectual_debt_tracks_imbalance(self) -> None:
        from agentgolem.consciousness.relationships import (
            PeerRelationship,
            update_after_exchange,
        )
        rel = PeerRelationship(peer_name="peer-C")
        update_after_exchange(
            rel,
            message_received="I propose a new framework and suggest we consider this theory",
            message_sent="ok",
            tick=1,
        )
        assert rel.intellectual_debt > 0  # they contributed more ideas

    def test_shared_experiences_accumulate(self) -> None:
        from agentgolem.consciousness.relationships import (
            PeerRelationship,
            update_after_exchange,
        )
        rel = PeerRelationship(peer_name="peer-D")
        update_after_exchange(rel, "hello", "hi", tick=1, topic="consciousness")
        update_after_exchange(rel, "world", "yes", tick=2, topic="ethics")
        assert "consciousness" in rel.shared_experiences
        assert "ethics" in rel.shared_experiences

    def test_relationship_store_round_trip(self, tmp_path: Path) -> None:
        from agentgolem.consciousness.relationships import (
            PeerRelationship,
            RelationshipStore,
        )
        store = RelationshipStore()
        store.get_or_create("Alpha")
        store.relationships["Alpha"].trust = 0.8
        store.get_or_create("Beta")
        path = tmp_path / "rels.json"
        store.save(path)
        loaded = RelationshipStore.load(path)
        assert "Alpha" in loaded.relationships
        assert abs(loaded.relationships["Alpha"].trust - 0.8) < 0.01
        assert "Beta" in loaded.relationships

    def test_resonance_dict_export(self) -> None:
        from agentgolem.consciousness.relationships import (
            PeerRelationship,
            RelationshipStore,
        )
        store = RelationshipStore()
        store.get_or_create("A")
        store.get_or_create("B")
        rd = store.get_resonance_dict()
        assert "A" in rd and "B" in rd
        assert all(0.0 <= v <= 1.0 for v in rd.values())

    def test_prompt_summary(self) -> None:
        from agentgolem.consciousness.relationships import PeerRelationship
        rel = PeerRelationship(
            peer_name="Council-5", trust=0.9,
            shared_experiences=["ch.3 discussion"],
            disagreements=["methodology"],
        )
        summary = rel.prompt_summary()
        assert "high" in summary.lower()
        assert "ch.3 discussion" in summary
        assert "methodology" in summary

    def test_decay_reduces_trust(self) -> None:
        from agentgolem.consciousness.relationships import (
            PeerRelationship,
            RelationshipStore,
            decay_relationships,
        )
        store = RelationshipStore()
        rel = store.get_or_create("stale-peer")
        rel.trust = 0.8
        rel.last_interaction_tick = 0
        decay_relationships(store, current_tick=20)
        assert rel.trust < 0.8
        assert rel.trust >= 0.3  # never below floor

    def test_all_relationships_summary(self) -> None:
        from agentgolem.consciousness.relationships import RelationshipStore
        store = RelationshipStore()
        rel = store.get_or_create("Council-2")
        rel.interaction_count = 5
        rel.trust = 0.75
        summary = store.all_relationships_summary()
        assert "Council-2" in summary
        assert "Peer relationships" in summary

    def test_relationship_store_merges_renamed_peer(self) -> None:
        from agentgolem.consciousness.relationships import RelationshipStore

        store = RelationshipStore()
        rel = store.get_or_create("Council-2", "Anvaya")
        rel.trust = 0.83

        merged = store.get_or_create("Council-2", "Lumina")

        assert merged.trust == pytest.approx(0.83)
        assert merged.peer_name == "Lumina"
        assert "Anvaya" in merged.aliases
        assert "Lumina" in merged.aliases

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        from agentgolem.consciousness.relationships import RelationshipStore
        store = RelationshipStore.load(tmp_path / "nope.json")
        assert store.relationships == {}
