"""Shared LLM rate limiter — spaces requests across all agents."""
from __future__ import annotations

import asyncio
import time
from typing import Any, TypeVar

from pydantic import BaseModel

from agentgolem.llm.base import LLMClient, Message

T = TypeVar("T", bound=BaseModel)


class LLMRateLimiter:
    """Throttle that spaces LLM request *starts* by ``delay`` seconds.

    Requests may overlap (one can still be streaming while the next begins),
    but no two requests will *start* closer than ``delay`` seconds apart.
    """

    def __init__(self, delay: float = 3.0) -> None:
        self._delay = delay
        self._lock: asyncio.Lock | None = None
        self._last_start: float = 0.0

    def _ensure_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def throttle(self) -> None:
        """Wait until at least ``delay`` seconds since the last request started."""
        async with self._ensure_lock():
            now = time.monotonic()
            wait = self._delay - (now - self._last_start)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()


class RateLimitedLLM:
    """Transparent wrapper that throttles LLM call starts through a shared limiter.

    Implements the same interface as ``OpenAIClient`` so it's a drop-in
    replacement — all existing ``self._llm.complete()`` and
    ``self._llm.complete_structured()`` calls work unchanged.
    """

    def __init__(self, inner: LLMClient, limiter: LLMRateLimiter) -> None:
        self._inner = inner
        self._limiter = limiter

    async def complete(self, messages: list[Message], **kwargs: Any) -> str:
        await self._limiter.throttle()
        return await self._inner.complete(messages, **kwargs)

    async def complete_structured(
        self, messages: list[Message], schema: type[T], **kwargs: Any
    ) -> T:
        await self._limiter.throttle()
        return await self._inner.complete_structured(messages, schema, **kwargs)

    async def close(self) -> None:
        await self._inner.close()

    @property
    def model_name(self) -> str:
        return getattr(self._inner, "model_name", getattr(self._inner, "_model", "unknown"))
