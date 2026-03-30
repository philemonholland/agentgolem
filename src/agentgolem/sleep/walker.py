"""Spiking-inspired graph walker for the sleep/default-mode subsystem."""
from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agentgolem.memory.store import SQLiteMemoryStore
    from agentgolem.runtime.state import RuntimeState


MIN_POTENTIAL = 0.05
MAX_STATE_NODES = 512
MAX_RECENT_SPIKES = 48
VALID_SLEEP_PHASES = {"consolidation", "dream"}


@dataclass(frozen=True)
class SleepSpikingConfig:
    """Tunable parameters for the spiking-inspired sleep heuristic."""

    membrane_decay: float = 0.82
    consolidation_threshold: float = 0.95
    dream_threshold: float = 0.75
    refractory_steps: int = 2
    stdp_window_steps: int = 3
    stdp_strength: float = 0.08
    dream_noise: float = 0.18


@dataclass(frozen=True)
class SpikeEvent:
    """One spike emitted by a memory node during a sleep walk."""

    node_id: str
    step: int
    potential: float
    phase: str


@dataclass
class SleepNeuralState:
    """Transient neural state carried across sleep cycles."""

    timestep: int = 0
    membrane_potentials: dict[str, float] = field(default_factory=dict)
    refractory_counters: dict[str, int] = field(default_factory=dict)
    pending_inputs: dict[str, float] = field(default_factory=dict)
    recent_spikes: list[SpikeEvent] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SleepNeuralState:
        """Hydrate persisted sleep state."""
        if not data:
            return cls()
        recent_raw = data.get("recent_spikes", [])
        recent_spikes = [
            SpikeEvent(
                node_id=str(item.get("node_id", "")),
                step=int(item.get("step", 0)),
                potential=float(item.get("potential", 0.0)),
                phase=str(item.get("phase", "consolidation")),
            )
            for item in recent_raw
            if item.get("node_id")
        ]
        return cls(
            timestep=int(data.get("timestep", 0)),
            membrane_potentials={
                str(node_id): float(value)
                for node_id, value in dict(data.get("membrane_potentials", {})).items()
            },
            refractory_counters={
                str(node_id): int(value)
                for node_id, value in dict(data.get("refractory_counters", {})).items()
                if int(value) > 0
            },
            pending_inputs={
                str(node_id): float(value)
                for node_id, value in dict(data.get("pending_inputs", {})).items()
            },
            recent_spikes=recent_spikes[-MAX_RECENT_SPIKES:],
        )

    def to_dict(self, *, top_k: int = 128) -> dict[str, Any]:
        """Serialize a bounded snapshot suitable for persistence."""
        bounded_top_k = max(1, min(top_k, MAX_STATE_NODES))
        membrane = _top_entries(self.membrane_potentials, bounded_top_k, MIN_POTENTIAL)
        pending = _top_entries(self.pending_inputs, bounded_top_k, MIN_POTENTIAL / 2.0)
        relevant_ids = set(membrane) | set(pending)
        refractory = {
            node_id: int(value)
            for node_id, value in self.refractory_counters.items()
            if value > 0 and (not relevant_ids or node_id in relevant_ids)
        }
        return {
            "timestep": int(self.timestep),
            "membrane_potentials": membrane,
            "refractory_counters": refractory,
            "pending_inputs": pending,
            "recent_spikes": [asdict(event) for event in self.recent_spikes[-MAX_RECENT_SPIKES:]],
        }


@dataclass
class WalkResult:
    """Result of a bounded spiking sleep walk."""

    seed_id: str
    visited_node_ids: list[str]
    edge_activations: dict[str, float]  # edge_id -> activation strength
    proposed_actions: list[dict[str, Any]]
    steps_taken: int
    time_ms: float
    interrupted: bool = False
    phase: str = "consolidation"
    spike_events: list[SpikeEvent] = field(default_factory=list)
    peak_potentials: dict[str, float] = field(default_factory=dict)


