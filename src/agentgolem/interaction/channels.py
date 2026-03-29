"""Channel abstractions for multi-source agent communication."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from agentgolem.memory.models import Source, SourceKind
from agentgolem.runtime.interrupts import InterruptManager
from agentgolem.tools.base import ToolResult

if TYPE_CHECKING:
    from agentgolem.tools.email_tool import EmailTool
    from agentgolem.tools.moltbook import MoltbookClient


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ChannelMessage:
    """A message received from any communication channel."""

    text: str
    channel: str  # "human", "email", "moltbook"
    sender: str
    timestamp: datetime = field(default_factory=_now)
    trust_level: float = 0.5


@runtime_checkable
class Channel(Protocol):
    """Protocol every concrete channel must satisfy."""

    async def send(self, content: str) -> ToolResult: ...
    async def receive(self) -> ChannelMessage | None: ...
    def is_available(self) -> bool: ...


# ---------------------------------------------------------------------------
# Concrete channels
# ---------------------------------------------------------------------------


class HumanChatChannel:
    """Direct human interaction via console / interrupt queue."""

    trust_level: float = 1.0

    def __init__(self, interrupt_manager: InterruptManager) -> None:
        self._im = interrupt_manager

    def is_available(self) -> bool:
        return True

    async def send(self, content: str) -> ToolResult:
        print(f"[Golem → Human] {content}")
        return ToolResult(success=True, data={"delivered": True})

    async def receive(self) -> ChannelMessage | None:
        if not self._im.has_messages():
            return None
        msg = await self._im.get_message(timeout=1.0)
        if msg is None:
            return None
        return ChannelMessage(
            text=msg.text,
            channel="human",
            sender="human",
            timestamp=msg.timestamp,
            trust_level=self.trust_level,
        )


class EmailChannel:
    """Communication via the email tool (drafts / send)."""

    trust_level: float = 0.3

    def __init__(self, email_tool: EmailTool | None = None, inbox_dir: Path | None = None) -> None:
        self._tool: Any = email_tool
        self._inbox_dir = inbox_dir

    def is_available(self) -> bool:
        return self._tool is not None

    async def send(self, content: str) -> ToolResult:
        if self._tool is None:
            return ToolResult(success=False, error="EmailTool not configured")
        if hasattr(self._tool, "_draft"):
            return await self._tool._draft(content)
        return await self._tool.execute(action="draft", body=content)

    async def receive(self) -> ChannelMessage | None:
        if self._inbox_dir is None or not self._inbox_dir.exists():
            return None
        files = sorted(self._inbox_dir.glob("*.txt"))
        if not files:
            return None
        path = files[0]
        text = path.read_text(encoding="utf-8")
        path.unlink()
        return ChannelMessage(
            text=text,
            channel="email",
            sender=path.stem,
            trust_level=self.trust_level,
        )


class MoltbookChannel:
    """Communication via the Moltbook social network — UNTRUSTED."""

    trust_level: float = 0.1

    def __init__(self, client: MoltbookClient | None = None) -> None:
        self._client: Any = client

    def is_available(self) -> bool:
        return self._client is not None

    async def send(self, content: str) -> ToolResult:
        if self._client is None:
            return ToolResult(success=False, error="MoltbookClient not configured")
        if hasattr(self._client, "post"):
            return await self._client.post(content)
        return ToolResult(success=True, data={"posted": content})

    async def receive(self) -> ChannelMessage | None:
        return None


# ---------------------------------------------------------------------------
# Channel router
# ---------------------------------------------------------------------------

_TRUST_MAP: dict[str, tuple[SourceKind, float]] = {
    "human": (SourceKind.HUMAN, 0.8),
    "email": (SourceKind.EMAIL, 0.3),
    "moltbook": (SourceKind.MOLTBOOK, 0.1),
}


class ChannelRouter:
    """Registry and dispatcher for communication channels."""

    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def register(self, channel: Channel, name: str) -> None:
        self._channels[name] = channel

    def get(self, name: str) -> Channel | None:
        return self._channels.get(name)

    def list_available(self) -> list[str]:
        return [name for name, ch in self._channels.items() if ch.is_available()]

    def create_source_from_message(self, msg: ChannelMessage) -> Source:
        kind, reliability = _TRUST_MAP.get(
            msg.channel, (SourceKind.HUMAN, 0.5)
        )
        return Source(
            kind=kind,
            origin=msg.sender,
            reliability=reliability,
            timestamp=msg.timestamp,
        )
