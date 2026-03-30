"""Shared pytest fixtures for AgentGolem tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory structure."""
    dirs = [
        "soul_versions",
        "heartbeat_history",
        "logs",
        "memory",
        "memory/snapshots",
        "approvals",
        "inbox",
        "outbox",
        "state",
    ]
    for d in dirs:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def tmp_env_file(tmp_path: Path) -> Path:
    """Create a temporary .env file with test secrets."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=sk-test-key-12345\n"
        "OPENAI_BASE_URL=https://api.openai.com/v1\n"
        "DEEPSEEK_API_KEY=sk-deepseek-key-54321\n"
        "DEEPSEEK_BASE_URL=https://api.deepseek.com/v1\n"
        "EMAIL_SMTP_HOST=smtp.test.com\n"
        "EMAIL_SMTP_PORT=587\n"
        "EMAIL_SMTP_USER=test@test.com\n"
        "EMAIL_SMTP_PASSWORD=test-smtp-pass\n"
        "EMAIL_IMAP_HOST=imap.test.com\n"
        "EMAIL_IMAP_USER=test@test.com\n"
        "EMAIL_IMAP_PASSWORD=test-imap-pass\n"
        "MOLTBOOK_API_KEY=mk-test-key-67890\n"
        "MOLTBOOK_BASE_URL=https://moltbook.test.com/api\n"
    )
    return env_path


@pytest.fixture
def mock_settings_yaml(tmp_path: Path) -> Path:
    """Create a temporary settings.yaml for testing."""
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        "data_dir: '{data_dir}'\n"
        "awake_duration_minutes: 1.0\n"
        "sleep_duration_minutes: 2.0\n"
        "wind_down_minutes: 0.5\n"
        "sleep_cycle_minutes: 1.0\n"
        "sleep_phase_cycle_length: 4\n"
        "sleep_phase_split: 0.5\n"
        "sleep_state_top_k: 64\n"
        "sleep_membrane_decay: 0.75\n"
        "sleep_consolidation_threshold: 1.1\n"
        "sleep_dream_threshold: 0.7\n"
        "sleep_refractory_steps: 3\n"
        "sleep_stdp_window_steps: 4\n"
        "sleep_stdp_strength: 0.12\n"
        "sleep_dream_noise: 0.25\n"
        "log_level: DEBUG\n"
        "llm_provider: openai\n"
        "llm_model: gpt-4o-mini\n"
        "llm_discussion_model: deepseek-reasoner\n"
        "llm_code_model: gpt-5.4\n"
        "soul_update_min_confidence: 0.5\n"
        "dry_run_mode: true\n"
        "email_enabled: false\n"
        "moltbook_enabled: false\n".format(data_dir=str(tmp_path / "data").replace("\\", "/"))
    )
    return settings_path
