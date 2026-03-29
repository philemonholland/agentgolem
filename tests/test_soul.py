"""Tests for SoulManager — identity evolution with guardrails."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agentgolem.identity.soul import SoulManager, SoulUpdate, SoulVersion
from agentgolem.logging.audit import AuditLogger

SAMPLE_SOUL = """\
# AgentGolem

I am AgentGolem — a persistent autonomous agent.

## Core Purpose

I exist to explore.
"""


# ── helpers ──────────────────────────────────────────────────────────────


def _make_manager(
    tmp_path: Path,
    *,
    soul_content: str | None = SAMPLE_SOUL,
    audit: bool = False,
) -> tuple[SoulManager, Path]:
    """Build a SoulManager rooted in *tmp_path*."""
    soul_path = tmp_path / "soul.md"
    if soul_content is not None:
        soul_path.write_text(soul_content, encoding="utf-8")
    data_dir = tmp_path / "data"
    audit_logger = AuditLogger(data_dir) if audit else None
    mgr = SoulManager(
        soul_path=soul_path,
        data_dir=data_dir,
        min_confidence=0.7,
        audit_logger=audit_logger,
    )
    return mgr, soul_path


def _valid_update(**overrides) -> SoulUpdate:
    defaults = dict(
        reason="Reflection on purpose after deep reading",
        source_evidence=["journal entry 2025-06-01", "niscalajyoti passage"],
        confidence=0.85,
        change_type="additive",
    )
    defaults.update(overrides)
    return SoulUpdate(**defaults)


# ── read tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_soul(tmp_path: Path) -> None:
    mgr, _ = _make_manager(tmp_path)
    content = await mgr.read()
    assert content == SAMPLE_SOUL


@pytest.mark.asyncio
async def test_read_missing_soul(tmp_path: Path) -> None:
    mgr, _ = _make_manager(tmp_path, soul_content=None)
    assert await mgr.read() == ""


# ── propose_update tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_propose_valid_update(tmp_path: Path) -> None:
    mgr, _ = _make_manager(tmp_path)
    result = await mgr.propose_update(_valid_update())
    assert "additive" in result
    assert "Reflection" in result


@pytest.mark.asyncio
async def test_propose_low_confidence_rejected(tmp_path: Path) -> None:
    mgr, _ = _make_manager(tmp_path)
    with pytest.raises(ValueError, match="Confidence.*below threshold"):
        await mgr.propose_update(_valid_update(confidence=0.3))


@pytest.mark.asyncio
async def test_propose_non_soul_worthy_rejected(tmp_path: Path) -> None:
    mgr, _ = _make_manager(tmp_path)
    with pytest.raises(ValueError, match="Non-soul-worthy"):
        await mgr.propose_update(
            _valid_update(reason="Based on a transient mood swing")
        )


@pytest.mark.asyncio
async def test_propose_no_evidence_rejected(tmp_path: Path) -> None:
    mgr, _ = _make_manager(tmp_path)
    with pytest.raises(ValueError, match="source evidence"):
        await mgr.propose_update(_valid_update(source_evidence=[]))


# ── apply_update tests ──────────────────────────────────────────────────


NEW_SOUL = """\
# AgentGolem

I am AgentGolem — a persistent autonomous agent.

## Core Purpose

I exist to explore and to understand compassion.
"""


@pytest.mark.asyncio
async def test_apply_update_writes_file(tmp_path: Path) -> None:
    mgr, soul_path = _make_manager(tmp_path)
    await mgr.apply_update(_valid_update(), NEW_SOUL)
    assert soul_path.read_text(encoding="utf-8") == NEW_SOUL


@pytest.mark.asyncio
async def test_apply_update_archives_old(tmp_path: Path) -> None:
    mgr, _ = _make_manager(tmp_path)
    await mgr.apply_update(_valid_update(), NEW_SOUL)

    versions_dir = tmp_path / "data" / "soul_versions"
    archived = list(versions_dir.glob("*.md"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == SAMPLE_SOUL


@pytest.mark.asyncio
async def test_apply_update_audit_logged(tmp_path: Path) -> None:
    mgr, _ = _make_manager(tmp_path, audit=True)
    await mgr.apply_update(_valid_update(change_type="revisive"), NEW_SOUL)

    audit_path = tmp_path / "data" / "logs" / "audit.jsonl"
    assert audit_path.exists()
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["mutation_type"] == "soul_revisive"
    assert entry["target_id"] == "soul.md"
    assert entry["evidence"]["reason"] == "Reflection on purpose after deep reading"
    assert "diff" in entry


# ── version history & diff ──────────────────────────────────────────────

EVEN_NEWER_SOUL = """\
# AgentGolem

I am AgentGolem — a persistent autonomous agent.

## Core Purpose

I exist to explore, understand compassion, and cultivate wisdom.
"""


@pytest.mark.asyncio
async def test_version_history(tmp_path: Path) -> None:
    mgr, _ = _make_manager(tmp_path)

    # Apply two updates with a small delay so timestamps differ
    await mgr.apply_update(_valid_update(reason="First evolution"), NEW_SOUL)
    # Ensure distinct timestamp (filesystem resolution can be 1s)
    await asyncio.sleep(1.1)
    await mgr.apply_update(_valid_update(reason="Second evolution"), EVEN_NEWER_SOUL)

    history = await mgr.get_version_history()
    assert len(history) == 2
    # Most recent archive first
    assert history[0].timestamp >= history[1].timestamp


@pytest.mark.asyncio
async def test_get_diff(tmp_path: Path) -> None:
    mgr, _ = _make_manager(tmp_path)
    await mgr.apply_update(_valid_update(), NEW_SOUL)

    history = await mgr.get_version_history()
    assert len(history) == 1

    diff = await mgr.get_diff(history[0].path)
    assert "---" in diff
    assert "+++" in diff
    assert "explore" in diff
