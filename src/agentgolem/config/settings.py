"""Non-secret configuration from settings.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Settings(BaseModel):
    data_dir: Path = Path("data")
    awake_duration_minutes: float = 15.0
    sleep_duration_minutes: float = 60.0
    wind_down_minutes: float = 1.0
    soul_update_min_confidence: float = 0.7
    sleep_cycle_minutes: float = 5.0
    sleep_max_nodes_per_cycle: int = 100
    sleep_max_time_ms: int = 5000
    llm_provider: str = "openai"
    llm_model: str = "gpt-5-mini"
    log_level: str = "INFO"
    email_enabled: bool = False
    moltbook_enabled: bool = False
    dry_run_mode: bool = True
    approval_required_actions: list[str] = Field(
        default_factory=lambda: ["email_send", "moltbook_send"]
    )
    niscalajyoti_revisit_hours: float = 168.0
    retention_archive_days: int = 30
    retention_purge_days: int = 90
    retention_min_trust_useful: float = 0.1
    retention_min_centrality: float = 0.05
    retention_promote_min_accesses: int = 10
    retention_promote_min_trust_useful: float = 0.5
    quarantine_emotion_threshold: float = 0.7
    quarantine_trust_useful_threshold: float = 0.3
    browser_rate_limit_per_minute: int = 10
    browser_timeout_seconds: int = 30
    llm_request_delay_seconds: float = 3.0

    # Multi-agent swarm
    agent_count: int = 6
    agent_offset_minutes: float = 1.0
    autonomous_interval_seconds: float = 15.0
    name_discovery_cycles: int = 4
    peer_checkin_interval_minutes: float = 10.0


def load_settings(config_path: Path = Path("config/settings.yaml")) -> Settings:
    """Load settings from YAML file, falling back to defaults."""
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return Settings(**data)
    return Settings()
