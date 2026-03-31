"""Inter-agent communication bus for the AgentGolem Ethical Council."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Default discussion priority — lower number speaks first.
# Council-6 (holistic integration) initiates; Council-7 (devil's advocate) goes last.
DISCUSSION_PRIORITY_DEFAULT = 50
DISCUSSION_PRIORITY_INITIATOR = 0  # Agent 6
DISCUSSION_PRIORITY_LAST = 99  # Agent 7


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

    The discussion floor uses priority-aware ordering: when multiple
    agents want to speak, lower-priority-number agents go first.
    Council-6 (holistic) initiates, Council-7 (devil's advocate) goes last.
    """

    def __init__(
        self, max_transcript: int = 30, default_max_chars: int = 0,
    ) -> None:
        self._queues: dict[str, asyncio.Queue[AgentMessage]] = {}
        self._name_map: dict[str, str] = {}  # lowered alias → canonical name
        self._default_max_chars = default_max_chars  # 0 = no limit

        # ── Discussion floor (turn-taking) ─────────────────────────────
        self._floor = asyncio.Lock()
        self._floor_holder: str | None = None
        self._transcript: list[AgentMessage] = []
        self._max_transcript = max_transcript

        # Priority-aware waiting: agents register their priority and
        # wait on per-agent Events.  When the floor is released we
        # wake the highest-priority (lowest number) waiter first.
        self._agent_priority: dict[str, int] = {}
        self._floor_waiters: dict[str, asyncio.Event] = {}
        self._floor_wait_queue: list[str] = []  # agents waiting for floor

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    def register(self, name: str, *, discussion_priority: int | None = None) -> None:
        """Register an agent on the bus.

        *discussion_priority* controls speaking order (lower = earlier).
        """
        self._queues[name] = asyncio.Queue()
        self._name_map[name.lower()] = name
        if discussion_priority is not None:
            self._agent_priority[name] = discussion_priority
        else:
            self._agent_priority[name] = DISCUSSION_PRIORITY_DEFAULT

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
            # Carry over discussion priority
            if old_name in self._agent_priority:
                self._agent_priority[new_name] = self._agent_priority.pop(old_name)

    def resolve_name(self, name: str) -> str | None:
        """Resolve a name/alias to a registered agent name."""
        return self._name_map.get(name.lower())

    def get_peers(self, exclude: str) -> list[str]:
        """Get list of all registered agents except *exclude*."""
        return [n for n in self._queues if n != exclude]

    def get_all_names(self) -> list[str]:
        """Get list of all registered agent names."""
        return list(self._queues.keys())

    def get_priority(self, agent_name: str) -> int:
        """Return the discussion priority for *agent_name*."""
        return self._agent_priority.get(agent_name, DISCUSSION_PRIORITY_DEFAULT)

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send(
        self, from_agent: str, to_agent: str, text: str,
        *, max_chars: int = 0,
    ) -> bool:
        """Send a direct message to a specific agent.

        If *max_chars* > 0 the message text is hard-truncated before delivery.
        Falls back to ``default_max_chars`` when *max_chars* is 0.
        """
        limit = max_chars or self._default_max_chars
        if limit > 0 and len(text) > limit:
            text = text[:limit] + "…"
        resolved = self.resolve_name(to_agent)
        if resolved and resolved in self._queues:
            msg = AgentMessage(
                from_agent=from_agent, text=text, to_agent=resolved
            )
            await self._queues[resolved].put(msg)
            self._record_utterance(msg)
            return True
        return False

    async def broadcast(
        self, from_agent: str, text: str, *, max_chars: int = 0,
    ) -> int:
        """Broadcast a message to every agent except the sender.

        If *max_chars* > 0 the message text is hard-truncated before delivery.
        Falls back to ``default_max_chars`` when *max_chars* is 0.
        """
        limit = max_chars or self._default_max_chars
        if limit > 0 and len(text) > limit:
            text = text[:limit] + "…"
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
        """Acquire exclusive discussion floor with priority ordering.

        When the floor is free the highest-priority (lowest number) waiting
        agent gets it.  If no one else is waiting, the caller gets it
        immediately.
        """
        # Fast path: floor is free, no one else waiting
        if not self._floor.locked() and not self._floor_wait_queue:
            await self._floor.acquire()
            self._floor_holder = agent_name
            return

        # Add ourselves to the priority wait queue
        evt = asyncio.Event()
        self._floor_waiters[agent_name] = evt
        self._floor_wait_queue.append(agent_name)
        try:
            await evt.wait()
        finally:
            self._floor_waiters.pop(agent_name, None)
            if agent_name in self._floor_wait_queue:
                self._floor_wait_queue.remove(agent_name)
        # When woken, we already hold the floor (set by release_floor)

    def release_floor(self) -> None:
        """Release the discussion floor, waking the next highest-priority waiter."""
        self._floor_holder = None

        if self._floor_wait_queue:
            # Sort waiting agents by priority (lowest number first)
            self._floor_wait_queue.sort(
                key=lambda n: self._agent_priority.get(n, DISCUSSION_PRIORITY_DEFAULT)
            )
            next_agent = self._floor_wait_queue.pop(0)
            self._floor_holder = next_agent
            evt = self._floor_waiters.get(next_agent)
            if evt:
                evt.set()
        elif self._floor.locked():
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
