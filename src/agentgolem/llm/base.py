"""LLM abstraction — provider-agnostic interface."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@runtime_checkable
class LLMClient(Protocol):
    async def complete(self, messages: list[Message], **kwargs: Any) -> str:
        """Send messages and get a text completion."""
        ...

    async def complete_structured(
        self, messages: list[Message], schema: type[T], **kwargs: Any
    ) -> T:
        """Send messages and parse response into a Pydantic model."""
        ...
