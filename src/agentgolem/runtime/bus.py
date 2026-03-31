"""Inter-agent communication bus for the AgentGolem Ethical Council."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
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
    Supports direct messages, broadcasts, agent renaming
    (for when agents discover their names), and a turn-taking
    discussion floor that serialises peer conversations.
    """

    def __init__(self, max_transcript: int = 30) -> None:
        self._queues: dict[str, asyncio.Queue[AgentMessage]] = {}
        self._name_map: dict[str, str] = {}  # lowered alias → canonical name

        # ── Discussion floor (turn-taking) ─────────────────────────────
        self._floor = asyncio.Lock()
        self._floor_holder: str | None = None
        self._transcript: list[AgentMessage] = []
        self._max_transcript = max_transcript

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send(self, from_agent: str, to_agent: str, text: str) -> bool:
        """Send a direct message to a specific agent."""
        resolved = self.resolve_name(to_agent)
        if resolved and resolved in self._queues:
            msg = AgentMessage(
                from_agent=from_agent, text=text, to_agent=resolved
            )
            await self._queues[resolved].put(msg)
            self._record_utterance(msg)
            return True
        return False

    async def broadcast(self, from_agent: str, text: str) -> int:
        """Broadcast a message to every agent except the sender."""
        count = 0
        msg_for_transcript = AgentMessage(from_agent=from_agent, text=text)
        for name, queue in self._queues.items():
            if name != from_agent:
                msg = AgentMessage(from_agent=from_agent, text=text)
                await queue.put(msg)
                count += 1
        self._record_utterance(msg_for_transcript)
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

    # ------------------------------------------------------------------
    # Discussion floor (turn-taking)
    # ------------------------------------------------------------------

    async def acquire_floor(self, agent_name: str) -> None:
        """Acquire exclusive discussion floor. Blocks (FIFO) until available."""
        await self._floor.acquire()
        self._floor_holder = agent_name

    def release_floor(self) -> None:
        """Release the discussion floor."""
        self._floor_holder = None
        if self._floor.locked():
            self._floor.release()

    def floor_locked(self) -> bool:
        """Return True if the discussion floor is currently held."""
        return self._floor.locked()

    @property
    def floor_holder(self) -> str | None:
        """Name of the agent currently holding the floor, or None."""
        return self._floor_holder

    @asynccontextmanager
    async def hold_floor(self, agent_name: str) -> AsyncIterator[list[AgentMessage]]:
        """Context manager: acquire floor, yield recent transcript, release."""
        await self.acquire_floor(agent_name)
        try:
            yield self.get_transcript()
        finally:
            self.release_floor()

    # ------------------------------------------------------------------
    # Transcript
    # ------------------------------------------------------------------

    def _record_utterance(self, msg: AgentMessage) -> None:
        """Record a message in the discussion transcript."""
        self._transcript.append(msg)
        if len(self._transcript) > self._max_transcript * 2:
            self._transcript = self._transcript[-self._max_transcript:]

    def get_transcript(self, limit: int = 10) -> list[AgentMessage]:
        """Return the *limit* most recent transcript messages."""
        return list(self._transcript[-limit:])

    def format_transcript(
        self, limit: int = 10, exclude: str = "", max_chars: int = 400
    ) -> str:
        """Format recent transcript as readable context for prompts.

        Each message is truncated to *max_chars* to keep prompt size bounded.
        Messages from *exclude* are skipped.
        """
        recent = self.get_transcript(limit)
        if not recent:
            return ""
        lines: list[str] = []
        for m in recent:
            if m.from_agent == exclude:
                continue
            text = m.text[:max_chars] + "…" if len(m.text) > max_chars else m.text
            lines.append(f"[{m.from_agent}]: {text}")
        return "\n".join(lines)
