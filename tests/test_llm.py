"""Tests for the LLM abstraction layer."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx
from httpx import Response
from pydantic import BaseModel, SecretStr

from agentgolem.llm import OpenAIClient, get_llm_client
from agentgolem.llm.base import LLMClient, Message


def test_message_creation() -> None:
    msg = Message(role="user", content="Hello")
    assert msg.role == "user"
    assert msg.content == "Hello"


def test_openai_client_implements_protocol() -> None:
    client = OpenAIClient(api_key=SecretStr("key"))
    assert isinstance(client, LLMClient)


@pytest.mark.asyncio
@respx.mock
async def test_complete_sends_correct_request() -> None:
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=Response(
            200, json={"choices": [{"message": {"content": "Hello!"}}]}
        )
    )
    client = OpenAIClient(api_key=SecretStr("test-key"), model="gpt-5.4-mini")
    result = await client.complete([Message(role="user", content="Hi")])

    assert result == "Hello!"
    assert route.called
    request_body = route.calls[0].request
    body = request_body.read()
    import json

    payload = json.loads(body)
    assert payload["model"] == "gpt-5.4-mini"
    assert payload["messages"] == [{"role": "user", "content": "Hi"}]
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_complete_structured_parses_json() -> None:
    class Greeting(BaseModel):
        text: str
        score: int

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"text": "hi", "score": 42}'}}
                ]
            },
        )
    )
    client = OpenAIClient(api_key=SecretStr("test-key"))
    result = await client.complete_structured(
        [Message(role="user", content="greet me")], Greeting
    )

    assert isinstance(result, Greeting)
    assert result.text == "hi"
    assert result.score == 42
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_complete_raises_on_http_error() -> None:
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=Response(500, json={"error": "internal server error"})
    )
    client = OpenAIClient(api_key=SecretStr("test-key"))

    with pytest.raises(httpx.HTTPStatusError):
        await client.complete([Message(role="user", content="Hi")])
    await client.close()


def test_get_llm_client_factory() -> None:
    settings = SimpleNamespace(llm_model="gpt-5.4")
    secrets = SimpleNamespace(
        openai_api_key=SecretStr("sk-test"),
        openai_base_url="https://custom.api.com/v1",
    )
    client = get_llm_client(settings, secrets)

    assert isinstance(client, OpenAIClient)
    assert client._model == "gpt-5.4"
    assert client._base_url == "https://custom.api.com/v1"
