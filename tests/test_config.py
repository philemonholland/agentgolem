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
    migrate_settings,
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
    assert settings.sleep_phase_cycle_length == 4
    assert settings.sleep_phase_split == 0.5
    assert settings.sleep_state_top_k == 64
    assert settings.sleep_membrane_decay == 0.75
    assert settings.sleep_consolidation_threshold == 1.1
    assert settings.sleep_dream_threshold == 0.7
    assert settings.sleep_refractory_steps == 3
    assert settings.sleep_stdp_window_steps == 4
    assert settings.sleep_stdp_strength == 0.12
    assert settings.sleep_dream_noise == 0.25
    assert settings.log_level == "DEBUG"
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.llm_discussion_model == "deepseek-reasoner"
    assert settings.llm_code_model == "gpt-5.4"
    assert settings.dry_run_mode is True
    assert settings.google_custom_search_enabled is True
    assert isinstance(settings.data_dir, Path)


# ── 2. Settings uses defaults when no YAML file exists ──────────────────


def test_settings_defaults_when_no_yaml(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "nonexistent.yaml")
    assert settings.data_dir == Path("data")
    assert settings.agent_count == 7
    assert settings.awake_duration_minutes == 10.0
    assert settings.sleep_duration_minutes == 5.0
    assert settings.wind_down_minutes == 2.0
    assert settings.sleep_phase_cycle_length == 6
    assert settings.sleep_phase_split == 0.67
    assert settings.sleep_state_top_k == 128
    assert settings.sleep_membrane_decay == 0.82
    assert settings.sleep_consolidation_threshold == 0.95
    assert settings.sleep_dream_threshold == 0.75
    assert settings.sleep_refractory_steps == 2
    assert settings.sleep_stdp_window_steps == 3
    assert settings.sleep_stdp_strength == 0.08
    assert settings.sleep_dream_noise == 0.18
    assert settings.llm_model == "gpt-5"
    assert settings.llm_discussion_model == "deepseek-reasoner"
    assert settings.llm_code_model == "gpt-5.4"
    assert settings.log_level == "INFO"
    assert settings.dry_run_mode is False
    assert settings.google_custom_search_enabled is False
    assert settings.google_custom_search_hourly_quota == 4
    assert settings.google_custom_search_bucket_capacity == 100
    assert settings.google_custom_search_safe == "active"
    assert settings.approval_required_actions == ["email_send", "moltbook_send"]


# ── 3. Secrets loads from a .env file ───────────────────────────────────


def test_secrets_from_env_file(tmp_env_file: Path) -> None:
    secrets = Secrets(_env_file=str(tmp_env_file))
    assert secrets.openai_api_key.get_secret_value() == "sk-test-key-12345"
    assert secrets.openai_base_url == "https://api.openai.com/v1"
    assert secrets.deepseek_api_key.get_secret_value() == "sk-deepseek-key-54321"
    assert secrets.deepseek_base_url == "https://api.deepseek.com/v1"
    assert secrets.llm_discussion_api_key.get_secret_value() == ""
    assert secrets.llm_discussion_base_url == ""
    assert secrets.llm_code_api_key.get_secret_value() == ""
    assert secrets.llm_code_base_url == ""
    assert secrets.email_smtp_host == "smtp.test.com"
    assert secrets.email_smtp_port == 587
    assert secrets.email_smtp_user == "test@test.com"
    assert secrets.email_smtp_password.get_secret_value() == "test-smtp-pass"
    assert secrets.email_imap_host == "imap.test.com"
    assert secrets.email_imap_user == "test@test.com"
    assert secrets.email_imap_password.get_secret_value() == "test-imap-pass"
    assert secrets.moltbook_api_key.get_secret_value() == "mk-test-key-67890"
    assert secrets.moltbook_base_url == "https://moltbook.test.com/api"
    assert secrets.google_custom_search_api_key.get_secret_value() == "google-search-key"
    assert secrets.google_custom_search_engine_id == "test-engine-id"
    assert secrets.google_oauth_client_id.get_secret_value() == "test-google-client-id"
    assert secrets.google_oauth_client_file == "config/google_oauth_client.json"
    assert secrets.google_oauth_token_file == "data/google/oauth_token.json"


