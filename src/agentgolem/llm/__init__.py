"""LLM abstraction layer."""
from agentgolem.llm.base import LLMClient, Message
from agentgolem.llm.openai_client import OpenAIClient
from typing import Any


def get_llm_client(settings: Any, secrets: Any) -> OpenAIClient:
    """Factory to create an LLM client based on config."""
    return OpenAIClient(
        api_key=secrets.openai_api_key,
        model=settings.llm_model,
        base_url=secrets.openai_base_url,
    )


__all__ = ["Message", "LLMClient", "OpenAIClient", "get_llm_client"]