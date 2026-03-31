"""Tests for the InterAgentBus — messaging, floor, and transcript."""
from __future__ import annotations

import asyncio

import pytest

from agentgolem.runtime.bus import AgentMessage, InterAgentBus


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def bus() -> InterAgentBus:
    b = InterAgentBus()
    b.register("Alice")
    b.register("Bob")
    b.register("Carol")
    return b


# ------------------------------------------------------------------
# Basic messaging (existing behaviour, regression)
# ------------------------------------------------------------------


async def test_send_direct(bus: InterAgentBus) -> None:
    ok = await bus.send("Alice", "Bob", "hello")
    assert ok is True
    msg = await bus.receive("Bob")
    assert msg is not None
    assert msg.from_agent == "Alice"
    assert msg.text == "hello"
    assert msg.to_agent == "Bob"


async def test_broadcast(bus: InterAgentBus) -> None:
    count = await bus.broadcast("Alice", "hi all")
    assert count == 2  # Bob + Carol
    bob_msg = await bus.receive("Bob")
    carol_msg = await bus.receive("Carol")
    assert bob_msg is not None and carol_msg is not None
    assert bob_msg.text == carol_msg.text == "hi all"
    # Alice should NOT get her own broadcast
    assert await bus.receive("Alice") is None


async def test_receive_nonblocking_empty(bus: InterAgentBus) -> None:
    msg = await bus.receive("Alice")
    assert msg is None


async def test_pending_count(bus: InterAgentBus) -> None:
    assert bus.pending_count("Bob") == 0
    await bus.send("Alice", "Bob", "m1")
    await bus.send("Alice", "Bob", "m2")
    assert bus.pending_count("Bob") == 2


async def test_rename(bus: InterAgentBus) -> None:
    await bus.send("Alice", "Bob", "before rename")
    bus.rename("Bob", "Robert")
    msg = await bus.receive("Robert")
    assert msg is not None
    assert msg.text == "before rename"
    ok = await bus.send("Alice", "Robert", "after rename")
    assert ok is True


async def test_resolve_name(bus: InterAgentBus) -> None:
    assert bus.resolve_name("alice") == "Alice"
    assert bus.resolve_name("ALICE") == "Alice"
    assert bus.resolve_name("nobody") is None


async def test_get_peers(bus: InterAgentBus) -> None:
    peers = bus.get_peers("Alice")
    assert set(peers) == {"Bob", "Carol"}


# ------------------------------------------------------------------
# Discussion floor (turn-taking)
# ------------------------------------------------------------------


async def test_floor_initially_free(bus: InterAgentBus) -> None:
    assert bus.floor_locked() is False
    assert bus.floor_holder is None


async def test_acquire_release_floor(bus: InterAgentBus) -> None:
    await bus.acquire_floor("Alice")
    assert bus.floor_locked() is True
    assert bus.floor_holder == "Alice"
    bus.release_floor()
    assert bus.floor_locked() is False
    assert bus.floor_holder is None


async def test_hold_floor_context_manager(bus: InterAgentBus) -> None:
    async with bus.hold_floor("Bob") as transcript:
        assert isinstance(transcript, list)
        assert bus.floor_holder == "Bob"
    assert bus.floor_locked() is False


async def test_floor_serialises_access(bus: InterAgentBus) -> None:
    """Two agents trying to speak — second must wait for first to finish."""
    order: list[str] = []

    async def speaker(name: str, delay: float) -> None:
        await bus.acquire_floor(name)
        order.append(f"{name}_start")
        await asyncio.sleep(delay)
        order.append(f"{name}_end")
        bus.release_floor()

    # Alice grabs the floor first, Bob must wait
    await bus.acquire_floor("Alice")
    bob_task = asyncio.create_task(speaker("Bob", 0.01))
    await asyncio.sleep(0.02)  # give Bob time to attempt acquire
    order.append("Alice_start")
    order.append("Alice_end")
    bus.release_floor()
    await bob_task

    assert order == [
        "Alice_start",
        "Alice_end",
        "Bob_start",
        "Bob_end",
    ]


async def test_floor_fifo_ordering(bus: InterAgentBus) -> None:
    """Three agents compete — served in FIFO order."""
    order: list[str] = []

    async def speaker(name: str) -> None:
        await bus.acquire_floor(name)
        order.append(name)
        await asyncio.sleep(0.01)
        bus.release_floor()

    # Lock the floor, then queue Bob and Carol
    await bus.acquire_floor("Alice")
    bob_task = asyncio.create_task(speaker("Bob"))
    await asyncio.sleep(0.01)
    carol_task = asyncio.create_task(speaker("Carol"))
    await asyncio.sleep(0.01)
    order.append("Alice")
    bus.release_floor()
    await bob_task
    await carol_task

    assert order[0] == "Alice"
    # Bob queued first so should go before Carol
    assert order[1] == "Bob"
    assert order[2] == "Carol"


# ------------------------------------------------------------------
# Transcript
# ------------------------------------------------------------------


async def test_send_records_transcript(bus: InterAgentBus) -> None:
    await bus.send("Alice", "Bob", "noted")
    transcript = bus.get_transcript()
    assert len(transcript) == 1
    assert transcript[0].from_agent == "Alice"
    assert transcript[0].text == "noted"


async def test_broadcast_records_single_transcript_entry(
    bus: InterAgentBus,
) -> None:
    await bus.broadcast("Alice", "shared insight")
    transcript = bus.get_transcript()
    assert len(transcript) == 1  # one entry, not one per recipient


async def test_transcript_limit(bus: InterAgentBus) -> None:
    small_bus = InterAgentBus(max_transcript=5)
    small_bus.register("A")
    small_bus.register("B")
    for i in range(20):
        await small_bus.send("A", "B", f"msg-{i}")
    transcript = small_bus.get_transcript(limit=100)
    # Should keep at most max_transcript * 2 then trim to max_transcript
    assert len(transcript) <= 10  # 5 * 2 before trim


async def test_format_transcript(bus: InterAgentBus) -> None:
    await bus.send("Alice", "Bob", "first idea")
    await bus.send("Bob", "Alice", "second idea")
    formatted = bus.format_transcript(limit=10, exclude="Alice")
    assert "Bob" in formatted
    assert "second idea" in formatted
    assert "Alice" not in formatted  # excluded


async def test_format_transcript_truncates_long_messages(
    bus: InterAgentBus,
) -> None:
    long_msg = "x" * 1000
    await bus.send("Alice", "Bob", long_msg)
    formatted = bus.format_transcript(limit=10, max_chars=50)
    assert "…" in formatted
    assert len(formatted) < 500


async def test_format_transcript_empty(bus: InterAgentBus) -> None:
    assert bus.format_transcript() == ""


async def test_hold_floor_yields_transcript(bus: InterAgentBus) -> None:
    await bus.broadcast("Alice", "context message")
    async with bus.hold_floor("Bob") as transcript:
        assert len(transcript) == 1
        assert transcript[0].text == "context message"