# ── 4. SecretStr fields don't expose values via str() ───────────────────


def test_secretstr_fields_are_hidden(tmp_env_file: Path) -> None:
    secrets = Secrets(_env_file=str(tmp_env_file))
    secret_fields = [
        secrets.openai_api_key,
        secrets.deepseek_api_key,
        secrets.llm_discussion_api_key,
        secrets.llm_code_api_key,
        secrets.email_smtp_password,
        secrets.email_imap_password,
        secrets.moltbook_api_key,
        secrets.google_custom_search_api_key,
        secrets.google_oauth_client_id,
    ]
    for field in secret_fields:
        assert isinstance(field, SecretStr)
        rendered = str(field)
        if field.get_secret_value():
            assert "**********" in rendered
            assert field.get_secret_value() not in rendered
        else:
            assert rendered == ""


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


def test_reset_config_clears_singletons(mock_settings_yaml: Path, tmp_env_file: Path) -> None:
    settings_a = get_settings(config_path=mock_settings_yaml)
    secrets_a = get_secrets(env_file=tmp_env_file)

    reset_config()

    settings_b = get_settings(config_path=mock_settings_yaml)
    secrets_b = get_secrets(env_file=tmp_env_file)

    assert settings_a is not settings_b
    assert secrets_a is not secrets_b


# ── 8. migrate_settings adds missing keys ───────────────────────────────


def test_migrate_settings_adds_missing_keys(tmp_path: Path) -> None:
    """migrate_settings inserts missing keys with defaults."""
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text("data_dir: mydata\nawake_duration_minutes: 5.0\n")

    added = migrate_settings(yaml_path)

    assert len(added) > 0
    assert "data_dir" not in added  # already existed
    assert "awake_duration_minutes" not in added  # already existed
    assert "agent_count" in added  # was missing

    # Verify the file was updated and existing values preserved
    reloaded = load_settings(yaml_path)
    assert str(reloaded.data_dir) == "mydata"  # preserved
    assert reloaded.awake_duration_minutes == 5.0  # preserved
    assert reloaded.agent_count == 7  # added with default


def test_migrate_settings_idempotent(tmp_path: Path) -> None:
    """Running migrate_settings twice produces no new additions the second time."""
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text("data_dir: data\n")

    first_added = migrate_settings(yaml_path)
    assert len(first_added) > 0

    second_added = migrate_settings(yaml_path)
    assert second_added == []


def test_migrate_settings_creates_file(tmp_path: Path) -> None:
    """migrate_settings creates the file if it doesn't exist."""
    yaml_path = tmp_path / "config" / "settings.yaml"
    assert not yaml_path.exists()

    added = migrate_settings(yaml_path)

    assert yaml_path.exists()
    assert len(added) == len(Settings.model_fields)
    reloaded = load_settings(yaml_path)
    assert reloaded.agent_count == 7


# ── 9. repo_root setting ────────────────────────────────────────────────


def test_repo_root_default_empty() -> None:
    """repo_root defaults to empty string (auto-detect)."""
    s = Settings()
    assert s.repo_root == ""


def test_repo_root_configurable(tmp_path: Path) -> None:
    """repo_root can be set to a specific path."""
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(f"repo_root: {tmp_path}\n")
    s = load_settings(yaml_path)
    assert s.repo_root == str(tmp_path)


def test_resolve_repo_root_auto_detect() -> None:
    """resolve_repo_root returns auto-detect when repo_root is empty."""
    from agentgolem.runtime.loop import REPO_ROOT, resolve_repo_root

    s = Settings()
    assert resolve_repo_root(s) == REPO_ROOT


def test_resolve_repo_root_configured(tmp_path: Path) -> None:
    """resolve_repo_root uses the configured path when set."""
    from agentgolem.runtime.loop import resolve_repo_root

    s = Settings(repo_root=str(tmp_path))
    assert resolve_repo_root(s) == tmp_path.resolve()