class GraphWalker:
    """Timestep-based spiking heuristic over the memory graph."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        runtime_state: RuntimeState,
        config: SleepSpikingConfig | None = None,
    ) -> None:
        self._store = store
        self._runtime_state = runtime_state
        self._config = config or SleepSpikingConfig()
        self._neural_state = SleepNeuralState()

    def update_config(self, config: SleepSpikingConfig) -> None:
        """Update spiking parameters at runtime."""
        self._config = config

    def restore_neural_state(
        self,
        state: SleepNeuralState | dict[str, Any] | None,
    ) -> None:
        """Load persisted transient neural state into the walker."""
        if state is None:
            self._neural_state = SleepNeuralState()
        elif isinstance(state, SleepNeuralState):
            self._neural_state = state
        else:
            self._neural_state = SleepNeuralState.from_dict(state)
        self._prune_state()

    def export_neural_state(self, *, top_k: int = 128) -> dict[str, Any]:
        """Return a bounded snapshot of transient neural state."""
        self._prune_state()
        return self._neural_state.to_dict(top_k=top_k)

    # ------------------------------------------------------------------
    # Seed sampling
    # ------------------------------------------------------------------

    async def sample_seeds(self, n: int) -> list[str]:
        """Return up to *n* node IDs weighted by centrality × recency × emotion × salience."""
        now = datetime.now(timezone.utc)

        async with self._store._db.execute(
            "SELECT id, centrality, last_accessed, emotion_score, salience "
            "FROM nodes WHERE status = 'active'"
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            return []

        ids: list[str] = []
        weights: list[float] = []
        for row in rows:
            node_id: str = row["id"]
            centrality: float = float(row["centrality"] or 0.0)
            emotion: float = float(row["emotion_score"] or 0.0)
            salience: float = float(row["salience"] or 0.5)
            last_accessed = datetime.fromisoformat(row["last_accessed"])
            days_since = (now - last_accessed).total_seconds() / 86_400
            recency_score = 1.0 / max(1.0, days_since)
            emotion_boost = 1.0 + 2.0 * min(abs(emotion), 1.0)
            salience_boost = 1.0 + min(max(salience, 0.0), 1.0)
            weight = centrality * recency_score * emotion_boost * salience_boost
            ids.append(node_id)
            weights.append(max(weight, 1e-9))

        k = min(n, len(ids))
        if k == len(ids):
            return ids

        selected: list[str] = []
        remaining_ids = list(ids)
        remaining_weights = list(weights)
        for _ in range(k):
            chosen = random.choices(remaining_ids, weights=remaining_weights, k=1)[0]
            idx = remaining_ids.index(chosen)
            selected.append(chosen)
            remaining_ids.pop(idx)
            remaining_weights.pop(idx)

        return selected

    # ------------------------------------------------------------------
    # Bounded walk
    # ------------------------------------------------------------------

    async def bounded_walk(
        self,
        seed_id: str,
        max_steps: int = 50,
        max_time_ms: int = 5000,
        interrupt_check: Callable[[], bool] | None = None,
        phase: str = "consolidation",
    ) -> WalkResult:
        """Perform a bounded spiking sleep walk from *seed_id*."""
        sleep_phase = phase if phase in VALID_SLEEP_PHASES else "consolidation"
        threshold = (
            self._config.consolidation_threshold
            if sleep_phase == "consolidation"
            else self._config.dream_threshold
        )
        decay = (
            self._config.membrane_decay
            if sleep_phase == "consolidation"
            else min(self._config.membrane_decay + 0.08, 0.98)
        )

        visited: list[str] = []
        edge_activations: dict[str, float] = {}
        edge_pairs: dict[str, tuple[str, str]] = {}
        peak_potentials: dict[str, float] = {}
        spike_events: list[SpikeEvent] = []
        steps = 0
        start_ns = time.perf_counter_ns()
        neighbor_cache: dict[str, list[tuple[Any, Any]]] = {}
        dream_injections = await self._dream_injection_pool(seed_id, sleep_phase, max_steps)

        while True:
            if steps >= max_steps:
                break

            elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
            if elapsed_ms >= max_time_ms:
                break

            if interrupt_check and steps > 0 and steps % 5 == 0 and interrupt_check():
                return WalkResult(
                    seed_id=seed_id,
                    visited_node_ids=visited,
                    edge_activations=edge_activations,
                    proposed_actions=self._propose_actions(
                        edge_activations=edge_activations,
                        edge_pairs=edge_pairs,
                        spike_events=spike_events,
                    ),
                    steps_taken=steps,
                    time_ms=elapsed_ms,
                    interrupted=True,
                    phase=sleep_phase,
                    spike_events=spike_events,
                    peak_potentials=_top_entries(peak_potentials, 12),
                )

            steps += 1
            self._advance_refractory()
            injections = self._step_injections(
                seed_id=seed_id,
                sleep_phase=sleep_phase,
                step_index=steps,
                threshold=threshold,
                dream_injections=dream_injections,
            )

            fired = await self._step_network(
                sleep_phase=sleep_phase,
                threshold=threshold,
                decay=decay,
                injections=injections,
                edge_activations=edge_activations,
                edge_pairs=edge_pairs,
                peak_potentials=peak_potentials,
                neighbor_cache=neighbor_cache,
            )
            self._neural_state.timestep += 1

            for node_id, potential in fired:
                if node_id not in visited:
                    visited.append(node_id)
                event = SpikeEvent(
                    node_id=node_id,
                    step=self._neural_state.timestep,
                    potential=potential,
                    phase=sleep_phase,
                )
                spike_events.append(event)
                self._neural_state.recent_spikes.append(event)

            self._trim_recent_spikes()
            self._prune_state()

            no_pending = not self._neural_state.pending_inputs
            no_live_potentials = not any(
                value >= MIN_POTENTIAL for value in self._neural_state.membrane_potentials.values()
            )
            if not fired and no_pending and no_live_potentials:
                break

        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
        return WalkResult(
            seed_id=seed_id,
            visited_node_ids=visited,
            edge_activations=edge_activations,
            proposed_actions=self._propose_actions(
                edge_activations=edge_activations,
                edge_pairs=edge_pairs,
                spike_events=spike_events,
            ),
            steps_taken=steps,
            time_ms=elapsed_ms,
            interrupted=False,
            phase=sleep_phase,
            spike_events=spike_events,
            peak_potentials=_top_entries(peak_potentials, 12),
        )

    # ------------------------------------------------------------------
    # Edge weight modification
    # ------------------------------------------------------------------

    async def reinforce_edge(self, edge_id: str, amount: float = 0.1) -> None:
        """Increase an edge's weight (clamped to [0, 5.0])."""
        async with self._store._db.execute(
            "SELECT weight FROM edges WHERE id = ?",
            (edge_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return
        new_weight = min(float(row["weight"]) + amount, 5.0)
        new_weight = max(new_weight, 0.0)
        await self._store.update_edge(edge_id, new_weight)

    async def weaken_edge(self, edge_id: str, amount: float = 0.1) -> None:
        """Decrease an edge's weight (clamped to [0.01, ∞))."""
        async with self._store._db.execute(
            "SELECT weight FROM edges WHERE id = ?",
            (edge_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return
        new_weight = max(float(row["weight"]) - amount, 0.01)
        await self._store.update_edge(edge_id, new_weight)

    async def apply_actions(self, actions: list[dict[str, Any]]) -> int:
        """Apply reinforce/weaken actions produced by the sleep walk."""
        applied = 0
        for action in actions:
            action_type = action.get("type") or action.get("kind")
            edge_id = action.get("edge_id")
            amount = float(action.get("amount", action.get("delta", 0.1)))
            if not edge_id:
                continue
            if action_type in {"reinforce", "reinforce_edge"}:
                await self.reinforce_edge(edge_id, amount=amount)
                applied += 1
            elif action_type in {"weaken", "weaken_edge"}:
                await self.weaken_edge(edge_id, amount=amount)
                applied += 1
        return applied

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _dream_injection_pool(
        self,
        seed_id: str,
        sleep_phase: str,
        max_steps: int,
    ) -> list[str]:
        if sleep_phase != "dream":
            return []
        seeds = await self.sample_seeds(min(6, max(2, max_steps)))
        return [node_id for node_id in seeds if node_id != seed_id]

    def _step_injections(
        self,
        *,
        seed_id: str,
        sleep_phase: str,
        step_index: int,
        threshold: float,
        dream_injections: list[str],
    ) -> dict[str, float]:
        injections: dict[str, float] = {}
        if step_index == 1:
            injections[seed_id] = max(threshold * 1.35, 1.0)
        elif not self._neural_state.pending_inputs:
            injections[seed_id] = threshold * 0.35

        if sleep_phase == "dream" and dream_injections:
            chosen = random.choice(dream_injections)
            injections[chosen] = injections.get(chosen, 0.0) + max(
                self._config.dream_noise * threshold,
                0.08,
            )
        return injections

    async def _step_network(
        self,
        *,
        sleep_phase: str,
        threshold: float,
        decay: float,
        injections: dict[str, float],
        edge_activations: dict[str, float],
        edge_pairs: dict[str, tuple[str, str]],
        peak_potentials: dict[str, float],
        neighbor_cache: dict[str, list[tuple[Any, Any]]],
    ) -> list[tuple[str, float]]:
        active_ids = (
            set(self._neural_state.membrane_potentials)
            | set(self._neural_state.pending_inputs)
            | set(injections)
        )
        if not active_ids:
            return []

        pending_inputs = dict(self._neural_state.pending_inputs)
        self._neural_state.pending_inputs = {}
        integrated: dict[str, float] = {}
        for node_id in active_ids:
            potential = self._neural_state.membrane_potentials.get(node_id, 0.0) * decay
            potential += pending_inputs.get(node_id, 0.0)
            potential += injections.get(node_id, 0.0)
            if sleep_phase == "dream":
                potential += random.uniform(0.0, self._config.dream_noise * 0.12)
            integrated[node_id] = potential
            peak_potentials[node_id] = max(peak_potentials.get(node_id, 0.0), potential)

        fired: list[tuple[str, float]] = []
        next_pending: dict[str, float] = {}
        for node_id, potential in sorted(
            integrated.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            if self._neural_state.refractory_counters.get(node_id, 0) > 0:
                self._neural_state.membrane_potentials[node_id] = min(
                    potential,
                    threshold * 0.5,
                )
                continue

            if potential < threshold:
                self._neural_state.membrane_potentials[node_id] = potential
                continue

            fired.append((node_id, potential))
            self._neural_state.refractory_counters[node_id] = max(
                1,
                self._config.refractory_steps,
            )
            self._neural_state.membrane_potentials[node_id] = threshold * 0.12

            for edge, neighbor in await self._get_cached_neighbors(node_id, neighbor_cache):
                propagated = self._propagation_strength(
                    edge_weight=edge.weight,
                    trust_useful=neighbor.trust_useful,
                    salience=neighbor.salience,
                    sleep_phase=sleep_phase,
                )
                next_pending[neighbor.id] = next_pending.get(neighbor.id, 0.0) + propagated
                edge_activations[edge.id] = edge_activations.get(edge.id, 0.0) + propagated
                edge_pairs[edge.id] = (edge.source_id, edge.target_id)

        self._neural_state.pending_inputs = next_pending
        return fired

    async def _get_cached_neighbors(
        self,
        node_id: str,
        cache: dict[str, list[tuple[Any, Any]]],
    ) -> list[tuple[Any, Any]]:
        if node_id not in cache:
            cache[node_id] = await self._store.get_neighbors(node_id)
        return cache[node_id]

    def _advance_refractory(self) -> None:
        next_refractory: dict[str, int] = {}
        for node_id, remaining in self._neural_state.refractory_counters.items():
            if remaining > 1:
                next_refractory[node_id] = remaining - 1
        self._neural_state.refractory_counters = next_refractory

    def _trim_recent_spikes(self) -> None:
        cutoff = self._neural_state.timestep - max(self._config.stdp_window_steps * 2, 6)
        self._neural_state.recent_spikes = [
            event
            for event in self._neural_state.recent_spikes[-MAX_RECENT_SPIKES:]
            if event.step >= cutoff
        ]

    def _prune_state(self) -> None:
        self._neural_state.membrane_potentials = _top_entries(
            self._neural_state.membrane_potentials,
            MAX_STATE_NODES,
            MIN_POTENTIAL,
        )
        self._neural_state.pending_inputs = _top_entries(
            self._neural_state.pending_inputs,
            MAX_STATE_NODES,
            MIN_POTENTIAL / 2.0,
        )
        self._neural_state.refractory_counters = {
            node_id: remaining
            for node_id, remaining in self._neural_state.refractory_counters.items()
            if remaining > 0
        }
        self._trim_recent_spikes()

    def _propose_actions(
        self,
        *,
        edge_activations: dict[str, float],
        edge_pairs: dict[str, tuple[str, str]],
        spike_events: list[SpikeEvent],
    ) -> list[dict[str, Any]]:
        """Generate STDP-like reinforce/weaken actions from spike timing."""
        if not edge_activations:
            return []

        max_activation = max(edge_activations.values(), default=1.0)
        actions: list[dict[str, Any]] = []
        for edge_id, activation in sorted(
            edge_activations.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            source_id, target_id = edge_pairs.get(edge_id, ("", ""))
            causal, anti = self._timing_alignment(source_id, target_id, spike_events)
            activation_bonus = 0.04 * (activation / max(max_activation, 1e-6))
            delta = activation_bonus + (
                self._config.stdp_strength * causal
            ) - (
                self._config.stdp_strength * 0.75 * anti
            )

            if delta >= 0.04:
                actions.append(
                    {
                        "type": "reinforce",
                        "edge_id": edge_id,
                        "amount": round(min(delta, 0.2), 4),
                    }
                )
            elif delta <= -0.02 or activation < 0.08:
                actions.append(
                    {
                        "type": "weaken",
                        "edge_id": edge_id,
                        "amount": round(min(abs(delta) + 0.02, 0.15), 4),
                    }
                )
        return actions

    def _timing_alignment(
        self,
        source_id: str,
        target_id: str,
        spike_events: list[SpikeEvent],
    ) -> tuple[float, float]:
        """Return causal and anti-causal timing scores for one directed edge."""
        if not source_id or not target_id:
            return 0.0, 0.0

        source_steps = [event.step for event in spike_events if event.node_id == source_id]
        target_steps = [event.step for event in spike_events if event.node_id == target_id]
        if not source_steps or not target_steps:
            return 0.0, 0.0

        window = max(1, self._config.stdp_window_steps)
        causal = 0.0
        anti = 0.0
        for source_step in source_steps:
            for target_step in target_steps:
                delta = target_step - source_step
                if 0 < delta <= window:
                    causal = max(causal, (window + 1 - delta) / window)
                elif 0 < -delta <= window:
                    anti = max(anti, (window + 1 - abs(delta)) / window)
        return causal, anti

    def _propagation_strength(
        self,
        *,
        edge_weight: float,
        trust_useful: float,
        salience: float,
        sleep_phase: str,
    ) -> float:
        base = max(edge_weight, 0.01) * (
            0.55 + min(max(trust_useful, 0.0), 1.0) + (0.25 * min(max(salience, 0.0), 1.0))
        )
        if sleep_phase == "dream":
            base += random.uniform(0.0, self._config.dream_noise * 0.35)
        return min(base, 1.5)


def _top_entries(
    values: dict[str, float],
    top_k: int,
    min_value: float = 0.0,
) -> dict[str, float]:
    filtered = [
        (node_id, float(value))
        for node_id, value in values.items()
        if float(value) >= min_value
    ]
    filtered.sort(key=lambda item: item[1], reverse=True)
    return {
        node_id: value
        for node_id, value in filtered[: max(1, top_k)]
    }
