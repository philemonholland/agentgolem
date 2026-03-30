"""Tests for agentgolem.config subsystem."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import SecretStr

from agentgolem.config import (
    Secrets,
    Settings,
    get_secrets,
    get_settings,
    load_settings,
    reset_config,
)

ENV_EXAMPLE_PATH = Path(__file__).resolve().parent.parent / ".env.example"


@pytest.fixture(autouse=True)
def _clean_singletons():
    """Reset config singletons before and after every test."""
    reset_config()
    yield
    reset_config()


# ── 1. Settings loads from YAML with correct types ──────────────────────


def test_settings_from_yaml(mock_settings_yaml: Path) -> None:
    settings = load_settings(mock_settings_yaml)
    assert isinstance(settings, Settings)
    assert settings.awake_duration_minutes == 1.0
    assert settings.sleep_duration_minutes == 2.0
    assert settings.wind_down_minutes == 0.5
    assert settings.sleep_cycle_minutes == 1.0
    assert settings.log_level == "DEBUG"
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.dry_run_mode is True
    assert isinstance(settings.data_dir, Path)


# ── 2. Settings uses defaults when no YAML file exists ──────────────────


def test_settings_defaults_when_no_yaml(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "nonexistent.yaml")
    assert settings.data_dir == Path("data")
    assert settings.awake_duration_minutes == 10.0
    assert settings.sleep_duration_minutes == 5.0
    assert settings.wind_down_minutes == 2.0
    assert settings.llm_model == "gpt-5"
    assert settings.log_level == "INFO"
    assert settings.dry_run_mode is False
    assert settings.approval_required_actions == ["email_send", "moltbook_send"]


# ── 3. Secrets loads from a .env file ───────────────────────────────────


def test_secrets_from_env_file(tmp_env_file: Path) -> None:
    secrets = Secrets(_env_file=str(tmp_env_file))
    assert secrets.openai_api_key.get_secret_value() == "sk-test-key-12345"
    assert secrets.openai_base_url == "https://api.openai.com/v1"
    assert secrets.email_smtp_host == "smtp.test.com"
    assert secrets.email_smtp_port == 587
    assert secrets.email_smtp_user == "test@test.com"
    assert secrets.email_smtp_password.get_secret_value() == "test-smtp-pass"
    assert secrets.email_imap_host == "imap.test.com"
    assert secrets.email_imap_user == "test@test.com"
    assert secrets.email_imap_password.get_secret_value() == "test-imap-pass"
    assert secrets.moltbook_api_key.get_secret_value() == "mk-test-key-67890"
    assert secrets.moltbook_base_url == "https://moltbook.test.com/api"


# ── 4. SecretStr fields don't expose values via str() ───────────────────


def test_secretstr_fields_are_hidden(tmp_env_file: Path) -> None:
    secrets = Secrets(_env_file=str(tmp_env_file))
    secret_fields = [
        secrets.openai_api_key,
        secrets.email_smtp_password,
        secrets.email_imap_password,
        secrets.moltbook_api_key,
    ]
    for field in secret_fields:
        assert isinstance(field, SecretStr)
        rendered = str(field)
        assert "**********" in rendered
        assert field.get_secret_value() not in rendered


# ── 5. All keys in .env.example have corresponding Secrets fields ───────


def test_env_example_keys_match_secrets_fields() -> None:
    assert ENV_EXAMPLE_PATH.exists(), f".env.example not found at {ENV_EXAMPLE_PATH}"
    text = ENV_EXAMPLE_PATH.read_text()
    keys: list[str] = re.findall(r"^([A-Z_]+)=", text, re.MULTILINE)
    assert len(keys) > 0, ".env.example contains no KEY=value lines"
    secrets_fields = set(Secrets.model_fields.keys())
    for key in keys:
        field_name = key.lower()
        assert field_name in secrets_fields, (
            f".env.example key {key} has no matching Secrets field '{field_name}'"
        )


# ── 6. get_settings() and get_secrets() return singletons ──────────────


def test_get_settings_singleton(mock_settings_yaml: Path) -> None:
    s1 = get_settings(config_path=mock_settings_yaml)
    s2 = get_settings()
    assert s1 is s2


def test_get_secrets_singleton(tmp_env_file: Path) -> None:
    s1 = get_secrets(env_file=tmp_env_file)
    s2 = get_secrets()
    assert s1 is s2


# ── 7. reset_config() clears singletons ────────────────────────────────


def test_reset_config_clears_singletons(
    mock_settings_yaml: Path, tmp_env_file: Path
) -> None:
    settings_a = get_settings(config_path=mock_settings_yaml)
    secrets_a = get_secrets(env_file=tmp_env_file)

    reset_config()

    settings_b = get_settings(config_path=mock_settings_yaml)
    secrets_b = get_secrets(env_file=tmp_env_file)

    assert settings_a is not settings_b
    assert secrets_a is not secrets_b
