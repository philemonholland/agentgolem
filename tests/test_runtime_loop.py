"""Tests for the main runtime loop."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest

from agentgolem.config.secrets import Secrets
from agentgolem.config.settings import Settings
from agentgolem.runtime.loop import MainLoop
from agentgolem.runtime.state import AgentMode
from agentgolem.tools.base import ApprovalGate

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def loop_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Settings, Secrets, Path]:
    """Set up a temporary environment for MainLoop tests."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "soul.md").write_text("test soul", encoding="utf-8")
    (tmp_path / "heartbeat.md").write_text("test heartbeat", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", awake_duration_minutes=9999)
    secrets = Secrets(_env_file=None)
    return settings, secrets, tmp_path


async def test_main_loop_starts_and_stops(loop_env: tuple[Settings, Secrets, Path]) -> None:
    settings, secrets, _ = loop_env
    loop = MainLoop(settings=settings, secrets=secrets)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.5)
    loop.stop()
    await asyncio.sleep(0.3)
    assert task.done() or task.cancelled()
    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_main_loop_transitions_to_awake(loop_env: tuple[Settings, Secrets, Path]) -> None:
    settings, secrets, _ = loop_env
    loop = MainLoop(settings=settings, secrets=secrets)
    # Starts in PAUSED
    assert loop.runtime_state.mode == AgentMode.PAUSED
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.3)
    # After run(), should have transitioned to AWAKE
    assert loop.runtime_state.mode == AgentMode.AWAKE
    loop.stop()
    await asyncio.sleep(0.3)
    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_inbox_message_processing(loop_env: tuple[Settings, Secrets, Path]) -> None:
    settings, secrets, tmp_path = loop_env
    loop = MainLoop(settings=settings, secrets=secrets)
    # Ensure dirs exist
    loop._ensure_dirs()
    inbox_dir = settings.data_dir / "inbox"
    msg_file = inbox_dir / "human_001.json"
    msg_file.write_text(json.dumps({"text": "hello agent"}), encoding="utf-8")

    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.5)
    loop.stop()
    await asyncio.sleep(0.3)

    # Message file should have been consumed
    assert not msg_file.exists()

    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_interrupt_preempts(loop_env: tuple[Settings, Secrets, Path]) -> None:
    settings, secrets, _ = loop_env
    loop = MainLoop(settings=settings, secrets=secrets)
    loop._ensure_dirs()

    # Transition to AWAKE so _tick doesn't block on wait_for_resume
    await loop.runtime_state.transition(AgentMode.AWAKE)

    # Set interrupt flag
    loop.interrupt_manager._interrupt_event.set()
    assert loop.interrupt_manager.check_interrupt()

    # Run a single tick
    await loop._tick()

    # Interrupt should have been cleared
    assert not loop.interrupt_manager.check_interrupt()


async def test_heartbeat_runs_when_due(loop_env: tuple[Settings, Secrets, Path]) -> None:
    settings, secrets, tmp_path = loop_env
    # Very short awake + wind-down so the heartbeat fires quickly
    settings = Settings(
        data_dir=settings.data_dir,
        awake_duration_minutes=0.001,
        wind_down_minutes=0.001,
        sleep_duration_minutes=9999,
    )
    loop = MainLoop(settings=settings, secrets=secrets)
    loop._ensure_dirs()

    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.5)
    loop.stop()
    await asyncio.sleep(0.3)

    # heartbeat.md should have been updated with the rendered content
    heartbeat_path = settings.data_dir / "heartbeat.md"
    heartbeat_content = heartbeat_path.read_text(encoding="utf-8")
    assert "Heartbeat" in heartbeat_content
    assert "Heartbeat cycle executed" in heartbeat_content

    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_auto_sleep_wake_cycle(loop_env: tuple[Settings, Secrets, Path]) -> None:
    """Agent should auto-transition: AWAKE → wind-down → ASLEEP → AWAKE."""
    settings, secrets, _ = loop_env
    # Tiny durations so the full cycle completes in < 1 second
    settings = Settings(
        data_dir=settings.data_dir,
        awake_duration_minutes=0.001,  # ~0.06s
        wind_down_minutes=0.001,  # ~0.06s
        sleep_duration_minutes=0.001,  # ~0.06s
    )
    loop = MainLoop(settings=settings, secrets=secrets)
    loop._ensure_dirs()

    task = asyncio.create_task(loop.run())
    # Give enough time for at least one full cycle
    await asyncio.sleep(1.0)

    # Agent should have cycled back to AWAKE (or still cycling)
    # The key assertion: it didn't crash, and it has transitioned at least once
    mode = loop.runtime_state.mode
    assert mode in (AgentMode.AWAKE, AgentMode.ASLEEP)

    loop.stop()
    await asyncio.sleep(0.3)
    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def test_main_loop_prefers_deepseek_for_discussion_and_openai_for_code(
    loop_env: tuple[Settings, Secrets, Path],
) -> None:
    settings, _, _ = loop_env
    routed_settings = Settings(
        data_dir=settings.data_dir,
        llm_model="gpt-4.1",
        llm_discussion_model="deepseek-reasoner",
        llm_code_model="gpt-5.4",
    )
    secrets = Secrets(
        _env_file=None,
        openai_api_key="sk-openai-test",
        openai_base_url="https://api.openai.com/v1",
        deepseek_api_key="sk-deepseek-test",
        deepseek_base_url="https://api.deepseek.com/v1",
    )

    loop = MainLoop(settings=routed_settings, secrets=secrets)

    assert loop._resolve_model_name(loop._llm) == "deepseek-reasoner"
    assert loop._resolve_model_name(loop._code_llm) == "gpt-5.4"


