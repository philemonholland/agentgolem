"""Tests for the LLM abstraction layer."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from httpx import Response
from pydantic import BaseModel, SecretStr

from agentgolem.llm import OpenAIClient, get_llm_client
from agentgolem.llm.anthropic_client import AnthropicClient
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


# --- Anthropic client tests ---


def test_anthropic_client_implements_protocol() -> None:
    client = AnthropicClient(api_key=SecretStr("key"))
    assert isinstance(client, LLMClient)


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_complete_sends_correct_request() -> None:
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(
            200,
            json={
                "content": [{"type": "text", "text": "Hello from Claude!"}],
                "model": "claude-sonnet-4-20250514",
                "stop_reason": "end_turn",
            },
        )
    )
    client = AnthropicClient(
        api_key=SecretStr("sk-ant-test"), model="claude-sonnet-4-20250514"
    )
    result = await client.complete([
        Message(role="system", content="You are helpful."),
        Message(role="user", content="Hi"),
    ])

    assert result == "Hello from Claude!"
    assert route.called
    body = json.loads(route.calls[0].request.read())
    assert body["model"] == "claude-sonnet-4-20250514"
    assert body["system"] == "You are helpful."
    assert body["messages"] == [{"role": "user", "content": "Hi"}]
    # Verify Anthropic-specific headers
    headers = route.calls[0].request.headers
    assert headers["x-api-key"] == "sk-ant-test"
    assert "anthropic-version" in headers
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_complete_multiple_system_messages() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(
            200,
            json={"content": [{"type": "text", "text": "OK"}]},
        )
    )
    client = AnthropicClient(api_key=SecretStr("key"))
    await client.complete([
        Message(role="system", content="First system."),
        Message(role="system", content="Second system."),
        Message(role="user", content="Go"),
    ])

    body = json.loads(respx.calls[0].request.read())
    assert body["system"] == "First system.\n\nSecond system."
    assert len(body["messages"]) == 1
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_complete_raises_on_http_error() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(500, json={"error": "overloaded"})
    )
    client = AnthropicClient(api_key=SecretStr("key"))
    with pytest.raises(httpx.HTTPStatusError):
        await client.complete([Message(role="user", content="Hi")])
    await client.close()


def test_anthropic_model_name() -> None:
    client = AnthropicClient(api_key=SecretStr("key"), model="claude-sonnet-4.6")
    assert client.model_name == "claude-sonnet-4.6"


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_strips_openai_only_kwargs() -> None:
    """Anthropic client drops frequency_penalty, presence_penalty, response_format."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(
            200,
            json={"content": [{"type": "text", "text": "OK"}]},
        )
    )
    client = AnthropicClient(api_key=SecretStr("key"))
    await client.complete(
        [Message(role="user", content="Hi")],
        temperature=0.8,
        frequency_penalty=0.5,
        presence_penalty=0.3,
        response_format={"type": "json_object"},
    )
    body = json.loads(route.calls[0].request.read())
    assert body["temperature"] == 0.8
    assert "frequency_penalty" not in body
    assert "presence_penalty" not in body
    assert "response_format" not in body
    await client.close()


# --- Bogus reply detection tests ---


class TestMissingSourceReplyDetection:
    """Test the _looks_like_missing_source_reply static method."""

    @staticmethod
    def _check(text: str) -> bool:
        from agentgolem.runtime.loop import MainLoop
        return MainLoop._looks_like_missing_source_reply(text)

    def test_ascii_apostrophe(self) -> None:
        assert self._check("I don't have the actual text of chapter 5")

    def test_unicode_smart_apostrophe(self) -> None:
        """The root cause of the chapter digest bug: Unicode \u2019 wasn't matched."""
        assert self._check("I don\u2019t have the actual text of chapter 5")

    def test_text_not_included(self) -> None:
        assert self._check(
            "I can do that, but the chapter text itself is not included in your message"
        )

    def test_missing_the_text(self) -> None:
        assert self._check(
            "I\u2019m missing the actual text of Chapter 6 (\u201cMarch Eighth\u201d)"
        )

    def test_paste_the_chapter(self) -> None:
        assert self._check("If you paste the chapter here, I can reflect on it.")

    def test_no_direct_access(self) -> None:
        assert self._check(
            "I don\u2019t have direct access to the URL from here."
        )

    def test_legitimate_reflection_not_flagged(self) -> None:
        assert not self._check(
            "This chapter reveals a profound tension between aspiration and "
            "self-deception. The author argues that genuine integrity requires "
            "surrendering the need to appear impressive."
        )

    def test_empty_string(self) -> None:
        assert not self._check("")

    def test_short_legitimate_text(self) -> None:
        assert not self._check("A thoughtful meditation on mortality.")


# --- Provider routing tests ---


class TestResolvedLLMRoute:
    def test_default_provider_is_openai(self) -> None:
        from agentgolem.runtime.loop import ResolvedLLMRoute
        route = ResolvedLLMRoute(
            route_name="discussion",
            model="gpt-5",
            api_key=SecretStr("key"),
            base_url="https://api.openai.com/v1",
            source="openai_fallback",
        )
        assert route.provider == "openai"

    def test_anthropic_provider(self) -> None:
        from agentgolem.runtime.loop import ResolvedLLMRoute
        route = ResolvedLLMRoute(
            route_name="discussion",
            model="claude-sonnet-4.6",
            api_key=SecretStr("key"),
            base_url="https://api.anthropic.com/v1",
            source="provider:anthropic",
            provider="anthropic",
        )
        assert route.provider == "anthropic"


# --- Secrets provider key lookup ---


def test_secrets_anthropic_key_lookup() -> None:
    from agentgolem.config.secrets import Secrets
    secrets = Secrets(anthropic_api_key=SecretStr("sk-ant-test"))
    key = secrets.get_provider_api_key("anthropic")
    assert key.get_secret_value() == "sk-ant-test"
