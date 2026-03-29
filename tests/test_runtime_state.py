"""Tests for the agent runtime state machine and interrupt system."""
from __future__ import annotations

import asyncio

import pytest

from agentgolem.runtime.interrupts import InterruptManager
from agentgolem.runtime.state import AgentMode, RuntimeState


# ── State machine tests ──────────────────────────────────────────────


async def test_initial_state(tmp_path):
    """New RuntimeState starts in PAUSED mode."""
    rs = RuntimeState(tmp_path)
    assert rs.mode is AgentMode.PAUSED


async def test_legal_transitions(tmp_path):
    """PAUSED→AWAKE, AWAKE→ASLEEP, ASLEEP→PAUSED all succeed."""
    rs = RuntimeState(tmp_path)
    await rs.transition(AgentMode.AWAKE)
    assert rs.mode is AgentMode.AWAKE

    await rs.transition(AgentMode.ASLEEP)
    assert rs.mode is AgentMode.ASLEEP

    await rs.transition(AgentMode.PAUSED)
    assert rs.mode is AgentMode.PAUSED


async def test_self_transition_is_noop(tmp_path):
    """Transitioning to the current mode is a silent no-op."""
    rs = RuntimeState(tmp_path)
    assert rs.mode is AgentMode.PAUSED
    await rs.transition(AgentMode.PAUSED)  # no error
    assert rs.mode is AgentMode.PAUSED


async def test_state_persistence(tmp_path):
    """Mode survives across RuntimeState instances sharing the same data_dir."""
    rs1 = RuntimeState(tmp_path)
    await rs1.transition(AgentMode.AWAKE)

    rs2 = RuntimeState(tmp_path)
    assert rs2.mode is AgentMode.AWAKE


# ── Interrupt manager tests ──────────────────────────────────────────


async def test_interrupt_flag():
    """request_interrupt sets the flag; clear_interrupt resets it."""
    im = InterruptManager()
    assert im.check_interrupt() is False

    await im.request_interrupt("test")
    assert im.check_interrupt() is True

    im.clear_interrupt()
    assert im.check_interrupt() is False


async def test_message_queue():
    """send_message queues a message and sets the interrupt flag."""
    im = InterruptManager()
    await im.send_message("hello")

    assert im.check_interrupt() is True
    assert im.has_messages() is True

    msg = await im.get_message(timeout=1.0)
    assert msg is not None
    assert msg.text == "hello"
    assert im.has_messages() is False


async def test_get_message_timeout():
    """get_message with timeout returns None when queue is empty."""
    im = InterruptManager()
    msg = await im.get_message(timeout=0.05)
    assert msg is None


async def test_resume_signal():
    """wait_for_resume blocks until signal_resume is called."""
    im = InterruptManager()
    resumed = False

    async def waiter():
        nonlocal resumed
        await im.wait_for_resume()
        resumed = True

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    assert resumed is False

    im.signal_resume()
    await asyncio.sleep(0.05)
    assert resumed is True
    await task


# ── Serialisation test ───────────────────────────────────────────────


async def test_to_dict(tmp_path):
    """to_dict returns the expected keys and values."""
    rs = RuntimeState(tmp_path)
    d = rs.to_dict()
    assert set(d.keys()) == {"mode", "current_task", "pending_tasks", "started_at"}
    assert d["mode"] == "paused"
    assert d["current_task"] is None
    assert d["pending_tasks"] == []
    assert isinstance(d["started_at"], str)