def test_main_loop_supports_route_specific_llm_overrides(
    loop_env: tuple[Settings, Secrets, Path],
) -> None:
    settings, _, _ = loop_env
    routed_settings = Settings(
        data_dir=settings.data_dir,
        llm_model="gpt-4.1",
        llm_discussion_model="custom-discussion-model",
        llm_code_model="custom-code-model",
    )
    secrets = Secrets(
        _env_file=None,
        openai_api_key="sk-openai-test",
        openai_base_url="https://api.openai.com/v1",
        llm_discussion_api_key="sk-discussion-test",
        llm_discussion_base_url="https://discussion.example/v1",
        llm_code_api_key="sk-code-test",
        llm_code_base_url="https://code.example/v1",
    )

    loop = MainLoop(settings=routed_settings, secrets=secrets)

    discussion_client = getattr(loop._llm, "_inner", loop._llm)
    code_client = getattr(loop._code_llm, "_inner", loop._code_llm)
    assert discussion_client._base_url == "https://discussion.example/v1"
    assert code_client._base_url == "https://code.example/v1"
    assert loop._resolve_model_name(loop._llm) == "custom-discussion-model"
    assert loop._resolve_model_name(loop._code_llm) == "custom-code-model"


def test_discussion_style_guidance_discourages_planning(
    loop_env: tuple[Settings, Secrets, Path],
) -> None:
    settings, secrets, _ = loop_env
    loop = MainLoop(settings=settings, secrets=secrets)

    guidance = loop._discussion_style_guidance()

    assert "curious colleague" in guidance
    assert "project manager" in guidance
    assert "implementation plans" in guidance


async def test_build_memory_context_keeps_peer_memories_separate(
    loop_env: tuple[Settings, Secrets, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, secrets, _ = loop_env
    loop = MainLoop(settings=settings, secrets=secrets)

    async def fake_local(context: str, top_k: int = 5) -> str:
        assert context == "naming resonance"
        return "Relevant memories:\n- Local identity reflection"

    async def fake_peer(context: str, top_k: int = 3) -> str:
        assert context == "naming resonance"
        return "Entangled peer memories:\n- [Council-2] A remembered thread of grace"

    monkeypatch.setattr(loop, "_recall_relevant_memories", fake_local)
    monkeypatch.setattr(loop, "_recall_entangled_peer_memories", fake_peer)

    context = await loop._build_memory_context("naming resonance", top_k=5)

    assert context == (
        "Relevant memories:\n- Local identity reflection\n\n"
        "Entangled peer memories:\n- [Council-2] A remembered thread of grace"
    )


def test_configure_tool_registry_exposes_capabilities(
    loop_env: tuple[Settings, Secrets, Path],
) -> None:
    settings, _, _ = loop_env
    settings = Settings(
        data_dir=settings.data_dir,
        email_enabled=True,
        moltbook_enabled=True,
    )
    secrets = Secrets(
        _env_file=None,
        email_smtp_host="smtp.example.com",
        email_smtp_user="agent@example.com",
        email_smtp_password="secret-pass",
        moltbook_api_key="mk-test",
        moltbook_base_url="https://moltbook.example/api",
    )
    loop = MainLoop(settings=settings, secrets=secrets)
    loop._ensure_dirs()
    loop._approval_gate = ApprovalGate(
        settings.data_dir / "approvals", ["email_send", "moltbook_send"]
    )
    loop.configure_tool_registry()

    summary = loop._toolbox_summary()

    assert "browser.fetch_text" in summary
    assert "email.send" in summary
    assert "moltbook.send" in summary
    assert "think.private" in summary
    assert "approval=email_send" in summary
    assert "approval=moltbook_send" in summary
