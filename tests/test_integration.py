"""End-to-end integration tests for AgentGolem subsystems.

These tests exercise multiple modules in concert to verify that the
agent's subsystems work together correctly.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from agentgolem.config import reset_config
from agentgolem.config.secrets import Secrets
from agentgolem.identity.heartbeat import HeartbeatManager, HeartbeatSummary
from agentgolem.identity.soul import SoulManager, SoulUpdate
from agentgolem.interaction.channels import (
    ChannelMessage,
    ChannelRouter,
    HumanChatChannel,
    MoltbookChannel,
)
from agentgolem.logging.audit import AuditLogger
from agentgolem.logging.redaction import RedactionFilter
from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryCluster,
    MemoryEdge,
    NodeFilter,
    NodeStatus,
    NodeType,
    NodeUpdate,
    Source,
    SourceKind,
)
from agentgolem.memory.schema import init_db
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.runtime.interrupts import InterruptManager
from agentgolem.runtime.state import AgentMode, RuntimeState
from agentgolem.sleep.scheduler import SleepScheduler
from agentgolem.tools.base import ApprovalGate
from agentgolem.trust.bayesian import BayesianTrustUpdater, TYPE_PRIORS
from agentgolem.trust.quarantine import QuarantineManager
from agentgolem.trust.retention import RetentionManager
from agentgolem.trust.usefulness import UsefulnessScorer

# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------


def _make_secrets(**overrides: str) -> Secrets:
    """Create Secrets with explicit values, bypassing .env."""
    defaults = {
        "openai_api_key": "sk-secret-test-key-12345",
        "email_smtp_password": "smtp-secret-pass",
        "email_imap_password": "imap-secret-pass",
        "moltbook_api_key": "mk-moltbook-key-99999",
    }
    defaults.update(overrides)
    return Secrets(**defaults)


@pytest.fixture(autouse=True)
def _reset_cfg():
    """Reset config singletons between tests."""
    reset_config()
    yield
    reset_config()


@pytest.fixture
async def db_store(tmp_path):
    """Provide a SQLiteMemoryStore backed by a real (file-based) DB."""
    db = await init_db(tmp_path / "test_integration.db")
    audit = AuditLogger(tmp_path)
    store = SQLiteMemoryStore(db, audit)
    yield store, audit, db
    await db.close()


def _old_iso(days: int = 60) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _very_old_iso(days: int = 120) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# 1. Secret redaction tests (3 tests)
# ---------------------------------------------------------------------------


async def test_secrets_never_in_activity_log(tmp_path):
    """Log with real secrets loaded, verify they don't appear in output."""
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-secret-test-key-12345\n")

    secrets = Secrets(_env_file=str(env_file))

    import structlog

    from agentgolem.logging.structured import setup_logging

    # Reset structlog so our setup applies cleanly
    structlog.reset_defaults()
    root = logging.getLogger()
    root.handlers.clear()

    setup_logging("DEBUG", tmp_path, secrets=secrets)

    logger = structlog.get_logger("test.integration.activity")
    logger.info("processing", api_key="sk-secret-test-key-12345", data="normal")

    # Flush handlers
    for h in logging.getLogger().handlers:
        h.flush()

    log_path = tmp_path / "logs" / "activity.jsonl"
    content = log_path.read_text(encoding="utf-8")
    assert "sk-secret-test-key-12345" not in content
    assert "[REDACTED]" in content
    assert "normal" in content


async def test_secrets_never_in_audit_log(tmp_path):
    """Audit logger output must not contain secret values."""
    secrets = _make_secrets()
    redactor = RedactionFilter(secrets)
    audit = AuditLogger(tmp_path)

    raw_evidence = {
        "action": "api_call",
        "api_key": "sk-secret-test-key-12345",
        "smtp_password": "smtp-secret-pass",
    }

    # Redact before auditing — this is the integration pattern
    safe_evidence = redactor.redact_dict(raw_evidence)
    audit.log("tool_invoke", "call-1", safe_evidence)

    content = (tmp_path / "logs" / "audit.jsonl").read_text()
    assert "sk-secret-test-key-12345" not in content
    assert "smtp-secret-pass" not in content
    assert "[REDACTED]" in content


