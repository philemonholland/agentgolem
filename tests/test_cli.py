"""Tests for the CLI control surface."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentgolem.config import reset_config
from agentgolem.interaction.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_config():
    """Reset config singletons before and after each test."""
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def cli_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Patch _get_data_dir and _get_runtime_state to use tmp_path."""
    from agentgolem.runtime.state import RuntimeState

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setattr("agentgolem.interaction.cli._get_data_dir", lambda: data_dir)
    monkeypatch.setattr(
        "agentgolem.interaction.cli._get_runtime_state", lambda: RuntimeState(data_dir)
    )
    return data_dir


def test_status_command(cli_data_dir: Path):
    """status command exits 0 and output contains 'mode'."""
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "mode" in result.output


def test_inspect_soul_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """inspect-soul shows the content of soul.md in cwd."""
    monkeypatch.chdir(tmp_path)
    soul_file = tmp_path / "soul.md"
    soul_file.write_text("I am the soul of AgentGolem.", encoding="utf-8")

    result = runner.invoke(app, ["inspect-soul"])
    assert result.exit_code == 0
    assert "I am the soul of AgentGolem." in result.output


def test_inspect_heartbeat_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """inspect-heartbeat shows the content of heartbeat.md in cwd."""
    monkeypatch.chdir(tmp_path)
    hb_file = tmp_path / "heartbeat.md"
    hb_file.write_text("Current heartbeat summary.", encoding="utf-8")

    result = runner.invoke(app, ["inspect-heartbeat"])
    assert result.exit_code == 0
    assert "Current heartbeat summary." in result.output


def test_inspect_logs_no_logs(cli_data_dir: Path):
    """inspect-logs works gracefully when no log file exists."""
    result = runner.invoke(app, ["inspect-logs"])
    assert result.exit_code == 0
    assert "No activity log yet." in result.output


def test_inspect_pending_empty(cli_data_dir: Path):
    """inspect-pending shows empty message when no tasks are pending."""
    result = runner.invoke(app, ["inspect-pending"])
    assert result.exit_code == 0
    assert "No pending tasks." in result.output


def test_message_command(cli_data_dir: Path):
    """message command writes a JSON file to data/inbox/."""
    result = runner.invoke(app, ["message", "Hello from the human"])
    assert result.exit_code == 0
    assert "Message queued." in result.output

    inbox = cli_data_dir / "inbox"
    files = list(inbox.glob("human_*.json"))
    assert len(files) == 1

    payload = json.loads(files[0].read_text())
    assert payload["text"] == "Hello from the human"
    assert "timestamp" in payload
