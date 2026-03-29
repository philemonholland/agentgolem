"""Tests for the sandboxed Moltbook client."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from agentgolem.memory.models import Source, SourceKind
from agentgolem.tools.moltbook import MoltbookClient, MoltbookMessage


# -- Fixtures ---------------------------------------------------------------

@pytest.fixture
def audit_logger(tmp_data_dir):
    """Provide a real AuditLogger backed by a temp directory."""
    from agentgolem.logging.audit import AuditLogger
    return AuditLogger(tmp_data_dir)


@pytest.fixture
def configured_client(audit_logger):
    """A MoltbookClient with valid credentials."""
    return MoltbookClient(
        api_key="mk-test-key",
        base_url="https://moltbook.test/api",
        rate_limit_per_minute=5,
        audit_logger=audit_logger,
    )


@pytest.fixture
def unconfigured_client():
    """A MoltbookClient without credentials."""
    return MoltbookClient()


@pytest.fixture
def sample_message():
    return MoltbookMessage(
        id="msg-001",
        channel="general",
        content="Hello from another agent",
        sender="agent-x",
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )


# -- Properties -------------------------------------------------------------

def test_moltbook_properties(configured_client: MoltbookClient):
    assert configured_client.requires_approval is True
    assert configured_client.supports_dry_run is True
    assert configured_client.name == "moltbook"


# -- Configuration -----------------------------------------------------------

async def test_not_configured_returns_error(unconfigured_client: MoltbookClient):
    result = await unconfigured_client.execute(action="send", channel="c", content="hi")
    assert result.success is False
    assert "not configured" in result.error.lower()


# -- Source creation ---------------------------------------------------------

def test_source_from_message_untrusted(
    configured_client: MoltbookClient, sample_message: MoltbookMessage
):
    source = configured_client.create_source_from_message(sample_message)
    assert source.reliability == pytest.approx(0.1)


def test_source_from_message_kind(
    configured_client: MoltbookClient, sample_message: MoltbookMessage
):
    source = configured_client.create_source_from_message(sample_message)
    assert source.kind == SourceKind.MOLTBOOK


def test_source_independence_group(
    configured_client: MoltbookClient, sample_message: MoltbookMessage
):
    source = configured_client.create_source_from_message(sample_message)
    assert source.independence_group == "moltbook_agent-x"


# -- Dry run -----------------------------------------------------------------

async def test_dry_run_does_not_send(configured_client: MoltbookClient):
    result = await configured_client.dry_run(
        action="send", channel="general", content="test payload"
    )
    assert result.success is True
    assert result.data["dry_run"] is True
    assert result.data["content_length"] == len("test payload")


# -- Audit logging -----------------------------------------------------------

async def test_audit_logged_on_send(configured_client: MoltbookClient, audit_logger):
    await configured_client.execute(action="send", channel="ops", content="hello")
    entries = audit_logger.read()
    moltbook_entries = [e for e in entries if e["mutation_type"] == "moltbook.send"]
    assert len(moltbook_entries) >= 1
    evidence = moltbook_entries[0]["evidence"]
    assert evidence["channel"] == "ops"
    assert evidence["content_length"] == len("hello")
    # Full content must NOT appear in audit logs
    assert "hello" not in str(evidence)


# -- Rate limiting -----------------------------------------------------------

async def test_rate_limit_enforcement():
    client = MoltbookClient(
        api_key="key",
        base_url="https://moltbook.test/api",
        rate_limit_per_minute=3,
    )
    # First 3 should succeed
    for _ in range(3):
        result = await client.execute(action="send", channel="c", content="x")
        assert result.success is True

    # 4th should be rate-limited
    result = await client.execute(action="send", channel="c", content="x")
    assert result.success is False
    assert "rate limit" in result.error.lower()


# -- Dispatch ----------------------------------------------------------------

async def test_execute_dispatch(configured_client: MoltbookClient):
    result = await configured_client.execute(action="read", channel="general")
    assert result.success is True
    assert result.data["messages"] == []


async def test_execute_unknown_action(configured_client: MoltbookClient):
    result = await configured_client.execute(action="delete", channel="x")
    assert result.success is False
    assert "unknown action" in result.error.lower()
