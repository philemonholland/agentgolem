"""Tests for the sleep-cycle scheduler."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentgolem.runtime.state import AgentMode
from agentgolem.sleep.scheduler import CycleResult, SleepScheduler, SleepState
from agentgolem.sleep.walker import WalkResult


# ---------------------------------------------------------------------------
# Mock walker
# ---------------------------------------------------------------------------

class MockWalker:
    """Minimal mock that satisfies the GraphWalker interface."""

    def __init__(self) -> None:
        self.seed_calls = 0
        self.walk_calls = 0

    async def sample_seeds(self, n: int) -> list[str]:
        self.seed_calls += 1
        return [f"seed_{i}" for i in range(n)]

    async def bounded_walk(
        self,
        seed_id: str,
        max_steps: int = 50,
        max_time_ms: int = 5000,
        interrupt_check=None,
    ) -> WalkResult:
        self.walk_calls += 1
        return WalkResult(
            seed_id=seed_id,
            visited_node_ids=[seed_id],
            edge_activations={},
            proposed_actions=[],
            steps_taken=1,
            time_ms=10.0,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def scheduler(tmp_path: Path) -> SleepScheduler:
    return SleepScheduler(
        cycle_minutes=5.0,
        max_nodes_per_cycle=100,
        max_time_ms=5000,
        state_path=tmp_path,
    )


@pytest.fixture
def walker() -> MockWalker:
    return MockWalker()


# ---------------------------------------------------------------------------
# Tests — should_run
# ---------------------------------------------------------------------------

def test_should_run_when_asleep(scheduler: SleepScheduler) -> None:
    """Returns True when ASLEEP and enough time has passed (first run)."""
    assert scheduler.should_run(AgentMode.ASLEEP) is True


def test_should_not_run_when_awake(scheduler: SleepScheduler) -> None:
    """Returns False when mode is AWAKE."""
    assert scheduler.should_run(AgentMode.AWAKE) is False


def test_should_not_run_when_paused(scheduler: SleepScheduler) -> None:
    """Returns False when mode is PAUSED."""
    assert scheduler.should_run(AgentMode.PAUSED) is False


def test_should_not_run_too_soon(tmp_path: Path) -> None:
    """Returns False if last cycle was too recent."""
    recent = datetime.now(timezone.utc).isoformat()
    state_file = tmp_path / "sleep_state.json"
    state_file.write_text(json.dumps({
        "last_cycle_time": recent,
        "cycles_completed": 1,
        "items_queued": 0,
    }))

    sched = SleepScheduler(cycle_minutes=5.0, state_path=tmp_path)
    assert sched.should_run(AgentMode.ASLEEP) is False


def test_first_run_allowed(tmp_path: Path) -> None:
    """First run with no prior state is allowed."""
    sched = SleepScheduler(state_path=tmp_path)
    assert sched.get_state().last_cycle_time == ""
    assert sched.should_run(AgentMode.ASLEEP) is True


# ---------------------------------------------------------------------------
# Tests — run_cycle
# ---------------------------------------------------------------------------

async def test_run_cycle_completes(
    scheduler: SleepScheduler, walker: MockWalker
) -> None:
    """Cycle runs and returns a CycleResult."""
    result = await scheduler.run_cycle(walker)

    assert isinstance(result, CycleResult)
    assert result.walks_completed == 5
    assert result.interrupted is False
    assert result.duration_ms >= 0
    assert walker.seed_calls == 1
    assert walker.walk_calls == 5


async def test_run_cycle_respects_budget(tmp_path: Path) -> None:
    """bounded_walk receives correctly partitioned budget."""
    recorded_args: list[dict] = []

    class RecordingWalker(MockWalker):
        async def bounded_walk(self, seed_id, max_steps=50, max_time_ms=5000, interrupt_check=None):
            recorded_args.append({"max_steps": max_steps, "max_time_ms": max_time_ms})
            return await super().bounded_walk(seed_id, max_steps, max_time_ms, interrupt_check)

    sched = SleepScheduler(
        max_nodes_per_cycle=100,
        max_time_ms=5000,
        state_path=tmp_path,
    )
    await sched.run_cycle(RecordingWalker())

    for args in recorded_args:
        assert args["max_steps"] == 20   # 100 // 5
        assert args["max_time_ms"] == 1000  # 5000 // 5


async def test_run_cycle_interrupt(
    scheduler: SleepScheduler, walker: MockWalker
) -> None:
    """Stops when interrupt_check returns True."""
    result = await scheduler.run_cycle(walker, interrupt_check=lambda: True)

    assert result.interrupted is True
    assert result.walks_completed == 0
    assert walker.walk_calls == 0


async def test_run_cycle_interrupt_mid_walk(tmp_path: Path) -> None:
    """Interrupt during iteration stops after the current walk."""
    call_count = 0

    def interrupt_after_two() -> bool:
        return call_count >= 2

    class CountingWalker(MockWalker):
        async def bounded_walk(self, seed_id, max_steps=50, max_time_ms=5000, interrupt_check=None):
            nonlocal call_count
            call_count += 1
            return await super().bounded_walk(seed_id, max_steps, max_time_ms, interrupt_check)

    sched = SleepScheduler(state_path=tmp_path)
    result = await sched.run_cycle(CountingWalker(), interrupt_check=interrupt_after_two)

    assert result.interrupted is True
    assert result.walks_completed == 2


async def test_run_cycle_tracks_applied_actions_and_mycelium_updates(
    tmp_path: Path,
) -> None:
    """Cycle reports both local action application and post-walk updates."""

    class ActionWalker(MockWalker):
        async def bounded_walk(self, seed_id, max_steps=50, max_time_ms=5000, interrupt_check=None):
            self.walk_calls += 1
            return WalkResult(
                seed_id=seed_id,
                visited_node_ids=[seed_id],
                edge_activations={},
                proposed_actions=[
                    {
                        "kind": "reinforce_edge",
                        "source_id": seed_id,
                        "target_id": f"{seed_id}-neighbor",
                        "delta": 0.1,
                    }
                ],
                steps_taken=1,
                time_ms=10.0,
            )

        async def apply_actions(self, actions):
            return len(actions)

    scheduler = SleepScheduler(state_path=tmp_path)

    async def post_walk_callback(result: WalkResult) -> int:
        assert len(result.proposed_actions) == 1
        return 2

    result = await scheduler.run_cycle(
        ActionWalker(),
        post_walk_callback=post_walk_callback,
    )

    assert result.walks_completed == 5
    assert result.applied_actions == 5
    assert result.mycelium_updates == 10
    assert result.items_queued == 15


# ---------------------------------------------------------------------------
# Tests — state persistence
# ---------------------------------------------------------------------------

def test_state_persistence(tmp_path: Path) -> None:
    """State round-trips through JSON."""
    state = SleepState(
        last_cycle_time="2025-01-01T00:00:00+00:00",
        cycles_completed=7,
        items_queued=42,
    )
    state_file = tmp_path / "sleep_state.json"
    state_file.write_text(json.dumps({
        "last_cycle_time": state.last_cycle_time,
        "cycles_completed": state.cycles_completed,
        "items_queued": state.items_queued,
    }))

    sched = SleepScheduler(state_path=tmp_path)
    loaded = sched.get_state()

    assert loaded.last_cycle_time == state.last_cycle_time
    assert loaded.cycles_completed == state.cycles_completed
    assert loaded.items_queued == state.items_queued


async def test_cycles_completed_increments(
    tmp_path: Path, walker: MockWalker
) -> None:
    """Counter goes up after each cycle."""
    sched = SleepScheduler(state_path=tmp_path)

    await sched.run_cycle(walker)
    assert sched.get_state().cycles_completed == 1

    await sched.run_cycle(walker)
    assert sched.get_state().cycles_completed == 2

    await sched.run_cycle(walker)
    assert sched.get_state().cycles_completed == 3


async def test_state_persisted_after_cycle(tmp_path: Path, walker: MockWalker) -> None:
    """State file is written after a cycle and survives a new scheduler instance."""
    sched = SleepScheduler(state_path=tmp_path)
    await sched.run_cycle(walker)

    # Create a brand-new scheduler pointing at the same directory
    sched2 = SleepScheduler(state_path=tmp_path)
    assert sched2.get_state().cycles_completed == 1
    assert sched2.get_state().last_cycle_time != ""
