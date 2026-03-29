"""Tests for the email tool."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from agentgolem.logging.audit import AuditLogger
from agentgolem.tools.email_tool import EmailTool


# -- helpers ------------------------------------------------------------------

def _make_tool(tmp_path, **overrides):
    """Create an EmailTool wired to tmp_path directories."""
    defaults = {
        "outbox_dir": tmp_path / "outbox",
        "inbox_dir": tmp_path / "inbox",
        "audit_logger": AuditLogger(tmp_path),
    }
    defaults.update(overrides)
    return EmailTool(**defaults)


def _configured_tool(tmp_path):
    """EmailTool with SMTP credentials configured."""
    return _make_tool(
        tmp_path,
        smtp_host="smtp.test.com",
        smtp_port=587,
        smtp_user="test@test.com",
        smtp_password="pass",
    )


# -- tests --------------------------------------------------------------------

def test_email_tool_properties():
    tool = EmailTool()
    assert tool.name == "email"
    assert tool.description == "Send, draft, and read email"
    assert tool.requires_approval is True
    assert tool.supports_dry_run is True


async def test_draft_creates_file(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.execute(action="draft", to="a@b.com", subject="Hi", body="Hello")
    assert result.success
    files = list((tmp_path / "outbox").glob("*.json"))
    assert len(files) == 1


async def test_draft_content(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.execute(action="draft", to="a@b.com", subject="Hi", body="Hello")
    draft_id = result.data["draft_id"]
    path = tmp_path / "outbox" / f"{draft_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["to"] == "a@b.com"
    assert data["subject"] == "Hi"
    assert data["body"] == "Hello"
    assert data["status"] == "draft"


async def test_dry_run_does_not_send(tmp_path):
    tool = _configured_tool(tmp_path)
    with patch("agentgolem.tools.email_tool.aiosmtplib") as mock_smtp:
        mock_smtp.send = AsyncMock()
        result = await tool.dry_run(action="send", to="a@b.com", subject="Hi", body="Hello")
        assert result.success
        assert result.data["dry_run"] is True
        assert result.data["would_send_to"] == "a@b.com"
        mock_smtp.send.assert_not_called()
    # Should have written a draft file in outbox
    files = list((tmp_path / "outbox").glob("*.json"))
    assert len(files) == 1


async def test_send_not_configured(tmp_path):
    tool = _make_tool(tmp_path)  # no SMTP credentials
    result = await tool.execute(action="send", to="a@b.com", subject="Hi", body="Hello")
    assert result.success is False
    assert "SMTP not configured" in result.error


async def test_send_configured_mock_smtp(tmp_path):
    tool = _configured_tool(tmp_path)
    with patch("agentgolem.tools.email_tool.aiosmtplib") as mock_smtp:
        mock_smtp.send = AsyncMock()
        result = await tool._send("recipient@test.com", "Test", "Body")
        assert result.success
        assert result.data["sent_to"] == "recipient@test.com"
        mock_smtp.send.assert_called_once()


async def test_send_smtp_error(tmp_path):
    tool = _configured_tool(tmp_path)
    with patch("agentgolem.tools.email_tool.aiosmtplib") as mock_smtp:
        mock_smtp.send = AsyncMock(side_effect=ConnectionError("refused"))
        result = await tool._send("a@b.com", "Hi", "Body")
        assert result.success is False
        assert "SMTP error" in result.error


async def test_read_inbox_empty(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.execute(action="read")
    assert result.success
    assert result.data == []


async def test_read_inbox_with_files(tmp_path):
    tool = _make_tool(tmp_path)
    inbox = tmp_path / "inbox"
    # Write two fake emails
    for i in range(2):
        (inbox / f"email{i}.json").write_text(
            json.dumps({
                "id": f"id{i}",
                "from_addr": "sender@test.com",
                "to": "me@test.com",
                "subject": f"Subject {i}",
                "body": f"Body {i}",
                "received_at": "2024-01-01T00:00:00+00:00",
            }),
            encoding="utf-8",
        )
    result = await tool.execute(action="read", limit=10)
    assert result.success
    assert len(result.data) == 2
    assert result.data[0]["subject"] == "Subject 0"


async def test_audit_logged_on_draft(tmp_path):
    tool = _make_tool(tmp_path)
    await tool.execute(action="draft", to="a@b.com", subject="Secret", body="Do not log this body")
    audit = AuditLogger(tmp_path)
    entries = audit.read(limit=10)
    assert len(entries) >= 1
    latest = entries[0]
    assert latest["mutation_type"] == "email.drafted"
    assert latest["evidence"]["to"] == "a@b.com"
    assert latest["evidence"]["subject"] == "Secret"
    # Body must NOT be in audit evidence
    assert "Do not log this body" not in json.dumps(latest)


async def test_execute_dispatch(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.execute(action="draft", to="x@y.com", subject="S", body="B")
    assert result.success
    assert result.data["to"] == "x@y.com"
    assert result.data["subject"] == "S"
    assert result.data["draft_id"]


async def test_execute_unknown_action(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.execute(action="fax", to="a@b.com", subject="S", body="B")
    assert result.success is False
    assert "Unknown action" in result.error
