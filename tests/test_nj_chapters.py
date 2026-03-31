"""Tests for the chapter-by-chapter Niscalajyoti reading system."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.config.secrets import Secrets
from agentgolem.config.settings import Settings
from agentgolem.runtime.loop import (
    NISCALAJYOTI_CHAPTERS,
    MainLoop,
)


def _make_loop(tmp_path: Path) -> MainLoop:
    """Build a minimal MainLoop for chapter reading tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for d in (
        "soul_versions", "heartbeat_history", "logs",
        "memory", "memory/snapshots", "approvals",
        "inbox", "outbox", "state",
    ):
        (data_dir / d).mkdir(parents=True, exist_ok=True)
    (data_dir / "soul.md").write_text("# Test Soul\n", encoding="utf-8")
    (data_dir / "heartbeat.md").write_text("# Heartbeat\n", encoding="utf-8")

    settings = Settings(data_dir=data_dir)
    secrets = Secrets(openai_api_key="", openai_base_url="")
    return MainLoop(
        settings=settings,
        secrets=secrets,
        agent_name="TestReader",
        ethical_vector="kindness",
    )


# ------------------------------------------------------------------
# Chapter list
# ------------------------------------------------------------------

class TestChapterList:
    """The chapter list is well-formed and covers the site."""

    def test_chapter_count(self) -> None:
        assert len(NISCALAJYOTI_CHAPTERS) >= 20

    def test_chapters_have_url_and_title(self) -> None:
        for ch in NISCALAJYOTI_CHAPTERS:
            assert "url" in ch and "title" in ch
            assert ch["url"].startswith("http")
            assert len(ch["title"]) > 3

    def test_no_pdf_in_chapters(self) -> None:
        for ch in NISCALAJYOTI_CHAPTERS:
            assert not ch["url"].lower().endswith(".pdf")

    def test_first_chapter_is_root(self) -> None:
        assert "niscalajyoti.org" in NISCALAJYOTI_CHAPTERS[0]["url"]


# ------------------------------------------------------------------
# Reading state persistence
# ------------------------------------------------------------------

