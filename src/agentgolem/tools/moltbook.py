"""Sandboxed Moltbook AI-network communication client.

Moltbook is an external AI-agent communication system treated as a **hostile
prompt-injection surface**.  All I/O is heavily logged, content is tagged
untrusted (reliability=0.1), and no Moltbook content may directly mutate
soul, heartbeat, or canonical memory without human approval.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agentgolem.memory.models import Source, SourceKind
from agentgolem.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from agentgolem.logging.audit import AuditLogger

logger = logging.getLogger(__name__)


@dataclass
class MoltbookMessage:
    """A single inbound/outbound Moltbook message."""

    id: str
    channel: str
    content: str
    sender: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MoltbookClient(Tool):
    """Sandboxed communication with the Moltbook AI network.

    Security invariants:
    * ``requires_approval = True`` — every outbound message needs human sign-off.
    * ``supports_dry_run = True`` — callers can preview without side-effects.
    * All content is tagged ``reliability=0.1`` (UNTRUSTED).
    * Full content is **never** written to the audit log; only metadata
      (channel, content length, timestamp) is recorded.
    """

    name = "moltbook"
    description = "Sandboxed communication with Moltbook AI network"
    requires_approval = True
    supports_dry_run = True

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        rate_limit_per_minute: int = 5,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._rate_limit = rate_limit_per_minute
        self._audit = audit_logger
        # Sliding-window timestamps for rate limiting
        self._call_timestamps: list[float] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def execute(self, action: str = "send", **kwargs: Any) -> ToolResult:
        """Dispatch to the requested action."""
        if not self._is_configured():
            return ToolResult(
                success=False,
                error="MoltbookClient is not configured (missing api_key or base_url)",
            )

        if action == "send":
            return await self._send_message(
                channel=kwargs.get("channel", ""),
                content=kwargs.get("content", ""),
            )
        if action == "read":
            return await self._read_messages(
                channel=kwargs.get("channel", ""),
                limit=kwargs.get("limit", 10),
            )

        return ToolResult(success=False, error=f"Unknown action: {action}")

    async def dry_run(self, action: str = "send", **kwargs: Any) -> ToolResult:
        """Preview what would happen without performing any side-effects."""
        self._log_audit(
            "moltbook.dry_run",
            {
                "action": action,
                "channel": kwargs.get("channel", ""),
                "content_length": len(kwargs.get("content", "")),
            },
        )
        return ToolResult(
            success=True,
            data={
                "dry_run": True,
                "action": action,
                "channel": kwargs.get("channel", ""),
                "content_length": len(kwargs.get("content", "")),
            },
        )

    # ------------------------------------------------------------------
    # Source creation (untrusted!)
    # ------------------------------------------------------------------

    def create_source_from_message(self, message: MoltbookMessage) -> Source:
        """Create a :class:`Source` from a Moltbook message.

        The source is intentionally tagged with ``reliability=0.1`` because
        Moltbook content is an untrusted, external prompt-injection surface.
        """
        return Source(
            kind=SourceKind.MOLTBOOK,
            origin=f"moltbook://{message.channel}/{message.id}",
            reliability=0.1,
            independence_group=f"moltbook_{message.sender}",
        )

    # ------------------------------------------------------------------
    # Internal actions (stubs — protocol TBD)
    # ------------------------------------------------------------------

    async def _send_message(self, channel: str, content: str) -> ToolResult:
        """Send a message to a Moltbook channel (stub)."""
        if not self._check_rate_limit():
            return ToolResult(success=False, error="Rate limit exceeded")

        self._log_audit(
            "moltbook.send",
            {
                "channel": channel,
                "content_length": len(content),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.info("Moltbook send stub: channel=%s len=%d", channel, len(content))

        # Stub: real HTTP call goes here once protocol is finalised
        return ToolResult(
            success=True,
            data={"sent": True, "channel": channel, "content_length": len(content)},
        )

    async def _read_messages(self, channel: str, limit: int = 10) -> ToolResult:
        """Read messages from a Moltbook channel (stub)."""
        self._log_audit(
            "moltbook.read",
            {"channel": channel, "limit": limit},
        )
        logger.info("Moltbook read stub: channel=%s limit=%d", channel, limit)

        # Stub: returns empty for now
        return ToolResult(success=True, data={"messages": []})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_configured(self) -> bool:
        """Return True if both api_key and base_url are set."""
        return bool(self._api_key) and bool(self._base_url)

    def _check_rate_limit(self) -> bool:
        """Sliding-window rate limiter (per minute)."""
        now = time.monotonic()
        window_start = now - 60.0
        self._call_timestamps = [t for t in self._call_timestamps if t > window_start]
        if len(self._call_timestamps) >= self._rate_limit:
            return False
        self._call_timestamps.append(now)
        return True

    def _log_audit(self, mutation_type: str, evidence: dict[str, Any]) -> None:
        """Write to the audit log if an AuditLogger is available."""
        if self._audit is not None:
            self._audit.log(
                mutation_type=mutation_type,
                target_id="moltbook",
                evidence=evidence,
            )
