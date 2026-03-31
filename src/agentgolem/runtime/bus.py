"""Inter-agent communication bus for the AgentGolem Ethical Council."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    from_agent_id: str | None = None
    to_agent_id: str | None = None
    from_agent_aliases: list[str] = field(default_factory=list)


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
        self._name_map: dict[str, str] = {}  # lowered alias → stable agent id
        self._current_names: dict[str, str] = {}  # stable agent id → current display name
        self._aliases: dict[str, list[str]] = {}  # stable agent id → alias history
        self._default_max_chars = default_max_chars  # 0 = no limit

        # ── Discussion floor (turn-taking) ─────────────────────────────
        self._floor = asyncio.Lock()
        self._floor_holder: str | None = None  # stable agent id
        self._transcript: list[AgentMessage] = []
        self._max_transcript = max_transcript

        # Priority-aware waiting: agents register their priority and
        # wait on per-agent Events.  When the floor is released we
        # wake the highest-priority (lowest number) waiter first.
        self._agent_priority: dict[str, int] = {}
        self._agent_last_spoke_order: dict[str, int | None] = {}
        self._speak_order_counter = 0
        self._floor_waiters: dict[str, asyncio.Event] = {}
        self._floor_wait_queue: list[str] = []  # stable ids waiting for floor

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    def register(self, name: str, *, discussion_priority: int | None = None) -> None:
        """Register an agent on the bus.

        *discussion_priority* controls speaking order (lower = earlier).
        """
        if self._resolve_agent_id(name) is not None:
            raise ValueError(f"Agent name '{name}' is already registered.")
        self._queues[name] = asyncio.Queue()
        self._current_names[name] = name
        self._aliases[name] = [name]
        self._name_map[name.lower()] = name
        if discussion_priority is not None:
            self._agent_priority[name] = discussion_priority
        else:
            self._agent_priority[name] = DISCUSSION_PRIORITY_DEFAULT
        self._agent_last_spoke_order[name] = None

    def rename(self, old_name: str, new_name: str) -> None:
        """Update an agent's visible name while preserving its stable identity."""
        agent_id = self._resolve_agent_id(old_name)
        if agent_id is None:
            return
        if not self.is_name_available(new_name, requester=agent_id):
            raise ValueError(f"Agent name '{new_name}' is already reserved.")
        self._current_names[agent_id] = new_name
        self.register_alias(agent_id, new_name)

    def resolve_name(self, name: str) -> str | None:
        """Resolve a name/alias to the current visible agent name."""
        agent_id = self._resolve_agent_id(name)
        if agent_id is None:
            return None
        return self._display_name(agent_id)

    def get_registered_id(self, name: str) -> str | None:
        """Resolve a name/alias to the stable registered agent id."""
        return self._resolve_agent_id(name)

    def current_name(self, name: str) -> str | None:
        """Return the current visible name for *name* or alias."""
        agent_id = self._resolve_agent_id(name)
        if agent_id is None:
            return None
        return self._display_name(agent_id)

    def register_alias(self, owner: str, alias: str) -> None:
        """Reserve *alias* for the same stable owner forever."""
        owner_id = self._resolve_agent_id(owner)
        if owner_id is None:
            raise ValueError(f"Agent '{owner}' is not registered.")
        existing = self._resolve_agent_id(alias)
        if existing is not None and existing != owner_id:
            raise ValueError(f"Alias '{alias}' is already reserved.")
        self._name_map[alias.lower()] = owner_id
        aliases = self._aliases.setdefault(owner_id, [self._display_name(owner_id)])
        if alias.lower() not in {item.lower() for item in aliases}:
            aliases.append(alias)

    def get_aliases(self, name: str) -> list[str]:
        """Return every reserved alias for the owner of *name*."""
        agent_id = self._resolve_agent_id(name)
        if agent_id is None:
            return []
        return list(self._aliases.get(agent_id, []))

    def get_reserved_names(self, requester: str | None = None) -> list[str]:
        """Return reserved aliases for everyone except the optional requester."""
        requester_id = self._resolve_agent_id(requester) if requester else None
        reserved: list[str] = []
        for agent_id, aliases in self._aliases.items():
            if requester_id is not None and agent_id == requester_id:
                continue
            reserved.extend(aliases)
        return reserved

    def is_name_available(self, name: str, *, requester: str | None = None) -> bool:
        """Return True when *name* is not reserved by another agent."""
        owner_id = self._resolve_agent_id(name)
        if owner_id is None:
            return True
        if requester is None:
            return False
        requester_id = self._resolve_agent_id(requester) or requester
        return owner_id == requester_id

    def get_peers(self, exclude: str) -> list[str]:
        """Get list of all registered agents except *exclude*."""
        excluded_id = self._resolve_agent_id(exclude)
        return [
            self._display_name(agent_id)
            for agent_id in self._queues
            if agent_id != excluded_id
        ]

    def get_all_names(self) -> list[str]:
        """Get list of all registered visible agent names."""
        return [self._display_name(agent_id) for agent_id in self._queues]

    def get_priority(self, agent_name: str) -> int:
        """Return the discussion priority for *agent_name*."""
        agent_id = self._resolve_agent_id(agent_name)
        if agent_id is None:
            return DISCUSSION_PRIORITY_DEFAULT
        return self._agent_priority.get(agent_id, DISCUSSION_PRIORITY_DEFAULT)

    def get_waiting_speakers(self) -> list[str]:
        """Return the current floor wait queue in priority-agnostic arrival order."""
        return [self._display_name(agent_id) for agent_id in self._floor_wait_queue]

    def recommend_responder(self) -> str | None:
        """Recommend the next natural responder based on speaking recency."""
        if not self._queues:
            return None
        agent_ids = list(self._queues.keys())
        agent_ids.sort(key=self._speaker_sort_key)
        return self._display_name(agent_ids[0])

    def set_max_transcript(self, max_transcript: int) -> None:
        """Update the rolling transcript retention limit."""
        self._max_transcript = max(1, int(max_transcript))
        if len(self._transcript) > self._max_transcript:
            self._transcript = self._transcript[-self._max_transcript :]

    def _speaker_sort_key(self, agent_name: str) -> tuple[int, int, int]:
        """Sort speakers by whether they've spoken and how recently."""
        agent_id = self._resolve_agent_id(agent_name) or agent_name
        last_spoke = self._agent_last_spoke_order.get(agent_id)
        priority = self._agent_priority.get(agent_id, DISCUSSION_PRIORITY_DEFAULT)
        if last_spoke is None:
            return (0, 0, priority)
        return (1, last_spoke, priority)

    def _note_spoke(self, agent_name: str) -> None:
        """Record that *agent_name* just took the floor."""
        agent_id = self._resolve_agent_id(agent_name)
        if agent_id is None or agent_id not in self._queues:
            return
        self._speak_order_counter += 1
        self._agent_last_spoke_order[agent_id] = self._speak_order_counter

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
        sender_id = self._resolve_agent_id(from_agent)
        recipient_id = self._resolve_agent_id(to_agent)
        sender_name = self._display_name(sender_id) if sender_id is not None else from_agent
        recipient_name = (
            self._display_name(recipient_id) if recipient_id is not None else to_agent
        )
        if recipient_id and recipient_id in self._queues:
            msg = AgentMessage(
                from_agent=sender_name,
                text=text,
                to_agent=recipient_name,
                from_agent_id=sender_id,
                to_agent_id=recipient_id,
                from_agent_aliases=self.get_aliases(sender_id) if sender_id is not None else [],
            )
            await self._queues[recipient_id].put(msg)
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
        sender_id = self._resolve_agent_id(from_agent)
        sender_name = self._display_name(sender_id) if sender_id is not None else from_agent
        count = 0
        msg_for_transcript = AgentMessage(
            from_agent=sender_name,
            text=text,
            from_agent_id=sender_id,
            from_agent_aliases=self.get_aliases(sender_id) if sender_id is not None else [],
        )
        for agent_id, queue in self._queues.items():
            if agent_id != sender_id:
                msg = AgentMessage(
                    from_agent=sender_name,
                    text=text,
                    to_agent=self._display_name(agent_id),
                    from_agent_id=sender_id,
                    to_agent_id=agent_id,
                    from_agent_aliases=self.get_aliases(sender_id) if sender_id is not None else [],
                )
                await queue.put(msg)
                count += 1
        self._record_utterance(msg_for_transcript)
        return count

    async def receive(
        self, agent_name: str, timeout: float = 0.0
    ) -> AgentMessage | None:
        """Receive one message for *agent_name*. Non-blocking by default."""
        agent_id = self._resolve_agent_id(agent_name)
        queue = self._queues.get(agent_id) if agent_id is not None else None
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
        agent_id = self._resolve_agent_id(agent_name)
        queue = self._queues.get(agent_id) if agent_id is not None else None
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
        agent_id = self._resolve_agent_id(agent_name) or agent_name
        # Fast path: floor is free, no one else waiting
        if not self._floor.locked() and not self._floor_wait_queue:
            await self._floor.acquire()
            self._floor_holder = agent_id
            self._note_spoke(agent_id)
            return

        # Add ourselves to the priority wait queue
        evt = asyncio.Event()
        self._floor_waiters[agent_id] = evt
        self._floor_wait_queue.append(agent_id)
        try:
            await evt.wait()
        finally:
            self._floor_waiters.pop(agent_id, None)
            if agent_id in self._floor_wait_queue:
                self._floor_wait_queue.remove(agent_id)
        # When woken, we already hold the floor (set by release_floor)
        self._note_spoke(agent_id)

    def release_floor(self) -> None:
        """Release the discussion floor, waking the next highest-priority waiter."""
        self._floor_holder = None

        if self._floor_wait_queue:
            # Prefer agents who have not spoken yet, then the least-recent speaker.
            self._floor_wait_queue.sort(key=self._speaker_sort_key)
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
        if self._floor_holder is None:
            return None
        return self._display_name(self._floor_holder)

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
        excluded_name = self.resolve_name(exclude) if exclude else ""
        lines: list[str] = []
        for m in recent:
            if excluded_name and m.from_agent == excluded_name:
                continue
            text = m.text[:max_chars] + "…" if len(m.text) > max_chars else m.text
            lines.append(f"[{m.from_agent}]: {text}")
        return "\n".join(lines)

    def _resolve_agent_id(self, name: str | None) -> str | None:
        if not name:
            return None
        if name in self._queues:
            return name
        return self._name_map.get(name.lower())

    def _display_name(self, agent_id: str) -> str:
        return self._current_names.get(agent_id, agent_id)