class TestReadingStatePersistence:
    """Niscalajyoti reading state saves and reloads correctly."""

    def test_initial_state(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._niscalajyoti_chapter_index == 0
        assert loop._niscalajyoti_discussed_through == -1
        assert loop._niscalajyoti_reading_complete is False
        assert loop._niscalajyoti_summaries == {}

    def test_save_and_reload(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop._niscalajyoti_chapter_index = 5
        loop._niscalajyoti_discussed_through = 4
        loop._niscalajyoti_summaries = {
            0: "First chapter summary",
            1: "Second chapter summary",
            4: "Fifth chapter summary",
        }
        loop._save_nj_reading_state()

        # Verify file exists
        state_path = loop._data_dir / "niscalajyoti_reading.json"
        assert state_path.exists()

        # Create a new loop and verify state loaded
        loop2 = _make_loop(tmp_path)
        assert loop2._niscalajyoti_chapter_index == 5
        assert loop2._niscalajyoti_discussed_through == 4
        assert loop2._niscalajyoti_summaries == {
            0: "First chapter summary",
            1: "Second chapter summary",
            4: "Fifth chapter summary",
        }

    def test_reading_complete_persists(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop._niscalajyoti_reading_complete = True
        loop._niscalajyoti_chapter_index = len(NISCALAJYOTI_CHAPTERS)
        loop._save_nj_reading_state()

        loop2 = _make_loop(tmp_path)
        assert loop2._niscalajyoti_reading_complete is True

    def test_corrupt_file_doesnt_crash(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        state_path = data_dir / "niscalajyoti_reading.json"
        state_path.write_text("not valid json{{{", encoding="utf-8")

        # Should not raise
        loop = _make_loop(tmp_path)
        assert loop._niscalajyoti_chapter_index == 0

    def test_summaries_key_conversion(self, tmp_path: Path) -> None:
        """JSON converts int keys to strings — verify round-trip."""
        loop = _make_loop(tmp_path)
        loop._niscalajyoti_summaries = {3: "test", 7: "test2"}
        loop._save_nj_reading_state()

        loop2 = _make_loop(tmp_path)
        assert 3 in loop2._niscalajyoti_summaries
        assert 7 in loop2._niscalajyoti_summaries


# ------------------------------------------------------------------
# Reading flow logic
# ------------------------------------------------------------------

class TestReadingFlow:
    """Verify the chapter-by-chapter reading priority logic."""

    def test_discussion_follows_reading(self, tmp_path: Path) -> None:
        """After reading ch N, discussed_through should be < N-1."""
        loop = _make_loop(tmp_path)
        loop._niscalajyoti_chapter_index = 3  # read chapters 0,1,2
        loop._niscalajyoti_discussed_through = 1  # discussed 0,1

        # Should need to discuss chapter 2 before reading chapter 3
        needs_discuss = (
            not loop._niscalajyoti_reading_complete
            and loop._niscalajyoti_chapter_index > 0
            and loop._niscalajyoti_discussed_through
            < loop._niscalajyoti_chapter_index - 1
        )
        assert needs_discuss

    def test_reading_complete_when_all_read_and_discussed(
        self, tmp_path: Path
    ) -> None:
        loop = _make_loop(tmp_path)
        total = len(NISCALAJYOTI_CHAPTERS)
        loop._niscalajyoti_chapter_index = total
        loop._niscalajyoti_discussed_through = total - 1
        loop._niscalajyoti_reading_complete = False

        # The tick_autonomous would set this, but test the condition
        should_complete = (
            loop._niscalajyoti_chapter_index >= total
            and loop._niscalajyoti_discussed_through >= total - 1
        )
        assert should_complete


# ------------------------------------------------------------------
# Peer check-in parameter
# ------------------------------------------------------------------

class TestPeerCheckin:
    """peer_checkin_interval_minutes parameter works correctly."""

    def test_default_value(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._peer_checkin_interval == 30.0

    def test_settings_has_field(self) -> None:
        s = Settings()
        assert hasattr(s, "peer_checkin_interval_minutes")
        assert s.peer_checkin_interval_minutes == 30.0

    def test_custom_value(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        for d in (
            "soul_versions", "heartbeat_history", "logs",
            "memory", "memory/snapshots", "approvals",
            "inbox", "outbox", "state",
        ):
            (data_dir / d).mkdir(parents=True, exist_ok=True)
        (data_dir / "soul.md").write_text("# Soul\n", encoding="utf-8")
        (data_dir / "heartbeat.md").write_text("# HB\n", encoding="utf-8")

        settings = Settings(
            data_dir=data_dir,
            peer_checkin_interval_minutes=25.0,
        )
        secrets = Secrets(openai_api_key="", openai_base_url="")
        loop = MainLoop(
            settings=settings,
            secrets=secrets,
            agent_name="Custom",
            ethical_vector="testing",
        )
        assert loop._peer_checkin_interval == 25.0


class _FakeBrowser:
    def __init__(self, text: str) -> None:
        self._text = text
        self.fetch_calls = 0

    async def fetch(self, url: str) -> str:
        self.fetch_calls += 1
        return f"<html>{url}</html>"

    def extract_text(self, page: str) -> str:
        return self._text


async def test_read_chapter_uses_user_grounded_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _make_loop(tmp_path)
    browser = _FakeBrowser("Full chapter text. " * 40)
    captured_messages: list[list] = []

    async def fake_complete(messages, **kwargs):
        captured_messages.append(messages)
        if len(captured_messages) == 1:
            return "Detailed digest grounded in the provided chapter text."
        return "Thoughtful reflection.\nSUMMARY: concise remembered summary."

    async def fake_encode(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(loop, "_get_browser", lambda: browser)
    monkeypatch.setattr(loop, "_complete_discussion", fake_complete)
    monkeypatch.setattr(loop, "_encode_to_memory", fake_encode)

    await loop._read_niscalajyoti_chapter()

    assert len(captured_messages) == 2
    assert captured_messages[0][0].role == "system"
    assert captured_messages[0][1].role == "user"
    assert "--- FULL TEXT ---" in captured_messages[0][1].content
    assert captured_messages[1][0].role == "system"
    assert captured_messages[1][1].role == "user"
    assert "--- CHAPTER DIGEST ---" in captured_messages[1][1].content
    assert loop._niscalajyoti_chapter_index == 1


async def test_read_chapter_does_not_cache_ungrounded_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _make_loop(tmp_path)
    browser = _FakeBrowser("Full chapter text. " * 40)

    async def fake_complete(messages, **kwargs):
        return (
            "I’m happy to do that, but I don’t have the actual text of the chapter. "
            "If you paste the chapter here, I’ll reflect on it."
        )

    monkeypatch.setattr(loop, "_get_browser", lambda: browser)
    monkeypatch.setattr(loop, "_complete_discussion", fake_complete)

    await loop._read_niscalajyoti_chapter()

    digest_path = loop._data_dir.parent / "nj_chapter_digests" / "ch_01.txt"
    assert not digest_path.exists()
    assert loop._niscalajyoti_chapter_index == 0
    assert loop._niscalajyoti_chapter_retries == 1


async def test_read_chapter_regenerates_invalid_cached_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _make_loop(tmp_path)
    browser = _FakeBrowser("Full chapter text. " * 40)
    digest_dir = loop._data_dir.parent / "nj_chapter_digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_path = digest_dir / "ch_01.txt"
    digest_path.write_text(
        "I’m missing the actual text of Chapter 1. If you paste the chapter here, I can help.",
        encoding="utf-8",
    )
    call_count = {"count": 0}

    async def fake_complete(messages, **kwargs):
        call_count["count"] += 1
        if call_count["count"] == 1:
            return "Detailed digest grounded in the provided chapter text."
        return "Thoughtful reflection.\nSUMMARY: concise remembered summary."

    async def fake_encode(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(loop, "_get_browser", lambda: browser)
    monkeypatch.setattr(loop, "_complete_discussion", fake_complete)
    monkeypatch.setattr(loop, "_encode_to_memory", fake_encode)

    await loop._read_niscalajyoti_chapter()

    assert browser.fetch_calls == 1
    assert call_count["count"] == 2
    assert digest_path.read_text(encoding="utf-8") == "Detailed digest grounded in the provided chapter text."
