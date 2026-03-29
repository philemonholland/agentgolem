"""Human interrupt and message system."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class HumanMessage:
    text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InterruptManager:
    def __init__(self) -> None:
        self._interrupt_event = asyncio.Event()
        self._message_queue: asyncio.Queue[HumanMessage] = asyncio.Queue()
        self._resume_event = asyncio.Event()

    async def request_interrupt(self, reason: str = "") -> None:
        """Signal that the agent should interrupt current work."""
        self._interrupt_event.set()

    def check_interrupt(self) -> bool:
        """Non-blocking check if interrupt was requested."""
        return self._interrupt_event.is_set()

    def clear_interrupt(self) -> None:
        """Clear the interrupt flag after handling."""
        self._interrupt_event.clear()

    async def send_message(self, text: str) -> None:
        """Queue a human message and trigger interrupt."""
        msg = HumanMessage(text=text)
        await self._message_queue.put(msg)
        self._interrupt_event.set()

    async def get_message(self, timeout: float | None = None) -> HumanMessage | None:
        """Get next message, or None if timeout."""
        try:
            if timeout is not None:
                return await asyncio.wait_for(self._message_queue.get(), timeout=timeout)
            return await self._message_queue.get()
        except asyncio.TimeoutError:
            return None

    def has_messages(self) -> bool:
        return not self._message_queue.empty()

    async def wait_for_resume(self) -> None:
        """Block until resume signal."""
        self._resume_event.clear()
        await self._resume_event.wait()

    def signal_resume(self) -> None:
        """Signal that the agent should resume."""
        self._resume_event.set()
