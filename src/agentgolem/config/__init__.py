"""Configuration management."""
from __future__ import annotations

from pathlib import Path

from agentgolem.config.secrets import Secrets
from agentgolem.config.settings import Settings, load_settings, migrate_settings

_settings: Settings | None = None
_secrets: Secrets | None = None


def get_settings(config_path: Path | None = None) -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings(config_path or Path("config/settings.yaml"))
    return _settings


def get_secrets(env_file: Path | None = None) -> Secrets:
    global _secrets
    if _secrets is None:
        if env_file:
            _secrets = Secrets(_env_file=str(env_file))
        else:
            _secrets = Secrets()
    return _secrets


def reset_config() -> None:
    """Reset singletons (for testing)."""
    global _settings, _secrets
    _settings = None
    _secrets = None


__all__ = [
    "Settings",
    "Secrets",
    "get_settings",
    "get_secrets",
    "reset_config",
    "load_settings",
    "migrate_settings",
]