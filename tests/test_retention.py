"""Tests for the archive / purge / promote retention pipeline."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryEdge,
    NodeStatus,
    NodeType,
    NodeUpdate,
    Source,
    SourceKind,
)
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.trust.retention import RetentionManager

OLD = datetime.now(UTC) - timedelta(days=60)
VERY_OLD = datetime.now(UTC) - timedelta(days=120)


@pytest.fixture
async def setup(tmp_path):
    db = await init_db(tmp_path / "test.db")
    store = SQLiteMemoryStore(db)
    audit = AuditLogger(tmp_path)
    rm = RetentionManager(store, audit)
    yield store, rm, audit
    await close_db(db)


def _weak_old_node(text: str = "old weak memory", **kw) -> ConceptualNode:
    """A node that should be caught by archive_candidates."""
    defaults = dict(
        type=NodeType.FACT,
        base_usefulness=0.05,
        trustworthiness=0.05,
        centrality=0.01,
        access_count=1,
        created_at=OLD,
        last_accessed=OLD,
    )
    defaults.update(kw)
    return ConceptualNode(text=text, **defaults)


def _strong_node(text: str = "strong memory", **kw) -> ConceptualNode:
    """A node that should never be archived or purged."""
    defaults = dict(
        type=NodeType.FACT,
        base_usefulness=0.9,
        trustworthiness=0.9,
        centrality=0.5,
        access_count=20,
    )
    defaults.update(kw)
    return ConceptualNode(text=text, **defaults)


# ------------------------------------------------------------------
# archive_candidates
# ------------------------------------------------------------------


async def test_archive_candidates_finds_weak_nodes(setup):
    store, rm, _ = setup
    weak = _weak_old_node()
    await store.add_node(weak)

    candidates = await rm.archive_candidates()
    assert weak.id in candidates


async def test_archive_candidates_skips_canonical(setup):
    store, rm, _ = setup
    node = _weak_old_node(canonical=True)
    await store.add_node(node)

    candidates = await rm.archive_candidates()
    assert node.id not in candidates


async def test_archive_candidates_skips_strong(setup):
    store, rm, _ = setup
    strong = _strong_node()
    await store.add_node(strong)

    candidates = await rm.archive_candidates()
    assert strong.id not in candidates


async def test_archive_candidates_support_fractional_hours(setup):
    store, _, audit = setup
    rm = RetentionManager(store, audit, archive_hours=0.5)
    stale = _weak_old_node(
        text="stale weak memory",
        last_accessed=datetime.now(UTC) - timedelta(minutes=45),
    )
    recent = _weak_old_node(
        text="recent weak memory",
        last_accessed=datetime.now(UTC) - timedelta(minutes=20),
    )
    await store.add_node(stale)
    await store.add_node(recent)

    candidates = await rm.archive_candidates()

    assert stale.id in candidates
    assert recent.id not in candidates


# ------------------------------------------------------------------
# archive
# ------------------------------------------------------------------


async def test_archive_sets_status(setup):
    store, rm, _ = setup
    node = _weak_old_node()
    await store.add_node(node)

    count = await rm.archive([node.id])

    assert count == 1
    # query_nodes doesn't bump access_count
    from agentgolem.memory.models import NodeFilter

    rows = await store.query_nodes(NodeFilter(status=NodeStatus.ARCHIVED, limit=100))
    assert any(n.id == node.id for n in rows)


# ------------------------------------------------------------------
# purge_candidates
# ------------------------------------------------------------------


async def test_purge_candidates_from_archived(setup):
    store, rm, _ = setup
    # Active node should NOT appear even if old
    active = _weak_old_node(text="active old")
    await store.add_node(active)

    # Archived + old enough
    archived = _weak_old_node(text="archived old", last_accessed=VERY_OLD)
    await store.add_node(archived)
    await store.update_node(archived.id, NodeUpdate(status=NodeStatus.ARCHIVED))

    candidates = await rm.purge_candidates()
    assert archived.id in candidates
    assert active.id not in candidates


async def test_purge_protects_canonical(setup):
    store, rm, _ = setup
    node = _weak_old_node(canonical=True, last_accessed=VERY_OLD)
    await store.add_node(node)
    await store.update_node(node.id, NodeUpdate(status=NodeStatus.ARCHIVED))

    candidates = await rm.purge_candidates()
    assert node.id not in candidates


async def test_purge_protects_niscalajyoti(setup):
    store, rm, _ = setup
    node = _weak_old_node(last_accessed=VERY_OLD)
    await store.add_node(node)
    await store.update_node(node.id, NodeUpdate(status=NodeStatus.ARCHIVED))

    # Attach a niscalajyoti source
    src = Source(kind=SourceKind.NISCALAJYOTI, origin="sacred-text")
    await store.add_source(src)
    await store.link_node_source(node.id, src.id)

    candidates = await rm.purge_candidates()
    assert node.id not in candidates


async def test_purge_protects_contradiction_nodes(setup):
    store, rm, _ = setup
    a = _weak_old_node(text="claim A", last_accessed=VERY_OLD)
    b = _weak_old_node(text="claim B", last_accessed=VERY_OLD)
    await store.add_node(a)
    await store.add_node(b)
    await store.update_node(a.id, NodeUpdate(status=NodeStatus.ARCHIVED))
    await store.update_node(b.id, NodeUpdate(status=NodeStatus.ARCHIVED))

    edge = MemoryEdge(source_id=a.id, target_id=b.id, edge_type=EdgeType.CONTRADICTS)
    await store.add_edge(edge)

    candidates = await rm.purge_candidates()
    assert a.id not in candidates
    assert b.id not in candidates


# ------------------------------------------------------------------
# purge
# ------------------------------------------------------------------


async def test_purge_sets_status(setup):
    store, rm, _ = setup
    node = _weak_old_node(last_accessed=VERY_OLD)
    await store.add_node(node)
    await store.update_node(node.id, NodeUpdate(status=NodeStatus.ARCHIVED))

    count = await rm.purge([node.id])

    assert count == 1
    from agentgolem.memory.models import NodeFilter

    rows = await store.query_nodes(NodeFilter(status=NodeStatus.PURGED, limit=100))
    assert any(n.id == node.id for n in rows)


# ------------------------------------------------------------------
# promote_candidates
# ------------------------------------------------------------------


async def test_promote_candidates_meets_thresholds(setup):
    store, rm, _ = setup
    good = _strong_node()
    await store.add_node(good)

    candidates = await rm.promote_candidates()
    assert good.id in candidates


async def test_promote_candidates_skips_weak(setup):
    store, rm, _ = setup
    weak = _weak_old_node()
    await store.add_node(weak)

    candidates = await rm.promote_candidates()
    assert weak.id not in candidates


# ------------------------------------------------------------------
# promote
# ------------------------------------------------------------------


async def test_promote_sets_canonical(setup):
    store, rm, _ = setup
    node = _strong_node()
    await store.add_node(node)

    count = await rm.promote([node.id])

    assert count == 1
    from agentgolem.memory.models import NodeFilter

    rows = await store.query_nodes(NodeFilter(canonical=True, limit=100))
    assert any(n.id == node.id for n in rows)


# ------------------------------------------------------------------
# Audit logging
# ------------------------------------------------------------------


async def test_audit_logged_on_archive(setup):
    store, rm, audit = setup
    node = _weak_old_node()
    await store.add_node(node)

    await rm.archive([node.id])

    entries = audit.read(limit=50)
    archive_entries = [e for e in entries if e["mutation_type"] == "retention_archive"]
    assert len(archive_entries) == 1
    assert archive_entries[0]["target_id"] == node.id


async def test_audit_logged_on_purge(setup):
    store, rm, audit = setup
    node = _weak_old_node(last_accessed=VERY_OLD)
    await store.add_node(node)
    await store.update_node(node.id, NodeUpdate(status=NodeStatus.ARCHIVED))

    await rm.purge([node.id])

    entries = audit.read(limit=50)
    purge_entries = [e for e in entries if e["mutation_type"] == "retention_purge"]
    assert len(purge_entries) == 1
    assert purge_entries[0]["target_id"] == node.id


async def test_audit_logged_on_promote(setup):
    store, rm, audit = setup
    node = _strong_node()
    await store.add_node(node)

    await rm.promote([node.id])

    entries = audit.read(limit=50)
    promo_entries = [e for e in entries if e["mutation_type"] == "retention_promote"]
    assert len(promo_entries) == 1
    assert promo_entries[0]["target_id"] == node.id
