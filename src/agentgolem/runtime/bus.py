"""Inter-agent communication bus for the AgentGolem Ethical Council."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AgentMessage:
    """A message between agents on the bus."""

    from_agent: str
    text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    to_agent: str | None = None  # None = broadcast


class InterAgentBus:
    """Shared message bus enabling agent-to-agent communication.

    Each registered agent gets a dedicated asyncio.Queue.
    Supports direct messages, broadcasts, and agent renaming
    (for when agents discover their names).
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[AgentMessage]] = {}
        self._name_map: dict[str, str] = {}  # lowered alias → canonical name

    def register(self, name: str) -> None:
        """Register an agent on the bus."""
        self._queues[name] = asyncio.Queue()
        self._name_map[name.lower()] = name

    def rename(self, old_name: str, new_name: str) -> None:
        """Update an agent's name (e.g., after name discovery)."""
        if old_name in self._queues:
            queue = self._queues.pop(old_name)
            self._queues[new_name] = queue
            self._name_map = {
                k: (new_name if v == old_name else v)
                for k, v in self._name_map.items()
            }
            self._name_map[new_name.lower()] = new_name

    def resolve_name(self, name: str) -> str | None:
        """Resolve a name/alias to a registered agent name."""
        return self._name_map.get(name.lower())

    def get_peers(self, exclude: str) -> list[str]:
        """Get list of all registered agents except *exclude*."""
        return [n for n in self._queues if n != exclude]

    def get_all_names(self) -> list[str]:
        """Get list of all registered agent names."""
        return list(self._queues.keys())

    async def send(self, from_agent: str, to_agent: str, text: str) -> bool:
        """Send a direct message to a specific agent."""
        resolved = self.resolve_name(to_agent)
        if resolved and resolved in self._queues:
            msg = AgentMessage(
                from_agent=from_agent, text=text, to_agent=resolved
            )
            await self._queues[resolved].put(msg)
            return True
        return False

    async def broadcast(self, from_agent: str, text: str) -> int:
        """Broadcast a message to every agent except the sender."""
        count = 0
        for name, queue in self._queues.items():
            if name != from_agent:
                msg = AgentMessage(from_agent=from_agent, text=text)
                await queue.put(msg)
                count += 1
        return count

    async def receive(
        self, agent_name: str, timeout: float = 0.0
    ) -> AgentMessage | None:
        """Receive one message for *agent_name*. Non-blocking by default."""
        queue = self._queues.get(agent_name)
        if queue is None:
            return None
        try:
            if timeout > 0:
                return await asyncio.wait_for(queue.get(), timeout=timeout)
            return queue.get_nowait()
        except (asyncio.TimeoutError, asyncio.QueueEmpty):
            return None

    def pending_count(self, agent_name: str) -> int:
        """Number of unread messages for an agent."""
        queue = self._queues.get(agent_name)
        return queue.qsize() if queue else 0
