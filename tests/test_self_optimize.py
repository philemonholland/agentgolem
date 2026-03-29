"""Tests for agent self-optimisation of settings."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from agentgolem.config.secrets import Secrets
from agentgolem.config.settings import Settings
from agentgolem.runtime.loop import (
    LOCKED_SETTINGS,
    OPTIMIZABLE_SETTINGS,
    MainLoop,
)


def _make_loop(tmp_path: Path) -> MainLoop:
    """Build a minimal MainLoop for testing (no LLM)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for d in (
        "soul_versions", "heartbeat_history", "logs",
        "memory", "memory/snapshots", "approvals",
        "inbox", "outbox", "state",
    ):
        (data_dir / d).mkdir(parents=True, exist_ok=True)
    (data_dir / "soul.md").write_text("# Test Soul\n", encoding="utf-8")
    (data_dir / "heartbeat.md").write_text("# Heartbeat\n", encoding="utf-8")

    settings = Settings(data_dir=data_dir)
    secrets = Secrets(openai_api_key="", openai_base_url="")
    return MainLoop(
        settings=settings,
        secrets=secrets,
        agent_name="TestAgent",
        ethical_vector="testing",
    )


# ------------------------------------------------------------------
# Locked settings cannot be changed
# ------------------------------------------------------------------

class TestLockedSettings:
    """Agents must never be able to change sleep-wake cycle settings."""

    @pytest.mark.parametrize("key", sorted(LOCKED_SETTINGS))
    def test_locked_setting_rejected(self, tmp_path: Path, key: str) -> None:
        loop = _make_loop(tmp_path)
        old_value = getattr(loop._settings, key)
        emitted: list[str] = []
        loop._activity_callback = lambda icon, text: emitted.append(f"{icon} {text}")

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting(key, "999", "test reason")
        )

        assert getattr(loop._settings, key) == old_value, (
            f"Locked setting '{key}' was modified!"
        )
        assert any("BLOCKED" in e or "🔒" in e for e in emitted)

    def test_locked_set_contains_all_cycle_settings(self) -> None:
        expected = {
            "awake_duration_minutes",
            "sleep_duration_minutes",
            "wind_down_minutes",
            "sleep_cycle_minutes",
            "agent_offset_minutes",
            "agent_count",
            "name_discovery_cycles",
        }
        assert LOCKED_SETTINGS == expected


# ------------------------------------------------------------------
# Optimizable settings CAN be changed
# ------------------------------------------------------------------

class TestOptimizableSettings:
    """Agents can change allowed settings within valid ranges."""

    def test_change_float_setting(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        old = loop._settings.quarantine_emotion_threshold
        emitted: list[str] = []
        loop._activity_callback = lambda icon, text: emitted.append(f"{icon} {text}")

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting(
                "quarantine_emotion_threshold", "0.85", "more cautious"
            )
        )

        assert loop._settings.quarantine_emotion_threshold == 0.85
        assert any("OPTIMIZED" in e for e in emitted)

    def test_change_int_setting(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting(
                "browser_rate_limit_per_minute", "30", "faster crawling"
            )
        )

        assert loop._settings.browser_rate_limit_per_minute == 30

    def test_change_bool_setting(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop._settings.dry_run_mode = True

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting(
                "dry_run_mode", "false", "ready for real actions"
            )
        )

        assert loop._settings.dry_run_mode is False

    def test_change_str_setting(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting("log_level", "DEBUG", "need more detail")
        )

        assert loop._settings.log_level == "DEBUG"


# ------------------------------------------------------------------
# Validation rejects out-of-range values
# ------------------------------------------------------------------

