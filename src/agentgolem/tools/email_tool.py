"""Email tool: send, draft, and read email with audit logging."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

try:
    import aiosmtplib
except ImportError:  # pragma: no cover
    aiosmtplib = None  # type: ignore[assignment]

from email.mime.text import MIMEText

from agentgolem.tools.base import Tool, ToolArgument, ToolResult

if TYPE_CHECKING:
    from pathlib import Path

    from agentgolem.logging.audit import AuditLogger


@dataclass
class EmailDraft:
    id: str
    to: str
    subject: str
    body: str
    created_at: datetime
    status: str = "draft"  # draft, sent, failed


@dataclass
class Email:
    id: str
    from_addr: str
    to: str
    subject: str
    body: str
    received_at: datetime


class EmailTool(Tool):
    """Send, draft, and read email with file-based storage and SMTP support."""

    name = "email"
    description = "Send, draft, and read email"
    requires_approval = True
    supports_dry_run = True
    domains = ("communication", "external")
    safety_class = "external_communication"
    side_effect_class = "external_write"
    supported_actions = ("send", "draft", "read")
    action_descriptions = {
        "send": "Send an email via SMTP",
        "draft": "Write an email draft to the local outbox",
        "read": "Read messages from the local inbox stub",
    }
    action_arguments = {
        "send": (
            ToolArgument("to", "Recipient email address"),
            ToolArgument("subject", "Email subject line"),
            ToolArgument("body", "Email body text"),
        ),
        "draft": (
            ToolArgument("to", "Draft recipient email address"),
            ToolArgument("subject", "Draft subject line"),
            ToolArgument("body", "Draft body text"),
        ),
        "read": (
            ToolArgument(
                "limit",
                "Maximum number of inbox messages to read",
                kind="int",
                required=False,
            ),
        ),
    }
    usage_hint = "email.send(to=person@example.com, subject=Hello, body=...)"

    def __init__(
        self,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        imap_host: str = "",
        imap_user: str = "",
        imap_password: str = "",
        outbox_dir: Path | None = None,
        inbox_dir: Path | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.imap_host = imap_host
        self.imap_user = imap_user
        self.imap_password = imap_password
        self.outbox_dir = outbox_dir
        self.inbox_dir = inbox_dir
        self.audit_logger = audit_logger

        if self.outbox_dir is not None:
            self.outbox_dir.mkdir(parents=True, exist_ok=True)
        if self.inbox_dir is not None:
            self.inbox_dir.mkdir(parents=True, exist_ok=True)

    def _is_configured(self) -> bool:
        """Return True if SMTP host and user are set."""
        return bool(self.smtp_host) and bool(self.smtp_user)

    def is_available(self) -> bool:
        """Return True if any email path is configured or locally stubbed."""
        return self._is_configured() or self.outbox_dir is not None or self.inbox_dir is not None

    def requires_approval_for(self, action: str) -> bool:
        """Only outbound sending requires human approval."""
        return action == "send"

    def approval_action_name(self, action: str) -> str:
        """Map email actions to approval-gate action names."""
        return f"email_{action}"

    async def execute(self, action: str = "send", **kwargs: Any) -> ToolResult:
        """Dispatch based on action: send, draft, or read."""
        if action == "send":
            if not self._is_configured():
                return ToolResult(success=False, error="SMTP not configured")
            return await self._send(
                to=kwargs["to"],
                subject=kwargs["subject"],
                body=kwargs["body"],
            )
        if action == "draft":
            return await self._draft(
                to=kwargs["to"],
                subject=kwargs["subject"],
                body=kwargs["body"],
            )
        if action == "read":
            return await self._read_inbox(limit=kwargs.get("limit", 10))
        return ToolResult(success=False, error=f"Unknown action: {action}")

    async def dry_run(self, action: str = "send", **kwargs: Any) -> ToolResult:
        """Like execute, but for send just writes to outbox without SMTP."""
        if action == "send":
            draft = await self._draft(
                to=kwargs["to"],
                subject=kwargs["subject"],
                body=kwargs["body"],
            )
            return ToolResult(
                success=True,
                data={
                    "dry_run": True,
                    "would_send_to": kwargs["to"],
                    "subject": kwargs["subject"],
                    "draft": draft.data,
                },
            )
        # For non-send actions, just delegate normally
        return await self.execute(action=action, **kwargs)

    async def _send(self, to: str, subject: str, body: str) -> ToolResult:
        """Send an email via SMTP."""
        if aiosmtplib is None:
            return ToolResult(success=False, error="aiosmtplib is not installed")

        msg = MIMEText(body)
        msg["From"] = self.smtp_user
        msg["To"] = to
        msg["Subject"] = subject

        try:
            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_user,
                password=self.smtp_password,
                start_tls=True,
            )
        except Exception as exc:
            self._audit_log("email.send_failed", to, subject)
            return ToolResult(success=False, error=f"SMTP error: {exc}")

        self._audit_log("email.sent", to, subject)
        return ToolResult(success=True, data={"sent_to": to, "subject": subject})

    async def _draft(self, to: str, subject: str, body: str) -> ToolResult:
        """Create an email draft and save to outbox_dir."""
        draft = EmailDraft(
            id=uuid.uuid4().hex,
            to=to,
            subject=subject,
            body=body,
            created_at=datetime.now(UTC),
        )

        if self.outbox_dir is not None:
            path = self.outbox_dir / f"{draft.id}.json"
            data = asdict(draft)
            data["created_at"] = data["created_at"].isoformat()
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        self._audit_log("email.drafted", to, subject)
        return ToolResult(
            success=True,
            data={"draft_id": draft.id, "to": to, "subject": subject, "status": draft.status},
        )

    async def _read_inbox(self, limit: int = 10) -> ToolResult:
        """Read emails from inbox_dir (file-based stub)."""
        if self.inbox_dir is None or not self.inbox_dir.exists():
            return ToolResult(success=True, data=[])

        emails: list[dict[str, Any]] = []
        for path in sorted(self.inbox_dir.glob("*.json"))[:limit]:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                emails.append(raw)
            except (json.JSONDecodeError, OSError):
                continue

        return ToolResult(success=True, data=emails)

    def _audit_log(self, mutation_type: str, to: str, subject: str) -> None:
        """Log email action to audit trail (never logs body or password)."""
        if self.audit_logger is not None:
            self.audit_logger.log(
                mutation_type=mutation_type,
                target_id=to,
                evidence={
                    "to": to,
                    "subject": subject,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
