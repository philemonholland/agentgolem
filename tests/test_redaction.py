"""Tests for the secret redaction filter."""
from __future__ import annotations

import pytest

from agentgolem.config.secrets import Secrets
from agentgolem.logging.redaction import RedactionFilter


def _make_secrets(**overrides: str) -> Secrets:
    """Build a Secrets instance without reading .env."""
    defaults = {
        "openai_api_key": "",
        "email_smtp_password": "",
        "email_imap_password": "",
        "moltbook_api_key": "",
    }
    defaults.update(overrides)
    return Secrets(**defaults, _env_file=None)  # type: ignore[call-arg]


@pytest.fixture()
def filt() -> RedactionFilter:
    secrets = _make_secrets(
        openai_api_key="sk-test-key-12345",
        email_smtp_password="p@ss+word.123",
        moltbook_api_key="mk-test-67890",
    )
    return RedactionFilter(secrets)


# ---- redact() -----------------------------------------------------------

def test_redact_single_secret(filt: RedactionFilter) -> None:
    text = "API key is sk-test-key-12345 end"
    assert filt.redact(text) == "API key is [REDACTED] end"


def test_redact_multiple_secrets(filt: RedactionFilter) -> None:
    text = "key=sk-test-key-12345 pass=p@ss+word.123 mk=mk-test-67890"
    result = filt.redact(text)
    assert "sk-test-key-12345" not in result
    assert "p@ss+word.123" not in result
    assert "mk-test-67890" not in result
    assert result.count("[REDACTED]") == 3


def test_redact_no_secrets() -> None:
    secrets = _make_secrets()  # all empty
    filt = RedactionFilter(secrets)
    assert filt.redact("nothing to redact") == "nothing to redact"


def test_redact_regex_special_chars(filt: RedactionFilter) -> None:
    # p@ss+word.123 contains regex-special chars (+, .)
    text = "password is p@ss+word.123 done"
    assert filt.redact(text) == "password is [REDACTED] done"
    # ensure the dot is literal, not wildcard
    assert filt.redact("p@ss+wordX123") == "p@ss+wordX123"


# ---- redact_dict() -------------------------------------------------------

def test_redact_dict_shallow(filt: RedactionFilter) -> None:
    data = {"event": "call", "api_key": "sk-test-key-12345"}
    result = filt.redact_dict(data)
    assert result["api_key"] == "[REDACTED]"
    assert result["event"] == "call"
    # original unchanged
    assert data["api_key"] == "sk-test-key-12345"


def test_redact_dict_nested(filt: RedactionFilter) -> None:
    data = {"outer": {"inner": {"secret": "has sk-test-key-12345 inside"}}}
    result = filt.redact_dict(data)
    assert result["outer"]["inner"]["secret"] == "has [REDACTED] inside"


def test_redact_dict_with_list(filt: RedactionFilter) -> None:
    data = {"items": ["safe", "sk-test-key-12345", "also safe"]}
    result = filt.redact_dict(data)
    assert result["items"] == ["safe", "[REDACTED]", "also safe"]


def test_redact_non_string_passthrough(filt: RedactionFilter) -> None:
    data = {"count": 42, "rate": 3.14, "flag": True, "nothing": None}
    result = filt.redact_dict(data)
    assert result == data


# ---- structlog processor -------------------------------------------------

def test_structlog_processor(filt: RedactionFilter) -> None:
    event_dict = {
        "event": "Calling API with key sk-test-key-12345",
        "password": "p@ss+word.123",
        "level": "info",
    }
    result = filt.structlog_processor(None, "info", event_dict)
    assert "sk-test-key-12345" not in result["event"]
    assert result["password"] == "[REDACTED]"
    assert result["level"] == "info"
