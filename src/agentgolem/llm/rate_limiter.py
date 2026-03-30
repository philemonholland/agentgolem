"""Shared LLM rate limiter — serialises requests across all agents."""
from __future__ import annotations

import asyncio
import time
from typing import Any, TypeVar

from pydantic import BaseModel

from agentgolem.llm.base import LLMClient, Message

T = TypeVar("T", bound=BaseModel)


class LLMRateLimiter:
    """FIFO lock with a cooldown between completions.

    All agents share one instance so only one LLM request flies at a time.
    After a request completes the limiter waits ``delay`` seconds before
    the next caller may proceed.  asyncio.Lock is FIFO in CPython, so
    the agent that just finished naturally goes to the back of the queue.
    """

    def __init__(self, delay: float = 3.0) -> None:
        self._delay = delay
        self._lock: asyncio.Lock | None = None
        self._last_complete: float = 0.0

    def _ensure_lock(self) -> asyncio.Lock:
        # Create the lock lazily inside the running event loop
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def acquire(self) -> None:
        await self._ensure_lock().acquire()
        now = time.monotonic()
        wait = self._delay - (now - self._last_complete)
        if wait > 0:
            await asyncio.sleep(wait)

    def release(self) -> None:
        self._last_complete = time.monotonic()
        if self._lock is not None and self._lock.locked():
            self._lock.release()

    async def __aenter__(self) -> "LLMRateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.release()


class RateLimitedLLM:
    """Transparent wrapper that queues LLM calls through a shared limiter.

    Implements the same interface as ``OpenAIClient`` so it's a drop-in
    replacement — all existing ``self._llm.complete()`` and
    ``self._llm.complete_structured()`` calls work unchanged.
    """

    def __init__(self, inner: LLMClient, limiter: LLMRateLimiter) -> None:
        self._inner = inner
        self._limiter = limiter

    async def complete(self, messages: list[Message], **kwargs: Any) -> str:
        async with self._limiter:
            return await self._inner.complete(messages, **kwargs)

    async def complete_structured(
        self, messages: list[Message], schema: type[T], **kwargs: Any
    ) -> T:
        async with self._limiter:
            return await self._inner.complete_structured(messages, schema, **kwargs)

    async def close(self) -> None:
        await self._inner.close()
