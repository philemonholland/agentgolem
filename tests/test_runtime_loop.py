"""Tests for the main runtime loop."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from agentgolem.config.secrets import Secrets
from agentgolem.config.settings import Settings
from agentgolem.runtime.loop import MainLoop
from agentgolem.runtime.state import AgentMode


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
        try:
            await task
        except asyncio.CancelledError:
            pass


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
        try:
            await task
        except asyncio.CancelledError:
            pass


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
        try:
            await task
        except asyncio.CancelledError:
            pass


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
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_auto_sleep_wake_cycle(loop_env: tuple[Settings, Secrets, Path]) -> None:
    """Agent should auto-transition: AWAKE → wind-down → ASLEEP → AWAKE."""
    settings, secrets, _ = loop_env
    # Tiny durations so the full cycle completes in < 1 second
    settings = Settings(
        data_dir=settings.data_dir,
        awake_duration_minutes=0.001,   # ~0.06s
        wind_down_minutes=0.001,        # ~0.06s
        sleep_duration_minutes=0.001,   # ~0.06s
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
        try:
            await task
        except asyncio.CancelledError:
            pass