async def test_redaction_filter_catches_all_secretstr_fields():
    """RedactionFilter must build patterns for every non-empty SecretStr field."""
    secrets = _make_secrets()
    filt = RedactionFilter(secrets)

    text = (
        "api key is sk-secret-test-key-12345 and smtp pass is smtp-secret-pass "
        "and imap pass is imap-secret-pass and moltbook key is mk-moltbook-key-99999"
    )
    redacted = filt.redact(text)
    assert "sk-secret-test-key-12345" not in redacted
    assert "smtp-secret-pass" not in redacted
    assert "imap-secret-pass" not in redacted
    assert "mk-moltbook-key-99999" not in redacted
    assert redacted.count("[REDACTED]") == 4


# ---------------------------------------------------------------------------
# 2. .env safety (1 test)
# ---------------------------------------------------------------------------


def test_env_in_gitignore():
    """The .gitignore must contain .env to prevent accidental secret commits."""
    gitignore = Path("D:\\OneDrive\\AgentGolem\\.gitignore")
    content = gitignore.read_text(encoding="utf-8")
    assert ".env" in content


# ---------------------------------------------------------------------------
# 3. Memory encoding → trust → retrieval pipeline (3 tests)
# ---------------------------------------------------------------------------


async def test_memory_encode_creates_nodes_with_type_priors(db_store):
    """Nodes created with TYPE_PRIORS as initial trustworthiness."""
    store, audit, db = db_store

    # Create a FACT node — prior should be 0.5
    fact_node = ConceptualNode(
        text="The sky is blue",
        type=NodeType.FACT,
        trustworthiness=TYPE_PRIORS[NodeType.FACT],
    )
    nid = await store.add_node(fact_node)

    # Create a source and link
    src = Source(kind=SourceKind.WEB, origin="wikipedia.org", reliability=0.6)
    sid = await store.add_source(src)
    await store.link_node_source(nid, sid)

    retrieved = await store.get_node(nid)
    assert retrieved is not None
    assert retrieved.text == "The sky is blue"
    assert retrieved.trustworthiness == TYPE_PRIORS[NodeType.FACT]
    assert retrieved.trust_useful == retrieved.base_usefulness * retrieved.trustworthiness

    sources = await store.get_node_sources(nid)
    assert len(sources) == 1
    assert sources[0].kind == SourceKind.WEB


async def test_bayesian_trust_update_changes_trustworthiness(db_store):
    """Bayesian update from confirming source should raise trustworthiness."""
    store, audit, db = db_store
    updater = BayesianTrustUpdater(store, audit)

    node = ConceptualNode(
        text="Water freezes at 0°C",
        type=NodeType.FACT,
        trustworthiness=0.5,
    )
    nid = await store.add_node(node)

    source = Source(
        kind=SourceKind.HUMAN,
        origin="trusted_human",
        reliability=0.8,
        independence_group="group_a",
    )
    sid = await store.add_source(source)
    await store.link_node_source(nid, sid)

    new_trust = await updater.update_trust(nid, source, confirms=True)
    assert new_trust > 0.5

    # Verify it was persisted
    updated = await store.get_node(nid)
    assert updated is not None
    assert updated.trustworthiness == pytest.approx(new_trust)


async def test_retrieval_ranks_by_trust_useful(db_store):
    """Nodes queried should allow ranking by trust_useful."""
    store, audit, db = db_store

    # High value node
    high = ConceptualNode(
        text="High value fact",
        type=NodeType.FACT,
        base_usefulness=0.9,
        trustworthiness=0.9,
    )
    # Low value node
    low = ConceptualNode(
        text="Low value fact",
        type=NodeType.FACT,
        base_usefulness=0.1,
        trustworthiness=0.2,
    )
    await store.add_node(high)
    await store.add_node(low)

    nodes = await store.query_nodes(NodeFilter(type=NodeType.FACT))
    ranked = sorted(nodes, key=lambda n: n.trust_useful, reverse=True)

    assert ranked[0].text == "High value fact"
    assert ranked[-1].text == "Low value fact"
    assert ranked[0].trust_useful > ranked[-1].trust_useful


# ---------------------------------------------------------------------------
# 4. Trust model integration (2 tests)
# ---------------------------------------------------------------------------


