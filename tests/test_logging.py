"""Tests for structured logging and audit subsystem."""
from __future__ import annotations

import json
import logging

import structlog

from agentgolem.config.secrets import Secrets
from agentgolem.logging.audit import AuditLogger
from agentgolem.logging.structured import get_logger, setup_logging


def _teardown_logging() -> None:
    """Reset logging state to avoid cross-test interference."""
    logging.getLogger().handlers.clear()
    structlog.reset_defaults()


def test_setup_logging_creates_log_dir(tmp_path):
    """setup_logging creates the logs directory."""
    try:
        setup_logging(data_dir=tmp_path)
        assert (tmp_path / "logs").is_dir()
    finally:
        _teardown_logging()


def test_activity_log_json_format(tmp_path):
    """After setup_logging + logging a message, activity.jsonl contains valid JSON."""
    try:
        setup_logging(data_dir=tmp_path)
        logger = get_logger("test")
        logger.info("hello_world", foo="bar")

        log_file = tmp_path / "logs" / "activity.jsonl"
        assert log_file.exists()
        lines = [line for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["event"] == "hello_world"
        assert entry["foo"] == "bar"
        assert "level" in entry
        assert "timestamp" in entry
    finally:
        _teardown_logging()


def test_activity_log_redaction(tmp_path):
    """Log a message containing a secret — verify it's redacted in activity.jsonl."""
    known_secret = "super-secret-key-12345"
    secrets = Secrets(openai_api_key=known_secret)
    try:
        setup_logging(data_dir=tmp_path, secrets=secrets)
        logger = get_logger("test")
        logger.warning("leak_attempt", payload=f"key is {known_secret}")

        log_file = tmp_path / "logs" / "activity.jsonl"
        content = log_file.read_text(encoding="utf-8")
        assert known_secret not in content
        assert "[REDACTED]" in content
    finally:
        _teardown_logging()


def test_audit_log_append(tmp_path):
    """AuditLogger.log() appends entries, AuditLogger.read() returns them."""
    audit = AuditLogger(data_dir=tmp_path)
    audit.log("create", "node-1", {"source": "test"})
    audit.log("update", "node-2", {"source": "test"}, diff="-old\n+new")

    entries = audit.read()
    assert len(entries) == 2
    assert entries[0]["mutation_type"] == "update"
    assert entries[1]["mutation_type"] == "create"
    assert entries[0]["diff"] == "-old\n+new"
    assert "diff" not in entries[1]


def test_audit_log_ordering(tmp_path):
    """read() returns most recent first."""
    audit = AuditLogger(data_dir=tmp_path)
    audit.log("first", "id-1", {})
    audit.log("second", "id-2", {})
    audit.log("third", "id-3", {})

    entries = audit.read()
    assert entries[0]["mutation_type"] == "third"
    assert entries[1]["mutation_type"] == "second"
    assert entries[2]["mutation_type"] == "first"


def test_audit_log_empty(tmp_path):
    """read() on non-existent file returns empty list."""
    audit = AuditLogger(data_dir=tmp_path)
    assert audit.read() == []


def test_get_logger():
    """get_logger returns a bound logger."""
    try:
        setup_logging()
        logger = get_logger("mylogger")
        assert hasattr(logger, "info") and hasattr(logger, "warning")
    finally:
        _teardown_logging()
