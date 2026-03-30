"""Sleep-cycle scheduler for background memory consolidation."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from agentgolem.runtime.state import AgentMode
from agentgolem.sleep.walker import GraphWalker, WalkResult


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CycleResult:
    """Result of one sleep cycle."""

    walks_completed: int
    items_queued: int
    duration_ms: float
    applied_actions: int = 0
    mycelium_updates: int = 0
    interrupted: bool = False


@dataclass
class SleepState:
    """Persisted sleep scheduler state."""

    last_cycle_time: str = ""  # ISO timestamp
    cycles_completed: int = 0
    items_queued: int = 0


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class SleepScheduler:
    """Orchestrates periodic sleep-cycle walks over the memory graph."""

    def __init__(
        self,
        cycle_minutes: float = 5.0,
        max_nodes_per_cycle: int = 100,
        max_time_ms: int = 5000,
        state_path: Path | None = None,
    ) -> None:
        self.cycle_minutes = cycle_minutes
        self.max_nodes_per_cycle = max_nodes_per_cycle
        self.max_time_ms = max_time_ms
        self._state_path = state_path
        self._state: SleepState = self._load_state()

    # -- public API ---------------------------------------------------------

    def should_run(self, mode: AgentMode) -> bool:
        """Return True when a sleep walk is due.

        Walks run continuously throughout sleep with a short cooldown
        between cycles (10 seconds) to avoid busy-looping.
        """
        if mode != AgentMode.ASLEEP:
            return False

        if not self._state.last_cycle_time:
            return True  # first run

        last = datetime.fromisoformat(self._state.last_cycle_time)
        elapsed = datetime.now(timezone.utc) - last
        # Short cooldown between dream walks (10s) rather than full cycle gap
        return elapsed >= timedelta(seconds=10)

    async def run_cycle(
        self,
        walker: GraphWalker,
        consolidation_engine: Any | None = None,
        interrupt_check: Callable[[], bool] | None = None,
        post_walk_callback: Callable[[WalkResult], Awaitable[int]] | None = None,
    ) -> CycleResult:
        """Execute one sleep cycle and return its result."""
        start = time.monotonic()

        # Early interrupt check
        if interrupt_check and interrupt_check():
            return CycleResult(
                walks_completed=0,
                items_queued=0,
                duration_ms=_elapsed_ms(start),
                interrupted=True,
            )

        num_seeds = 5
        seeds = await walker.sample_seeds(num_seeds)

        steps_per_walk = max(1, self.max_nodes_per_cycle // num_seeds)
        time_per_walk = max(1, self.max_time_ms // num_seeds)

        walk_results: list[WalkResult] = []
        mycelium_updates = 0
        for seed in seeds:
            if interrupt_check and interrupt_check():
                return CycleResult(
                    walks_completed=len(walk_results),
                    items_queued=0,
                    duration_ms=_elapsed_ms(start),
                    applied_actions=0,
                    mycelium_updates=mycelium_updates,
                    interrupted=True,
                )

            result = await walker.bounded_walk(
                seed_id=seed,
                max_steps=steps_per_walk,
                max_time_ms=time_per_walk,
                interrupt_check=interrupt_check,
            )
            walk_results.append(result)
            if post_walk_callback is not None:
                mycelium_updates += await post_walk_callback(result)

        # Consolidation (pass-through for now)
        proposed_actions: list[dict[str, Any]] = []
        if consolidation_engine is not None:
            proposed_actions = consolidation_engine.process(walk_results)
        else:
            for wr in walk_results:
                proposed_actions.extend(wr.proposed_actions)

        applied_actions = 0
        if hasattr(walker, "apply_actions"):
            applied_actions = await walker.apply_actions(proposed_actions)
        total_changes = applied_actions + mycelium_updates

        # Update state
        self._state.last_cycle_time = datetime.now(timezone.utc).isoformat()
        self._state.cycles_completed += 1
        self._state.items_queued += total_changes
        self._save_state(self._state)

        return CycleResult(
            walks_completed=len(walk_results),
            items_queued=total_changes,
            duration_ms=_elapsed_ms(start),
            applied_actions=applied_actions,
            mycelium_updates=mycelium_updates,
        )

    def get_state(self) -> SleepState:
        """Return the current scheduler state (for inspection)."""
        return self._state

    # -- persistence --------------------------------------------------------

    def _resolve_state_file(self) -> Path | None:
        if self._state_path is None:
            return None
        path = self._state_path
        if path.suffix == ".json":
            return path
        return path / "sleep_state.json"

    def _load_state(self) -> SleepState:
        state_file = self._resolve_state_file()
        if state_file is None or not state_file.exists():
            return SleepState()
        try:
            data = json.loads(state_file.read_text())
            return SleepState(**data)
        except (json.JSONDecodeError, TypeError):
            return SleepState()

    def _save_state(self, state: SleepState) -> None:
        state_file = self._resolve_state_file()
        if state_file is None:
            return
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(asdict(state), indent=2))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000.0
