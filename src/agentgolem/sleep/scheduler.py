"""Sleep-cycle scheduler for background memory consolidation."""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    phase: str = "consolidation"


@dataclass
class SleepState:
    """Persisted sleep scheduler state."""

    last_cycle_time: str = ""  # ISO timestamp
    cycles_completed: int = 0
    items_queued: int = 0
    current_phase: str = "consolidation"
    phase_step: int = 0
    neural_state: dict[str, Any] = field(default_factory=dict)
    last_cycle_activity: dict[str, Any] = field(default_factory=dict)


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
        phase_cycle_length: int = 6,
        phase_split: float = 0.67,
        persist_top_k: int = 128,
    ) -> None:
        self.cycle_minutes = cycle_minutes
        self.max_nodes_per_cycle = max_nodes_per_cycle
        self.max_time_ms = max_time_ms
        self.phase_cycle_length = max(2, int(phase_cycle_length))
        self.phase_split = min(max(float(phase_split), 0.1), 0.9)
        self.persist_top_k = max(8, int(persist_top_k))
        self._state_path = state_path
        self._state: SleepState = self._load_state()

    # -- public API ---------------------------------------------------------

    def should_run(self, mode: AgentMode) -> bool:
        """Return True when a sleep walk is due."""
        if mode != AgentMode.ASLEEP:
            return False

        if not self._state.last_cycle_time:
            return True

        last = datetime.fromisoformat(self._state.last_cycle_time)
        elapsed = datetime.now(timezone.utc) - last
        cooldown_seconds = max(1.0, min(self.cycle_minutes * 60.0, 10.0))
        return elapsed >= timedelta(seconds=cooldown_seconds)

    async def run_cycle(
        self,
        walker: GraphWalker,
        consolidation_engine: Any | None = None,
        interrupt_check: Callable[[], bool] | None = None,
        post_walk_callback: Callable[[WalkResult], Awaitable[int]] | None = None,
    ) -> CycleResult:
        """Execute one sleep cycle and return its result."""
        start = time.monotonic()
        phase = self._state.current_phase or "consolidation"

        if hasattr(walker, "restore_neural_state"):
            walker.restore_neural_state(self._state.neural_state)

        if interrupt_check and interrupt_check():
            return CycleResult(
                walks_completed=0,
                items_queued=0,
                duration_ms=_elapsed_ms(start),
                interrupted=True,
                phase=phase,
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
                    phase=phase,
                )

            result = await walker.bounded_walk(
                seed_id=seed,
                max_steps=steps_per_walk,
                max_time_ms=time_per_walk,
                interrupt_check=interrupt_check,
                phase=phase,
            )
            walk_results.append(result)
            if post_walk_callback is not None:
                mycelium_updates += await post_walk_callback(result)

        proposed_actions: list[dict[str, Any]] = []
        if consolidation_engine is not None:
            proposed_actions = consolidation_engine.process(walk_results)
        else:
            for walk_result in walk_results:
                proposed_actions.extend(walk_result.proposed_actions)

        applied_actions = 0
        if hasattr(walker, "apply_actions"):
            applied_actions = await walker.apply_actions(proposed_actions)
        total_changes = applied_actions + mycelium_updates

        if hasattr(walker, "export_neural_state"):
            self._state.neural_state = walker.export_neural_state(top_k=self.persist_top_k)

        cycle_timestamp = datetime.now(timezone.utc).isoformat()
        self._state.last_cycle_time = cycle_timestamp
        self._state.last_cycle_activity = _summarize_cycle_activity(
            walk_results,
            phase=phase,
            timestamp=cycle_timestamp,
        )
        self._state.cycles_completed += 1
        self._state.items_queued += total_changes
        self._advance_phase()
        self._save_state(self._state)

        return CycleResult(
            walks_completed=len(walk_results),
            items_queued=total_changes,
            duration_ms=_elapsed_ms(start),
            applied_actions=applied_actions,
            mycelium_updates=mycelium_updates,
            interrupted=False,
            phase=phase,
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
            return SleepState(
                last_cycle_time=str(data.get("last_cycle_time", "")),
                cycles_completed=int(data.get("cycles_completed", 0)),
                items_queued=int(data.get("items_queued", 0)),
                current_phase=str(data.get("current_phase", "consolidation")),
                phase_step=int(data.get("phase_step", 0)),
                neural_state=dict(data.get("neural_state", {})),
                last_cycle_activity=dict(data.get("last_cycle_activity", {})),
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            return SleepState()

    def _save_state(self, state: SleepState) -> None:
        state_file = self._resolve_state_file()
        if state_file is None:
            return
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(asdict(state), indent=2))

    def _advance_phase(self) -> None:
        consolidation_cycles = max(
            1,
            min(
                self.phase_cycle_length - 1,
                round(self.phase_cycle_length * self.phase_split),
            ),
        )
        dream_cycles = max(1, self.phase_cycle_length - consolidation_cycles)

        self._state.phase_step += 1
        if self._state.current_phase == "dream":
            if self._state.phase_step >= dream_cycles:
                self._state.current_phase = "consolidation"
                self._state.phase_step = 0
            return

        if self._state.phase_step >= consolidation_cycles:
            self._state.current_phase = "dream"
            self._state.phase_step = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000.0


def _summarize_cycle_activity(
    walk_results: list[WalkResult],
    *,
    phase: str,
    timestamp: str,
) -> dict[str, Any]:
    """Return a compact activation summary for the latest completed sleep cycle."""
    activated_node_ids: list[str] = []
    seen_node_ids: set[str] = set()
    edge_strengths: dict[str, float] = {}

    for walk_result in walk_results:
        for node_id in walk_result.visited_node_ids:
            if not node_id or node_id in seen_node_ids:
                continue
            seen_node_ids.add(node_id)
            activated_node_ids.append(node_id)
        for edge_id, activation in walk_result.edge_activations.items():
            if not edge_id:
                continue
            edge_strengths[edge_id] = max(edge_strengths.get(edge_id, 0.0), float(activation))

    activated_edge_ids = [
        edge_id
        for edge_id, _activation in sorted(
            edge_strengths.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:64]
    ]
    return {
        "timestamp": timestamp,
        "phase": phase,
        "walks_completed": len(walk_results),
        "node_ids": activated_node_ids[:64],
        "edge_ids": activated_edge_ids,
    }
