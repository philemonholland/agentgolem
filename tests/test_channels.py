"""Tests for agentgolem.interaction.channels."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agentgolem.interaction.channels import (
    Channel,
    ChannelMessage,
    ChannelRouter,
    EmailChannel,
    HumanChatChannel,
    MoltbookChannel,
)
from agentgolem.memory.models import Source, SourceKind
from agentgolem.runtime.interrupts import InterruptManager
from agentgolem.tools.base import ToolResult


# ---------------------------------------------------------------------------
# Stubs for EmailTool and MoltbookClient (being built in parallel)
# ---------------------------------------------------------------------------


class _StubEmailTool:
    """Minimal stand-in for EmailTool."""

    async def _draft(self, body: str) -> ToolResult:
        return ToolResult(success=True, data={"drafted": body})


class _StubMoltbookClient:
    """Minimal stand-in for MoltbookClient."""

    async def post(self, content: str) -> ToolResult:
        return ToolResult(success=True, data={"posted": content})


# ---------------------------------------------------------------------------
# HumanChatChannel
# ---------------------------------------------------------------------------


async def test_human_channel_available():
    im = InterruptManager()
    ch = HumanChatChannel(im)
    assert ch.is_available() is True


async def test_human_channel_send(capsys):
    im = InterruptManager()
    ch = HumanChatChannel(im)
    result = await ch.send("Hello human")
    assert result.success is True
    captured = capsys.readouterr()
    assert "Hello human" in captured.out


async def test_human_channel_trust_level():
    im = InterruptManager()
    ch = HumanChatChannel(im)
    assert ch.trust_level == 1.0


async def test_human_channel_receive():
    im = InterruptManager()
    ch = HumanChatChannel(im)
    await im.send_message("hi from human")
    msg = await ch.receive()
    assert msg is not None
    assert msg.text == "hi from human"
    assert msg.channel == "human"
    assert msg.trust_level == 1.0


async def test_human_channel_receive_empty():
    im = InterruptManager()
    ch = HumanChatChannel(im)
    msg = await ch.receive()
    assert msg is None


# ---------------------------------------------------------------------------
# EmailChannel
# ---------------------------------------------------------------------------


async def test_email_channel_unavailable_when_not_configured():
    ch = EmailChannel()
    assert ch.is_available() is False


async def test_email_channel_available_with_tool():
    ch = EmailChannel(email_tool=_StubEmailTool())
    assert ch.is_available() is True


async def test_email_channel_send():
    ch = EmailChannel(email_tool=_StubEmailTool())
    result = await ch.send("Draft this")
    assert result.success is True
    assert result.data["drafted"] == "Draft this"


async def test_email_channel_send_unconfigured():
    ch = EmailChannel()
    result = await ch.send("anything")
    assert result.success is False
    assert "not configured" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# MoltbookChannel
# ---------------------------------------------------------------------------


async def test_moltbook_channel_trust_level():
    ch = MoltbookChannel()
    assert ch.trust_level == 0.1


async def test_moltbook_channel_unavailable_without_client():
    ch = MoltbookChannel()
    assert ch.is_available() is False


async def test_moltbook_channel_send():
    ch = MoltbookChannel(client=_StubMoltbookClient())
    result = await ch.send("Post this")
    assert result.success is True


async def test_moltbook_channel_receive_returns_none():
    ch = MoltbookChannel(client=_StubMoltbookClient())
    msg = await ch.receive()
    assert msg is None


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_channels_satisfy_protocol():
    im = InterruptManager()
    assert isinstance(HumanChatChannel(im), Channel)
    assert isinstance(EmailChannel(), Channel)
    assert isinstance(MoltbookChannel(), Channel)


# ---------------------------------------------------------------------------
# ChannelRouter
# ---------------------------------------------------------------------------


def test_channel_router_register_and_get():
    router = ChannelRouter()
    im = InterruptManager()
    ch = HumanChatChannel(im)
    router.register(ch, "human")
    assert router.get("human") is ch
    assert router.get("nonexistent") is None


def test_channel_router_list_available():
    router = ChannelRouter()
    im = InterruptManager()
    router.register(HumanChatChannel(im), "human")
    router.register(EmailChannel(), "email")  # not configured → unavailable
    router.register(MoltbookChannel(), "moltbook")  # not configured → unavailable
    available = router.list_available()
    assert "human" in available
    assert "email" not in available
    assert "moltbook" not in available


# ---------------------------------------------------------------------------
# ChannelRouter.create_source_from_message
# ---------------------------------------------------------------------------


def _make_msg(channel: str, sender: str = "test", trust: float = 0.5) -> ChannelMessage:
    return ChannelMessage(
        text="hello",
        channel=channel,
        sender=sender,
        timestamp=datetime.now(timezone.utc),
        trust_level=trust,
    )


def test_create_source_from_human_message():
    router = ChannelRouter()
    src = router.create_source_from_message(_make_msg("human", trust=1.0))
    assert src.kind == SourceKind.HUMAN
    assert src.reliability == 0.8


def test_create_source_from_email_message():
    router = ChannelRouter()
    src = router.create_source_from_message(_make_msg("email", trust=0.3))
    assert src.kind == SourceKind.EMAIL
    assert src.reliability == 0.3


def test_create_source_from_moltbook_message():
    router = ChannelRouter()
    src = router.create_source_from_message(_make_msg("moltbook", trust=0.1))
    assert src.kind == SourceKind.MOLTBOOK
    assert src.reliability == 0.1
