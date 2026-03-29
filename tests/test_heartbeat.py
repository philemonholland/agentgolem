"""Tests for the heartbeat manager."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentgolem.identity.heartbeat import (
    HeartbeatEntry,
    HeartbeatManager,
    HeartbeatSummary,
)


def _make_manager(
    tmp_path: Path,
    *,
    heartbeat_content: str | None = None,
    interval_minutes: float = 15.0,
    audit_logger: object | None = None,
) -> HeartbeatManager:
    """Helper to build a HeartbeatManager rooted in tmp_path."""
    hb_path = tmp_path / "heartbeat.md"
    if heartbeat_content is not None:
        hb_path.write_text(heartbeat_content, encoding="utf-8")
    return HeartbeatManager(
        heartbeat_path=hb_path,
        data_dir=tmp_path,
        interval_minutes=interval_minutes,
        audit_logger=audit_logger,
    )


# ── 1. read ──────────────────────────────────────────────────────────────

async def test_read_heartbeat(tmp_path: Path) -> None:
    """read() returns existing heartbeat.md content."""
    mgr = _make_manager(tmp_path, heartbeat_content="# Hello")
    assert await mgr.read() == "# Hello"


async def test_read_missing_heartbeat(tmp_path: Path) -> None:
    """read() returns empty string when file is absent."""
    mgr = _make_manager(tmp_path)
    assert await mgr.read() == ""


# ── 2. update ────────────────────────────────────────────────────────────

async def test_update_writes_file(tmp_path: Path) -> None:
    """After update(), heartbeat.md exists with rendered content."""
    mgr = _make_manager(tmp_path)
    summary = HeartbeatSummary(recent_actions=["Loaded config"])
    await mgr.update(summary)

    content = (tmp_path / "heartbeat.md").read_text(encoding="utf-8")
    assert "# Heartbeat" in content
    assert "Loaded config" in content


async def test_update_archives_old(tmp_path: Path) -> None:
    """Previous heartbeat content is archived into heartbeat_history/."""
    mgr = _make_manager(tmp_path, heartbeat_content="# Old heartbeat")
    summary = HeartbeatSummary(recent_actions=["Fresh start"])
    await mgr.update(summary)

    history_dir = tmp_path / "heartbeat_history"
    archives = list(history_dir.glob("*.md"))
    assert len(archives) == 1
    assert archives[0].read_text(encoding="utf-8") == "# Old heartbeat"


# ── 3. rendered content ─────────────────────────────────────────────────

async def test_rendered_content_format(tmp_path: Path) -> None:
    """Output contains section headers for every summary field."""
    mgr = _make_manager(tmp_path)
    summary = HeartbeatSummary(
        recent_actions=["action-1"],
        changing_priorities=["priority-1"],
        unresolved_questions=["question-1"],
        memory_mutations=["mutation-1"],
        contradictions_and_supersessions=["contra-1"],
    )
    await mgr.update(summary)

    content = (tmp_path / "heartbeat.md").read_text(encoding="utf-8")
    assert "## Recent Actions" in content
    assert "- action-1" in content
    assert "## Changing Priorities" in content
    assert "- priority-1" in content
    assert "## Unresolved Questions" in content
    assert "- question-1" in content
    assert "## Memory Mutations" in content
    assert "- mutation-1" in content
    assert "## Contradictions & Supersessions" in content
    assert "- contra-1" in content
    assert "**Timestamp**:" in content


# ── 4. scheduling ────────────────────────────────────────────────────────

async def test_is_due_initially(tmp_path: Path) -> None:
    """is_due() is True when there has been no prior update."""
    mgr = _make_manager(tmp_path)
    assert mgr.is_due() is True


async def test_is_due_after_update(tmp_path: Path) -> None:
    """is_due() is False immediately after an update (interval > 0)."""
    mgr = _make_manager(tmp_path, interval_minutes=60.0)
    await mgr.update(HeartbeatSummary())
    assert mgr.is_due() is False


# ── 5. history ───────────────────────────────────────────────────────────

async def test_get_history(tmp_path: Path) -> None:
    """After multiple updates, history returns entries in reverse chronological order."""
    mgr = _make_manager(tmp_path)

    # Seed distinct archive files with known timestamps
    history_dir = tmp_path / "heartbeat_history"
    stamps = ["20250101T000000", "20250102T000000", "20250103T000000"]
    for stamp in stamps:
        (history_dir / f"{stamp}.md").write_text(f"content-{stamp}", encoding="utf-8")

    entries = await mgr.get_history()
    assert len(entries) == 3
    assert entries[0].timestamp == "20250103T000000"
    assert entries[-1].timestamp == "20250101T000000"


# ── 6. audit logging ────────────────────────────────────────────────────

async def test_update_audit_logged(tmp_path: Path) -> None:
    """When an audit_logger is provided, update() calls log()."""
    mock_audit = MagicMock()
    mgr = _make_manager(tmp_path, audit_logger=mock_audit)

    summary = HeartbeatSummary(recent_actions=["logged action"])
    await mgr.update(summary)

    mock_audit.log.assert_called_once_with(
        mutation_type="heartbeat_update",
        target_id="heartbeat.md",
        evidence=summary.model_dump(),
    )
