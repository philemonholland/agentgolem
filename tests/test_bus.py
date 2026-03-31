"""Tests for the InterAgentBus — messaging, floor, and transcript."""
from __future__ import annotations

import asyncio

import pytest

from agentgolem.runtime.bus import (
    AgentMessage,
    InterAgentBus,
    DISCUSSION_PRIORITY_DEFAULT,
    DISCUSSION_PRIORITY_INITIATOR,
    DISCUSSION_PRIORITY_LAST,
)

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


# ------------------------------------------------------------------
# Discussion priority (speaking order)
# ------------------------------------------------------------------


async def test_register_with_priority() -> None:
    bus = InterAgentBus()
    bus.register("Agent-6", discussion_priority=DISCUSSION_PRIORITY_INITIATOR)
    bus.register("Agent-1", discussion_priority=DISCUSSION_PRIORITY_DEFAULT)
    bus.register("Agent-7", discussion_priority=DISCUSSION_PRIORITY_LAST)
    assert bus.get_priority("Agent-6") == DISCUSSION_PRIORITY_INITIATOR
    assert bus.get_priority("Agent-1") == DISCUSSION_PRIORITY_DEFAULT
    assert bus.get_priority("Agent-7") == DISCUSSION_PRIORITY_LAST


async def test_priority_default_when_unset() -> None:
    bus = InterAgentBus()
    bus.register("Nobody")
    assert bus.get_priority("Nobody") == DISCUSSION_PRIORITY_DEFAULT


async def test_rename_preserves_priority() -> None:
    bus = InterAgentBus()
    bus.register("Council-6", discussion_priority=DISCUSSION_PRIORITY_INITIATOR)
    bus.rename("Council-6", "Harmony")
    assert bus.get_priority("Harmony") == DISCUSSION_PRIORITY_INITIATOR
    assert bus.get_priority("Council-6") == DISCUSSION_PRIORITY_DEFAULT  # old name gone


async def test_priority_floor_ordering() -> None:
    """Highest-priority (lowest number) agent should get the floor first."""
    bus = InterAgentBus()
    bus.register("Low", discussion_priority=99)
    bus.register("Mid", discussion_priority=50)
    bus.register("High", discussion_priority=0)

    # High grabs the floor first
    await bus.acquire_floor("High")

    order: list[str] = []

    async def wait_and_record(name: str) -> None:
        await bus.acquire_floor(name)
        order.append(name)
        bus.release_floor()

    # Mid and Low both want the floor while High holds it
    t_mid = asyncio.create_task(wait_and_record("Mid"))
    t_low = asyncio.create_task(wait_and_record("Low"))
    await asyncio.sleep(0.05)  # let both tasks register in wait queue

    # Release High's floor — priority order should give Mid before Low
    bus.release_floor()
    await asyncio.gather(t_mid, t_low)

    assert order == ["Mid", "Low"], f"Expected Mid before Low, got {order}"


# ------------------------------------------------------------------
# Message truncation
# ------------------------------------------------------------------


async def test_send_truncation(bus: InterAgentBus) -> None:
    long = "a" * 5000
    ok = await bus.send("Alice", "Bob", long, max_chars=100)
    assert ok
    msg = await bus.receive("Bob")
    assert msg is not None
    assert len(msg.text) == 101  # 100 chars + "…"
    assert msg.text.endswith("…")


async def test_broadcast_truncation(bus: InterAgentBus) -> None:
    long = "b" * 5000
    count = await bus.broadcast("Alice", long, max_chars=200)
    assert count == 2  # Bob and Carol
    msg = await bus.receive("Bob")
    assert msg is not None
    assert len(msg.text) == 201  # 200 + "…"


async def test_default_max_chars_applied() -> None:
    bus = InterAgentBus(default_max_chars=50)
    bus.register("X")
    bus.register("Y")
    long = "z" * 200
    await bus.send("X", "Y", long)
    msg = await bus.receive("Y")
    assert msg is not None
    assert len(msg.text) == 51  # 50 + "…"


async def test_explicit_max_chars_overrides_default() -> None:
    bus = InterAgentBus(default_max_chars=50)
    bus.register("X")
    bus.register("Y")
    long = "z" * 200
    await bus.send("X", "Y", long, max_chars=100)
    msg = await bus.receive("Y")
    assert msg is not None
    assert len(msg.text) == 101  # explicit 100 + "…"


async def test_no_truncation_when_under_limit(bus: InterAgentBus) -> None:
    short = "hello"
    await bus.send("Alice", "Bob", short, max_chars=100)
    msg = await bus.receive("Bob")
    assert msg is not None
    assert msg.text == "hello"  # no truncation