class TestValidation:
    """Out-of-range, wrong-type, and unknown settings are rejected."""

    def test_below_min_rejected(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        old = loop._settings.quarantine_emotion_threshold
        emitted: list[str] = []
        loop._activity_callback = lambda icon, text: emitted.append(f"{icon} {text}")

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting(
                "quarantine_emotion_threshold", "-0.5", "bad value"
            )
        )

        assert loop._settings.quarantine_emotion_threshold == old
        assert any("below minimum" in e for e in emitted)

    def test_above_max_rejected(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        old = loop._settings.browser_rate_limit_per_minute
        emitted: list[str] = []
        loop._activity_callback = lambda icon, text: emitted.append(f"{icon} {text}")

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting(
                "browser_rate_limit_per_minute", "9999", "too many"
            )
        )

        assert loop._settings.browser_rate_limit_per_minute == old
        assert any("above maximum" in e for e in emitted)

    def test_invalid_choice_rejected(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        old = loop._settings.log_level
        emitted: list[str] = []
        loop._activity_callback = lambda icon, text: emitted.append(f"{icon} {text}")

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting("log_level", "BANANA", "invalid")
        )

        assert loop._settings.log_level == old
        assert any("not in" in e for e in emitted)

    def test_unknown_setting_rejected(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        emitted: list[str] = []
        loop._activity_callback = lambda icon, text: emitted.append(f"{icon} {text}")

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting("nonexistent_key", "42", "no reason")
        )

        assert any("Unknown" in e for e in emitted)

    def test_noop_when_same_value(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        current = str(loop._settings.quarantine_emotion_threshold)
        emitted: list[str] = []
        loop._activity_callback = lambda icon, text: emitted.append(f"{icon} {text}")

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting(
                "quarantine_emotion_threshold", current, "no change"
            )
        )

        assert any("already has value" in e for e in emitted)
        assert not any("OPTIMIZED" in e for e in emitted)


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------

