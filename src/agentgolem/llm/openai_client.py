"""OpenAI-compatible LLM client."""
from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, SecretStr

from agentgolem.llm.base import LLMClient, Message

T = TypeVar("T", bound=BaseModel)


class OpenAIClient:
    """Async client for OpenAI-compatible APIs."""

    def __init__(
        self,
        api_key: SecretStr,
        model: str = "gpt-5.4-mini",
        base_url: str = "https://api.openai.com/v1",
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
                    "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        return self._client

    async def complete(self, messages: list[Message], **kwargs: Any) -> str:
        """Send chat completion request."""
        client = await self._get_client()
        timeout = kwargs.pop("timeout", None)
        payload: dict[str, Any] = {
            "model": kwargs.pop("model", self._model),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_completion_tokens": kwargs.pop("max_completion_tokens", 16384),
            **kwargs,
        }
        response = await client.post(
            "chat/completions", json=payload,
            **({"timeout": timeout} if timeout else {}),
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

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

        raw = await self.complete(
            all_messages,
            response_format={"type": "json_object"},
            **kwargs,
        )
        return schema.model_validate_json(raw)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
