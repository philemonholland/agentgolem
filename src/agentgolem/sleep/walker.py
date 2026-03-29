"""Bounded graph walker for the sleep/default-mode subsystem."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agentgolem.memory.store import SQLiteMemoryStore
    from agentgolem.runtime.state import RuntimeState


@dataclass
class WalkResult:
    """Result of a bounded graph walk."""

    seed_id: str
    visited_node_ids: list[str]
    edge_activations: dict[str, float]  # edge_id -> activation strength
    proposed_actions: list[dict[str, Any]]
    steps_taken: int
    time_ms: float
    interrupted: bool = False


class GraphWalker:
    """Spreading-activation walker over the memory graph."""

    def __init__(self, store: SQLiteMemoryStore, runtime_state: RuntimeState) -> None:
        self._store = store
        self._runtime_state = runtime_state

    # ------------------------------------------------------------------
    # Seed sampling
    # ------------------------------------------------------------------

    async def sample_seeds(self, n: int) -> list[str]:
        """Return up to *n* node IDs weighted by centrality × recency."""
        now = datetime.now(timezone.utc)

        async with self._store._db.execute(
            "SELECT id, centrality, last_accessed FROM nodes WHERE status = 'active'"
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            return []

        ids: list[str] = []
        weights: list[float] = []
        for row in rows:
            node_id: str = row["id"]
            centrality: float = row["centrality"]
            last_accessed = datetime.fromisoformat(row["last_accessed"])
            days_since = (now - last_accessed).total_seconds() / 86_400
            recency_score = 1.0 / max(1.0, days_since)
            weight = centrality * recency_score
            ids.append(node_id)
            weights.append(max(weight, 1e-9))  # avoid zero weights

        k = min(n, len(ids))
        if k == len(ids):
            return ids

        # Weighted sampling without replacement
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
    ) -> WalkResult:
        """Perform a spreading-activation walk from *seed_id*."""
        activation_map: dict[str, float] = {seed_id: 1.0}
        visited: list[str] = []
        edge_activations: dict[str, float] = {}
        steps = 0
        start_ns = time.perf_counter_ns()

        while True:
            # Budget: steps
            if steps >= max_steps:
                break

            # Budget: time
            elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
            if elapsed_ms >= max_time_ms:
                break

            # Budget: interrupt every 10 steps
            if interrupt_check and steps > 0 and steps % 10 == 0:
                if interrupt_check():
                    elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
                    return WalkResult(
                        seed_id=seed_id,
                        visited_node_ids=visited,
                        edge_activations=edge_activations,
                        proposed_actions=self._propose_actions(edge_activations),
                        steps_taken=steps,
                        time_ms=elapsed_ms,
                        interrupted=True,
                    )

            # Pick highest-activation unvisited node
            best_id: str | None = None
            best_act = -1.0
            for nid, act in activation_map.items():
                if nid not in visited and act > best_act:
                    best_id = nid
                    best_act = act

            if best_id is None:
                break

            visited.append(best_id)
            steps += 1

            # Propagate activation to neighbors
            neighbors = await self._store.get_neighbors(best_id)
            for edge, neighbor in neighbors:
                propagated = best_act * edge.weight * neighbor.trust_useful
                edge_activations[edge.id] = (
                    edge_activations.get(edge.id, 0.0) + propagated
                )
                current = activation_map.get(neighbor.id, 0.0)
                activation_map[neighbor.id] = max(current, propagated)

        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
        return WalkResult(
            seed_id=seed_id,
            visited_node_ids=visited,
            edge_activations=edge_activations,
            proposed_actions=self._propose_actions(edge_activations),
            steps_taken=steps,
            time_ms=elapsed_ms,
        )

    # ------------------------------------------------------------------
    # Edge weight modification
    # ------------------------------------------------------------------

    async def reinforce_edge(self, edge_id: str, amount: float = 0.1) -> None:
        """Increase an edge's weight (clamped to [0, 5.0])."""
        async with self._store._db.execute(
            "SELECT weight FROM edges WHERE id = ?", (edge_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return
        new_weight = min(row["weight"] + amount, 5.0)
        new_weight = max(new_weight, 0.0)
        await self._store.update_edge(edge_id, new_weight)

    async def weaken_edge(self, edge_id: str, amount: float = 0.1) -> None:
        """Decrease an edge's weight (clamped to [0.01, ∞))."""
        async with self._store._db.execute(
            "SELECT weight FROM edges WHERE id = ?", (edge_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return
        new_weight = max(row["weight"] - amount, 0.01)
        await self._store.update_edge(edge_id, new_weight)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _propose_actions(edge_activations: dict[str, float]) -> list[dict[str, Any]]:
        """Generate proposed actions from edge activation strengths."""
        actions: list[dict[str, Any]] = []
        for edge_id, activation in edge_activations.items():
            if activation > 0.5:
                actions.append({
                    "type": "reinforce",
                    "edge_id": edge_id,
                    "amount": 0.1,
                })
            elif activation < 0.1:
                actions.append({
                    "type": "weaken",
                    "edge_id": edge_id,
                    "amount": 0.1,
                })
        return actions