class TestPersistence:
    """Setting overrides persist to YAML and reload on next startup."""

    def test_overrides_written_to_yaml(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting(
                "browser_timeout_seconds", "45", "testing persistence"
            )
        )

        overrides_path = loop._data_dir / "settings_overrides.yaml"
        assert overrides_path.exists()
        data = yaml.safe_load(overrides_path.read_text(encoding="utf-8"))
        assert data["browser_timeout_seconds"] == 45

    def test_overrides_loaded_on_init(self, tmp_path: Path) -> None:
        # Create overrides file first
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        for d in (
            "soul_versions", "heartbeat_history", "logs",
            "memory", "memory/snapshots", "approvals",
            "inbox", "outbox", "state",
        ):
            (data_dir / d).mkdir(parents=True, exist_ok=True)
        (data_dir / "soul.md").write_text("# Soul\n", encoding="utf-8")
        (data_dir / "heartbeat.md").write_text("# HB\n", encoding="utf-8")

        overrides_path = data_dir / "settings_overrides.yaml"
        overrides_path.write_text(
            yaml.safe_dump({"browser_timeout_seconds": 99}),
            encoding="utf-8",
        )

        settings = Settings(data_dir=data_dir)
        secrets = Secrets(openai_api_key="", openai_base_url="")
        loop = MainLoop(
            settings=settings,
            secrets=secrets,
            agent_name="Reload",
            ethical_vector="testing",
        )

        assert loop._settings.browser_timeout_seconds == 99

    def test_locked_overrides_ignored_on_load(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        for d in (
            "soul_versions", "heartbeat_history", "logs",
            "memory", "memory/snapshots", "approvals",
            "inbox", "outbox", "state",
        ):
            (data_dir / d).mkdir(parents=True, exist_ok=True)
        (data_dir / "soul.md").write_text("# Soul\n", encoding="utf-8")
        (data_dir / "heartbeat.md").write_text("# HB\n", encoding="utf-8")

        # Tamper the overrides file to include a locked setting
        overrides_path = data_dir / "settings_overrides.yaml"
        overrides_path.write_text(
            yaml.safe_dump({"awake_duration_minutes": 999.0}),
            encoding="utf-8",
        )

        settings = Settings(data_dir=data_dir)
        original = settings.awake_duration_minutes
        secrets = Secrets(openai_api_key="", openai_base_url="")
        loop = MainLoop(
            settings=settings,
            secrets=secrets,
            agent_name="Tampered",
            ethical_vector="testing",
        )

        assert loop._settings.awake_duration_minutes == original


# ------------------------------------------------------------------
# Parse helper
# ------------------------------------------------------------------

class TestParseAndOptimize:
    """Test the OPTIMIZE action-line parser."""

    def test_parses_key_value_reason(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        emitted: list[str] = []
        loop._activity_callback = lambda icon, text: emitted.append(f"{icon} {text}")

        asyncio.get_event_loop().run_until_complete(
            loop._parse_and_optimize(
                "browser_timeout_seconds 50 | faster browsing experience"
            )
        )

        assert loop._settings.browser_timeout_seconds == 50
        assert any("OPTIMIZED" in e for e in emitted)

    def test_parses_without_reason(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)

        asyncio.get_event_loop().run_until_complete(
            loop._parse_and_optimize("browser_timeout_seconds 55")
        )

        assert loop._settings.browser_timeout_seconds == 55

    def test_rejects_malformed_input(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        emitted: list[str] = []
        loop._activity_callback = lambda icon, text: emitted.append(f"{icon} {text}")

        asyncio.get_event_loop().run_until_complete(
            loop._parse_and_optimize("just_a_key")
        )

        assert any("Invalid OPTIMIZE format" in e for e in emitted)


# ------------------------------------------------------------------
# Audit logging
# ------------------------------------------------------------------

class TestAuditLogging:
    """All setting changes and blocked attempts are audit-logged."""

    def test_successful_change_audited(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting(
                "browser_timeout_seconds", "42", "audit test"
            )
        )

        log_path = loop._data_dir / "logs" / "audit.jsonl"
        assert log_path.exists()
        import json
        entries = [json.loads(l) for l in log_path.read_text().splitlines() if l]
        optimized = [e for e in entries if e.get("mutation_type") == "setting_optimized"]
        assert len(optimized) == 1
        assert optimized[0]["evidence"]["key"] == "browser_timeout_seconds"
        assert optimized[0]["evidence"]["new_value"] == "42"

    def test_blocked_change_audited(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting(
                "awake_duration_minutes", "999", "blocked test"
            )
        )

        log_path = loop._data_dir / "logs" / "audit.jsonl"
        assert log_path.exists()
        import json
        entries = [json.loads(l) for l in log_path.read_text().splitlines() if l]
        blocked = [e for e in entries if e.get("mutation_type") == "setting_change_blocked"]
        assert len(blocked) == 1
        assert blocked[0]["evidence"]["key"] == "awake_duration_minutes"


# ------------------------------------------------------------------
# Derived value updates
# ------------------------------------------------------------------

class TestDerivedValues:
    """Changing settings updates cached derived values."""

    def test_autonomous_interval_updates(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._autonomous_interval == 15.0  # default

        asyncio.get_event_loop().run_until_complete(
            loop._optimize_setting(
                "autonomous_interval_seconds", "30", "slower ticks"
            )
        )

        assert loop._autonomous_interval == 30.0


# ------------------------------------------------------------------
# Coverage: every optimizable key has valid metadata
# ------------------------------------------------------------------

class TestMetadata:
    """All optimizable settings have valid type and constraint metadata."""

    @pytest.mark.parametrize("key", sorted(OPTIMIZABLE_SETTINGS.keys()))
    def test_has_valid_type(self, key: str) -> None:
        meta = OPTIMIZABLE_SETTINGS[key]
        assert "type" in meta
        assert meta["type"] in (int, float, str, bool)

    def test_no_overlap_with_locked(self) -> None:
        overlap = set(OPTIMIZABLE_SETTINGS.keys()) & LOCKED_SETTINGS
        assert not overlap, f"Settings in both locked and optimizable: {overlap}"

    @pytest.mark.parametrize("key", sorted(OPTIMIZABLE_SETTINGS.keys()))
    def test_setting_exists_on_model(self, key: str) -> None:
        assert hasattr(Settings, key) or key in Settings.model_fields, (
            f"Optimizable setting '{key}' not found on Settings model"
        )