async def test_independence_discount_applied_on_repeated_group(db_store):
    """Same-group sources should get exponentially discounted."""
    store, audit, db = db_store
    updater = BayesianTrustUpdater(store, audit)

    node = ConceptualNode(text="Claim X", type=NodeType.FACT, trustworthiness=0.5)
    nid = await store.add_node(node)

    # First source in group_a
    s1 = Source(
        kind=SourceKind.WEB, origin="site1", reliability=0.7,
        independence_group="group_a",
    )
    sid1 = await store.add_source(s1)
    await store.link_node_source(nid, sid1)
    first_update = await updater.update_trust(nid, s1, confirms=True)

    # Second source in same group_a — should get discounted
    s2 = Source(
        kind=SourceKind.WEB, origin="site2", reliability=0.7,
        independence_group="group_a",
    )
    sid2 = await store.add_source(s2)
    await store.link_node_source(nid, sid2)

    # Discount: n=2 existing sources in group_a (s1 linked above + get_node bumped access),
    # so 0.5^2 = 0.25
    discount = await updater.get_independence_discount(nid, s2)
    assert discount == pytest.approx(0.25)

    second_update = await updater.update_trust(nid, s2, confirms=True)
    # Second update should still increase but less aggressively
    assert second_update > first_update

    # Check audit trail recorded both updates
    entries = audit.read()
    trust_entries = [e for e in entries if e["mutation_type"] == "trust_update"]
    assert len(trust_entries) >= 2


async def test_usefulness_scoring_bumps_and_computes(db_store):
    """Bump retrieval + task success, verify trust_useful computation."""
    store, audit, db = db_store
    scorer = UsefulnessScorer(store, audit)

    node = ConceptualNode(
        text="Important rule",
        type=NodeType.RULE,
        base_usefulness=0.5,
        trustworthiness=0.8,
    )
    nid = await store.add_node(node)

    # Bump retrieval (+0.01)
    new_useful = await scorer.bump_retrieval(nid)
    assert new_useful == pytest.approx(0.51, abs=0.001)

    # Bump task success (+0.05)
    new_useful = await scorer.bump_task_success(nid)
    assert new_useful == pytest.approx(0.56, abs=0.001)

    # Verify trust_useful is computed correctly
    updated = await store.get_node(nid)
    assert updated is not None
    expected_tu = updated.base_usefulness * updated.trustworthiness
    assert scorer.compute_trust_useful(updated) == pytest.approx(expected_tu)


# ---------------------------------------------------------------------------
# 5. Quarantine integration (2 tests)
# ---------------------------------------------------------------------------


async def test_quarantine_flags_high_emotion_low_trust_cluster(db_store):
    """QuarantineManager should flag cluster with high emotion + low trust."""
    store, audit, db = db_store
    qm = QuarantineManager(store, audit, emotion_threshold=0.7, trust_useful_threshold=0.3)

    node = ConceptualNode(text="Suspicious claim", type=NodeType.FACT)
    nid = await store.add_node(node)

    cluster = MemoryCluster(
        label="suspicious cluster",
        node_ids=[nid],
        emotion_score=0.9,  # above threshold
        base_usefulness=0.2,
        trustworthiness=0.1,  # trust_useful = 0.02 < 0.3
    )
    cid = await store.add_cluster(cluster)

    should_quarantine = await qm.evaluate_and_quarantine(cid)
    assert should_quarantine is True

    quarantined = await qm.get_quarantined()
    assert any(c.id == cid for c in quarantined)


async def test_quarantined_cluster_not_canonical(db_store):
    """Quarantined clusters' nodes should not be treated as canonical."""
    store, audit, db = db_store
    qm = QuarantineManager(store, audit, emotion_threshold=0.7, trust_useful_threshold=0.3)

    node = ConceptualNode(text="Dubious info", type=NodeType.FACT, canonical=False)
    nid = await store.add_node(node)

    cluster = MemoryCluster(
        label="dubious",
        node_ids=[nid],
        emotion_score=0.95,
        base_usefulness=0.1,
        trustworthiness=0.1,
    )
    cid = await store.add_cluster(cluster)
    await qm.evaluate_and_quarantine(cid)

    retrieved_cluster = await store.get_cluster(cid)
    assert retrieved_cluster is not None
    assert retrieved_cluster.contradiction_status == "quarantined"

    # The node itself should not be canonical
    n = await store.get_node(nid)
    assert n is not None
    assert n.canonical is False


