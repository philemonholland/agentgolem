"""Anthropic (Claude) LLM client — speaks the native Messages API."""
from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, SecretStr

from agentgolem.llm.base import LLMClient, Message

T = TypeVar("T", bound=BaseModel)

ANTHROPIC_API_VERSION = "2023-06-01"


class AnthropicClient:
    """Async client for the Anthropic Messages API.

    Implements the same ``LLMClient`` protocol as ``OpenAIClient`` but speaks
    Anthropic's native wire format (``x-api-key`` auth, ``/messages`` endpoint,
    system messages in a dedicated ``system`` field).
    """

    def __init__(
        self,
        api_key: SecretStr,
        model: str = "claude-sonnet-4-20250514",
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "x-api-key": self._api_key.get_secret_value(),
                    "anthropic-version": ANTHROPIC_API_VERSION,
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        return self._client

    async def complete(self, messages: list[Message], **kwargs: Any) -> str:
        """Send chat completion request via the Anthropic Messages API."""
        client = await self._get_client()
        timeout = kwargs.pop("timeout", None)

        # Anthropic puts system messages in a separate field
        system_parts: list[str] = []
        conversation: list[dict[str, str]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                conversation.append({"role": m.role, "content": m.content})

        # Anthropic requires at least one user message
        if not conversation:
            conversation.append({"role": "user", "content": "Continue."})

        # Translate max_completion_tokens → Anthropic's max_tokens
        max_tokens = kwargs.pop("max_completion_tokens", kwargs.pop("max_tokens", 16384))

        # Drop OpenAI-specific kwargs Anthropic doesn't understand
        kwargs.pop("response_format", None)
        kwargs.pop("frequency_penalty", None)
        kwargs.pop("presence_penalty", None)

        payload: dict[str, Any] = {
            "model": kwargs.pop("model", self._model),
            "messages": conversation,
            "max_tokens": max_tokens,
            **kwargs,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        response = await client.post(
            "/messages",
            json=payload,
            **({"timeout": timeout} if timeout else {}),
        )
        response.raise_for_status()
        data = response.json()

        # Anthropic returns content as a list of blocks
        content_blocks = data.get("content", [])
        text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
        return "\n".join(text_parts)

    @property
    def model_name(self) -> str:
        return self._model

    async def complete_structured(
        self, messages: list[Message], schema: type[T], **kwargs: Any
    ) -> T:
        """Chat completion with JSON mode, parsed into Pydantic model."""
        system_msg = Message(
            role="system",
            content=(
                "Respond with valid JSON matching this schema: "
                f"{json.dumps(schema.model_json_schema())}"
            ),
        )
        all_messages = [system_msg, *messages]
        raw = await self.complete(all_messages, **kwargs)
        return schema.model_validate_json(raw)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
