"""Non-secret configuration from settings.yaml."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

_HOURS_PER_DAY = 24


def _migrate_legacy_retention_fields(data: dict) -> list[str]:
    migrated: list[str] = []
    legacy_map = {
        "retention_archive_days": "retention_archive_hours",
        "retention_purge_days": "retention_purge_hours",
    }
    for old_key, new_key in legacy_map.items():
        if old_key in data:
            if new_key not in data:
                data[new_key] = float(data[old_key]) * _HOURS_PER_DAY
                migrated.append(new_key)
            del data[old_key]
    return migrated


class Settings(BaseModel):
    data_dir: Path = Path("data")
    awake_duration_minutes: float = 10.0
    sleep_duration_minutes: float = 5.0
    wind_down_minutes: float = 2.0
    soul_update_min_confidence: float = 0.7
    sleep_cycle_minutes: float = 5.0
    sleep_max_nodes_per_cycle: int = 1000
    sleep_max_time_ms: int = 5000
    sleep_phase_cycle_length: int = 6
    sleep_phase_split: float = 0.67
    sleep_state_top_k: int = 128
    sleep_membrane_decay: float = 0.82
    sleep_consolidation_threshold: float = 0.95
    sleep_dream_threshold: float = 0.75
    sleep_refractory_steps: int = 2
    sleep_stdp_window_steps: int = 3
    sleep_stdp_strength: float = 0.08
    sleep_dream_noise: float = 0.18
    llm_provider: str = "openai"
    llm_model: str = "gpt-5"
    llm_discussion_model: str = "deepseek-reasoner"
    log_level: str = "INFO"
    email_enabled: bool = False
    moltbook_enabled: bool = False
    google_custom_search_enabled: bool = False
    google_gmail_api_enabled: bool = False
    google_drive_api_enabled: bool = False
    dry_run_mode: bool = False
    approval_required_actions: list[str] = Field(
        default_factory=lambda: ["email_send", "moltbook_send"]
    )
    niscalajyoti_revisit_hours: float = 6.0
    calibration_interval_hours: float = 24.0
    calibration_heartbeat_cadence: int = 3  # run calibration every N heartbeats
    retention_archive_hours: float = 120.0
    retention_purge_hours: float = 720.0
    retention_min_trust_useful: float = 0.1
    retention_min_centrality: float = 0.05
    retention_promote_min_accesses: int = 10
    retention_promote_min_trust_useful: float = 0.5
    quarantine_emotion_threshold: float = 0.7
    quarantine_trust_useful_threshold: float = 0.3
    browser_rate_limit_per_minute: int = 10
    browser_timeout_seconds: int = 20
    autonomous_browse_max_depth: int = 5
    google_custom_search_default_num_results: int = 5
    google_custom_search_hourly_quota: int = 4
    google_custom_search_bucket_capacity: int = 100
    google_custom_search_safe: str = "active"
    llm_request_delay_seconds: float = 3.0

    # Multi-agent swarm
    agent_count: int = 7
    agent_offset_minutes: float = 0.0
    autonomous_interval_seconds: float = 60.0
    name_discovery_cycles: int = 4
    peer_checkin_interval_minutes: float = 30.0
    peer_message_max_chars: int = 3000
    discussion_max_completion_tokens: int = 2048
    reflection_max_tokens: int = 1024
    encoding_max_tokens: int = 16384
    discussion_target_paragraphs: int = 5
    discussion_transcript_max_entries: int = 30
    llm_code_model: str = "gpt-5.4"

    # LLM inference parameters
    llm_temperature: float = 0.7
    llm_top_p: float = 1.0
    llm_frequency_penalty: float = 0.0
    llm_presence_penalty: float = 0.0

    # Workspace boundary (empty = auto-detect from module location)
    repo_root: str = ""

    # Consciousness kernel
    metacognition_interval: int = 3
    narrative_synthesis_interval: int = 15
    self_model_rebuild_interval: int = 10
    attention_influence_weight: float = 0.7
    internal_state_mycelium_share: bool = True
    metacognition_novelty_bias: float = 0.3

    # Dashboard tuning
    dashboard_refresh_interval_seconds: int = 5
    dashboard_recent_change_seconds: int = 60
    dashboard_dialogue_limit: int = 10
    dashboard_activity_limit: int = 6
    dashboard_settings_history_limit: int = 25

    # LLM provider registry — map provider names to base URLs.
    # API keys are stored in .env as <PROVIDER>_API_KEY (upper-cased).
    # Example: {"openai": "https://api.openai.com/v1", "deepseek": "https://api.deepseek.com/v1"}
    llm_providers: dict[str, str] = Field(default_factory=lambda: {
        "openai": "https://api.openai.com/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
    })
    # Which provider to use for each LLM route (must be a key in llm_providers).
    # Empty string = auto-detect from legacy env vars.
    llm_discussion_provider: str = ""
    llm_code_provider: str = ""

    # Autonomy: attention, goals, outcomes
    tool_failure_escalation_threshold: int = 2
    max_active_goals: int = 3
    goal_stale_ticks: int = 10
    team_idle_nudge_ticks: int = 5

    @model_validator(mode="before")
    @classmethod
    def _convert_legacy_retention_units(cls, value: object) -> object:
        if isinstance(value, Mapping):
            data = dict(value)
            _migrate_legacy_retention_fields(data)
            return data
        return value


def load_settings(config_path: Path = Path("config/settings.yaml")) -> Settings:
    """Load settings from YAML file, falling back to defaults."""
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return Settings(**data)
    return Settings()


def migrate_settings(config_path: Path = Path("config/settings.yaml")) -> list[str]:
    """Ensure settings.yaml contains all keys from the Settings model.

    Compares the on-disk YAML against ``Settings.model_fields``. Any key
    present in the model but missing from the file is inserted with its
    default value.  Existing values are never overwritten.

    Returns the list of newly added key names (empty if nothing changed).
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            data: dict = yaml.safe_load(f) or {}
    else:
        data = {}

    added: list[str] = []
    added.extend(_migrate_legacy_retention_fields(data))
    defaults = Settings()

    for field_name in Settings.model_fields:
        if field_name not in data:
            value = getattr(defaults, field_name)
            # Convert Path to string for YAML serialisation
            if isinstance(value, Path):
                value = str(value)
            data[field_name] = value
            added.append(field_name)

    if added:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return added