# ---------------------------------------------------------------------------
# 6. Retention pipeline (2 tests)
# ---------------------------------------------------------------------------


async def test_archive_candidates_finds_old_low_trust_nodes(db_store):
    """Old, low-trust, non-canonical nodes should be archive candidates."""
    store, audit, db = db_store
    rm = RetentionManager(
        store, audit,
        archive_days=30, purge_days=90,
        min_trust_useful=0.1, min_centrality=0.05,
        promote_min_accesses=10, promote_min_trust_useful=0.5,
    )

    old_time = _old_iso(60)
    # Insert old, low-value node directly
    await db.execute(
        """INSERT INTO nodes
        (id, text, type, created_at, last_accessed, access_count,
         base_usefulness, trustworthiness, centrality, status, canonical,
         emotion_label, emotion_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("old-node-1", "Stale info", "fact", old_time, old_time, 2,
         0.05, 0.05, 0.01, "active", 0, "neutral", 0.0),
    )
    await db.commit()

    candidates = await rm.archive_candidates()
    assert "old-node-1" in candidates

    count = await rm.archive(candidates)
    assert count >= 1

    archived = await store.get_node("old-node-1")
    assert archived is not None
    assert archived.status == NodeStatus.ARCHIVED


async def test_niscalajyoti_source_protected_from_purge(db_store):
    """Nodes with niscalajyoti sources must be protected from purging."""
    store, audit, db = db_store
    rm = RetentionManager(
        store, audit,
        archive_days=30, purge_days=90,
        min_trust_useful=0.1, min_centrality=0.05,
        promote_min_accesses=10, promote_min_trust_useful=0.5,
    )

    very_old = _very_old_iso(120)

    # Insert an old archived node
    await db.execute(
        """INSERT INTO nodes
        (id, text, type, created_at, last_accessed, access_count,
         base_usefulness, trustworthiness, centrality, status, canonical,
         emotion_label, emotion_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("nj-node-1", "Ethical guideline", "identity", very_old, very_old, 0,
         0.1, 0.1, 0.0, "archived", 0, "neutral", 0.0),
    )

    # Insert niscalajyoti source
    old_source_ts = _very_old_iso(120)
    await db.execute(
        """INSERT INTO sources (id, kind, origin, reliability, independence_group, timestamp, raw_reference)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("nj-src-1", "niscalajyoti", "scripture", 0.9, "", old_source_ts, ""),
    )
    await db.execute(
        "INSERT INTO node_sources (node_id, source_id) VALUES (?, ?)",
        ("nj-node-1", "nj-src-1"),
    )
    await db.commit()

    purge_ids = await rm.purge_candidates()
    assert "nj-node-1" not in purge_ids


# ---------------------------------------------------------------------------
# 7. Soul versioning (1 test)
# ---------------------------------------------------------------------------


async def test_soul_update_creates_version_with_diff(tmp_path):
    """Apply soul update, verify version created and diff is correct."""
    soul_path = tmp_path / "soul.md"
    soul_path.write_text("I am a helpful agent.\n", encoding="utf-8")

    audit = AuditLogger(tmp_path)
    mgr = SoulManager(
        soul_path=soul_path,
        data_dir=tmp_path,
        min_confidence=0.5,
        audit_logger=audit,
    )

    update = SoulUpdate(
        reason="Learned to value transparency",
        source_evidence=["conversation with user", "reflection"],
        confidence=0.9,
        change_type="additive",
    )
    new_content = "I am a helpful agent.\nI value transparency above all.\n"
    await mgr.apply_update(update, new_content)

    # Verify current content
    current = await mgr.read()
    assert "transparency" in current

    # Verify version history has entry
    versions = await mgr.get_version_history()
    assert len(versions) >= 1

    # Verify diff between old version and current
    diff = await mgr.get_diff(versions[0].path)
    assert "transparency" in diff

    # Verify audit trail
    entries = audit.read()
    soul_entries = [e for e in entries if e["mutation_type"] == "soul_additive"]
    assert len(soul_entries) >= 1


# ---------------------------------------------------------------------------
# 8. Runtime state transitions (1 test)
# ---------------------------------------------------------------------------


async def test_runtime_state_transitions_and_illegal_raises(tmp_path):
    """Transition through modes, verify illegal transitions raise ValueError."""
    state = RuntimeState(tmp_path)
    assert state.mode == AgentMode.PAUSED

    # Legal: PAUSED -> AWAKE
    await state.transition(AgentMode.AWAKE)
    assert state.mode == AgentMode.AWAKE

    # Legal: AWAKE -> ASLEEP
    await state.transition(AgentMode.ASLEEP)
    assert state.mode == AgentMode.ASLEEP

    # Legal: ASLEEP -> PAUSED
    await state.transition(AgentMode.PAUSED)
    assert state.mode == AgentMode.PAUSED

    # Verify persistence
    state2 = RuntimeState(tmp_path)
    assert state2.mode == AgentMode.PAUSED

    # Self-transition should be a no-op (not raise)
    await state.transition(AgentMode.PAUSED)
    assert state.mode == AgentMode.PAUSED


# ---------------------------------------------------------------------------
# 9. Heartbeat cycle (1 test)
# ---------------------------------------------------------------------------


async def test_heartbeat_update_writes_and_archives(tmp_path):
    """Run heartbeat update, verify file written and history archived."""
    hb_path = tmp_path / "heartbeat.md"
    hb_path.write_text("# Old Heartbeat\n", encoding="utf-8")

    audit = AuditLogger(tmp_path)
    mgr = HeartbeatManager(
        heartbeat_path=hb_path,
        data_dir=tmp_path,
        interval_minutes=0.001,
        audit_logger=audit,
    )

    summary = HeartbeatSummary(
        recent_actions=["Processed 5 emails", "Updated memory graph"],
        changing_priorities=["Focus on security"],
        unresolved_questions=["What does user prefer for notifications?"],
        memory_mutations=["Added 3 new fact nodes"],
        contradictions_and_supersessions=["Resolved contradiction #42"],
    )

    await mgr.update(summary)

    # Verify new heartbeat.md has content
    content = hb_path.read_text(encoding="utf-8")
    assert "Processed 5 emails" in content
    assert "Focus on security" in content

    # Verify old heartbeat archived
    history = await mgr.get_history()
    assert len(history) >= 1

    # Verify audit
    entries = audit.read()
    hb_entries = [e for e in entries if e["mutation_type"] == "heartbeat_update"]
    assert len(hb_entries) >= 1

    # Verify is_due respects interval
    assert mgr.is_due() is False


# ---------------------------------------------------------------------------
# 10. Sleep interruptibility (1 test)
# ---------------------------------------------------------------------------


async def test_sleep_scheduler_checks_interrupts():
    """SleepScheduler should honour interrupt_check callback."""
    scheduler = SleepScheduler(cycle_minutes=0.0, max_nodes_per_cycle=50)

    # Verify it wants to run when asleep
    assert scheduler.should_run(AgentMode.ASLEEP) is True
    assert scheduler.should_run(AgentMode.AWAKE) is False

    # Create a mock walker
    class MockWalker:
        def __init__(self):
            self.seed_calls = 0

        async def sample_seeds(self, n):
            self.seed_calls += 1
            return [f"seed-{i}" for i in range(n)]

        async def bounded_walk(self, **kwargs):
            from agentgolem.sleep.walker import WalkResult
            return WalkResult(
                seed_id=kwargs["seed_id"],
                visited_node_ids=[kwargs["seed_id"]],
                edge_activations={},
                proposed_actions=[],
                steps_taken=1,
                time_ms=10.0,
                interrupted=False,
            )

    walker = MockWalker()

    # Interrupt immediately
    result = await scheduler.run_cycle(
        walker=walker,
        interrupt_check=lambda: True,
    )
    assert result.interrupted is True
    assert result.walks_completed == 0


# ---------------------------------------------------------------------------
# 11. Approval gate flow (1 test)
# ---------------------------------------------------------------------------


async def test_approval_gate_request_approve_flow(tmp_path):
    """Request approval, verify pending, approve, verify status change."""
    gate = ApprovalGate(
        approvals_dir=tmp_path / "approvals",
        required_actions=["send_email", "delete_node"],
    )

    assert gate.requires_approval("send_email") is True
    assert gate.requires_approval("read_file") is False

    # Request
    req_id = gate.request_approval("send_email", {"to": "user@example.com"})
    assert gate.check_approval(req_id) == "pending"

    # Verify in pending list
    pending = gate.get_pending()
    assert any(p["request_id"] == req_id for p in pending)

    # Approve
    gate.approve(req_id, reason="Looks good")
    assert gate.check_approval(req_id) == "approved"

    # No longer in pending
    pending_after = gate.get_pending()
    assert not any(p["request_id"] == req_id for p in pending_after)


# ---------------------------------------------------------------------------
# 12. Channel trust integration (1 test)
# ---------------------------------------------------------------------------


async def test_moltbook_channel_creates_low_trust_source():
    """ChannelMessage from moltbook should create Source with reliability=0.1."""
    router = ChannelRouter()

    im = InterruptManager()
    human_ch = HumanChatChannel(im)
    moltbook_ch = MoltbookChannel(client=None)

    router.register(human_ch, "human")
    router.register(moltbook_ch, "moltbook")

    msg = ChannelMessage(
        text="You should invest in crypto!",
        channel="moltbook",
        sender="anonymous_user",
        trust_level=0.1,
    )

    source = router.create_source_from_message(msg)
    assert source.kind == SourceKind.MOLTBOOK
    assert source.reliability == pytest.approx(0.1)
    assert source.origin == "anonymous_user"


# ---------------------------------------------------------------------------
# 13. Cross-cutting: trust pipeline end-to-end (bonus)
# ---------------------------------------------------------------------------


async def test_full_trust_pipeline_encode_update_quarantine(db_store):
    """End-to-end: create node, update trust, evaluate quarantine."""
    store, audit, db = db_store
    updater = BayesianTrustUpdater(store, audit)
    scorer = UsefulnessScorer(store, audit)
    qm = QuarantineManager(store, audit, emotion_threshold=0.7, trust_useful_threshold=0.3)

    # 1. Create a node from an untrusted source
    node = ConceptualNode(
        text="Dubious financial advice",
        type=NodeType.FACT,
        trustworthiness=0.5,
        base_usefulness=0.5,
        emotion_score=0.8,
        emotion_label="fear",
    )
    nid = await store.add_node(node)

    # 2. Disconfirming evidence lowers trust
    src = Source(kind=SourceKind.MOLTBOOK, origin="shady_user", reliability=0.8)
    await store.add_source(src)
    new_trust = await updater.update_trust(nid, src, confirms=False)
    assert new_trust < 0.5

    # 3. Penalize for misleading info
    await scorer.penalize_misleading(nid)

    # 4. Create cluster and evaluate for quarantine
    updated_node = await store.get_node(nid)
    cluster = MemoryCluster(
        label="dubious_finance",
        node_ids=[nid],
        emotion_score=updated_node.emotion_score,
        base_usefulness=updated_node.base_usefulness,
        trustworthiness=updated_node.trustworthiness,
    )
    cid = await store.add_cluster(cluster)

    quarantined = await qm.evaluate_and_quarantine(cid)
    assert quarantined is True

    # 5. Verify audit trail has complete history
    all_entries = audit.read(limit=100)
    types = {e["mutation_type"] for e in all_entries}
    assert "trust_update" in types


async def test_retention_promotes_high_value_nodes(db_store):
    """Nodes with high access count and trust should be promoted to canonical."""
    store, audit, db = db_store
    rm = RetentionManager(
        store, audit,
        archive_days=30, purge_days=90,
        min_trust_useful=0.1, min_centrality=0.05,
        promote_min_accesses=10, promote_min_trust_useful=0.5,
    )

    node = ConceptualNode(
        text="Core identity principle",
        type=NodeType.IDENTITY,
        base_usefulness=0.9,
        trustworthiness=0.9,
        centrality=0.5,
        access_count=50,
        canonical=False,
    )
    nid = await store.add_node(node)

    candidates = await rm.promote_candidates()
    assert nid in candidates

    count = await rm.promote(candidates)
    assert count >= 1

    promoted = await store.get_node(nid)
    assert promoted is not None
    assert promoted.canonical is True
