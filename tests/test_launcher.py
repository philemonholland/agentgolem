"""Tests for run_golem.py — launcher parameter system and /command dispatch."""

from __future__ import annotations

import asyncio
import threading

# Import from the launcher module at repo root
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
import run_golem
from agentgolem.runtime.interrupts import InterruptManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """Patch all file paths to tmp_path."""
    settings_path = tmp_path / "config" / "settings.yaml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    env_path = tmp_path / ".env"
    launcher_path = tmp_path / "data" / "state" / "launcher_state.json"
    launcher_path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(run_golem, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(run_golem, "ENV_PATH", env_path)
    monkeypatch.setattr(run_golem, "LAUNCHER_STATE_PATH", launcher_path)
    monkeypatch.setattr(run_golem, "ROOT", tmp_path)

    return tmp_path


# ---------------------------------------------------------------------------
# ParamStore
# ---------------------------------------------------------------------------


class TestParamStore:
    def test_agent_count_default_tracks_seven_councils(self, tmp_env):
        store = run_golem.ParamStore()
        assert store.get("agent_count", "int") == 7

    def test_get_launcher_defaults(self, tmp_env):
        store = run_golem.ParamStore()
        assert store.get("dashboard_enabled", "bool") is True
        assert store.get("dashboard_host", "str") == "127.0.0.1"
        assert store.get("dashboard_port", "int") == run_golem.DASHBOARD_SAFE_DEFAULT_PORT
        assert store.get("dashboard_auto_open_browser", "bool") is True

    def test_set_persists_launcher_param(self, tmp_env):
        store = run_golem.ParamStore()
        store.set("dashboard_port", 9000, "int")
        assert store.get("dashboard_port", "int") == 9000

        # Reload from disk
        store2 = run_golem.ParamStore()
        assert store2.get("dashboard_port", "int") == 9000

    def test_set_persists_settings_param(self, tmp_env):
        store = run_golem.ParamStore()
        store.set("awake_duration_minutes", 12.0, "float")
        assert store.get("awake_duration_minutes", "float") == 12.0

        # Verify written to yaml
        data = yaml.safe_load((tmp_env / "config" / "settings.yaml").read_text())
        assert data["awake_duration_minutes"] == 12.0

    def test_set_persists_env_param(self, tmp_env):
        store = run_golem.ParamStore()
        store.set("openai_api_key", "sk-test-key-123", "secret")
        assert store.get("openai_api_key", "secret") == "sk-test-key-123"

        # Verify written to .env
        content = (tmp_env / ".env").read_text()
        assert "OPENAI_API_KEY=sk-test-key-123" in content

    def test_get_display_masks_secrets(self, tmp_env):
        store = run_golem.ParamStore()
        store.set("openai_api_key", "sk-test-secret-value-12345", "secret")
        display = store.get_display("openai_api_key", "secret")
        assert "sk-t" in display
        assert "2345" in display
        assert "secret-value" not in display

    def test_get_display_empty_secret(self, tmp_env):
        store = run_golem.ParamStore()
        display = store.get_display("openai_api_key", "secret")
        assert display == "(not set)"

    def test_get_display_list(self, tmp_env):
        store = run_golem.ParamStore()
        store.set("approval_required_actions", ["email_send", "moltbook_send"], "list[str]")
        display = store.get_display("approval_required_actions", "list[str]")
        assert "email_send" in display
        assert "moltbook_send" in display

    def test_runtime_overrides_take_precedence(self, tmp_env):
        store = run_golem.ParamStore()
        store.set("log_level", "DEBUG", "str")
        assert store.get("log_level", "str") == "DEBUG"

        # Override again
        store.set("log_level", "ERROR", "str")
        assert store.get("log_level", "str") == "ERROR"

    def test_reload_into_settings_object(self, tmp_env):
        store = run_golem.ParamStore()
        store.set("awake_duration_minutes", 3.0, "float")
        store.set("llm_model", "gpt-4", "str")
        settings = store.reload_into_settings_object()
        assert settings.awake_duration_minutes == 3.0
        assert settings.llm_model == "gpt-4"

    def test_bool_param_roundtrip(self, tmp_env):
        store = run_golem.ParamStore()
        store.set("email_enabled", True, "bool")
        assert store.get("email_enabled", "bool") is True
        store.set("email_enabled", False, "bool")
        assert store.get("email_enabled", "bool") is False


# ---------------------------------------------------------------------------
# parse_input
# ---------------------------------------------------------------------------


class TestParseInput:
    def test_str(self):
        assert run_golem.parse_input("hello", "str") == "hello"

    def test_int(self):
        assert run_golem.parse_input("42", "int") == 42

    def test_float(self):
        assert run_golem.parse_input("3.14", "float") == 3.14

    def test_bool_true(self):
        assert run_golem.parse_input("true", "bool") is True
        assert run_golem.parse_input("yes", "bool") is True
        assert run_golem.parse_input("1", "bool") is True

    def test_bool_false(self):
        assert run_golem.parse_input("false", "bool") is False
        assert run_golem.parse_input("no", "bool") is False
        assert run_golem.parse_input("0", "bool") is False

    def test_list_str(self):
        result = run_golem.parse_input("email_send, moltbook_send", "list[str]")
        assert result == ["email_send", "moltbook_send"]

    def test_list_str_empty(self):
        result = run_golem.parse_input("", "list[str]")
        assert result == []

    def test_secret(self):
        assert run_golem.parse_input("sk-key", "secret") == "sk-key"

    def test_int_invalid_raises(self):
        with pytest.raises(ValueError):
            run_golem.parse_input("abc", "int")


# ---------------------------------------------------------------------------
# mask_secret
# ---------------------------------------------------------------------------


class TestMaskSecret:
    def test_empty(self):
        assert run_golem.mask_secret("") == "(not set)"

    def test_short(self):
        assert run_golem.mask_secret("1234") == "****"

    def test_long(self):
        masked = run_golem.mask_secret("sk-test-secret-value-12345")
        assert masked.startswith("sk-t")
        assert masked.endswith("2345")
        assert "****" in masked
        assert "secret-value" not in masked


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------


class TestConfigIO:
    def test_load_save_settings(self, tmp_env):
        run_golem.save_settings_dict({"llm_model": "gpt-4", "log_level": "DEBUG"})
        loaded = run_golem.load_settings_dict()
        assert loaded["llm_model"] == "gpt-4"
        assert loaded["log_level"] == "DEBUG"

    def test_load_save_env(self, tmp_env):
        run_golem.save_env_dict({"OPENAI_API_KEY": "sk-test", "EMAIL_SMTP_HOST": "smtp.test"})
        loaded = run_golem.load_env_dict()
        assert loaded["OPENAI_API_KEY"] == "sk-test"
        assert loaded["EMAIL_SMTP_HOST"] == "smtp.test"

    def test_load_env_skips_comments(self, tmp_env):
        env_path = tmp_env / ".env"
        env_path.write_text("# comment\nKEY=val\n\n# another\nKEY2=val2\n")
        loaded = run_golem.load_env_dict()
        assert loaded == {"KEY": "val", "KEY2": "val2"}

    def test_load_save_launcher_state(self, tmp_env):
        run_golem.save_launcher_state({"dashboard_port": 9000})
        loaded = run_golem.load_launcher_state()
        assert loaded["dashboard_port"] == 9000


# ---------------------------------------------------------------------------
# Walkthrough (mocked input)
# ---------------------------------------------------------------------------


class TestWalkthrough:
    def test_accept_all_skips_walkthrough(self, tmp_env):
        """Typing 'y' at the accept-all prompt should skip the full walkthrough."""
        store = run_golem.ParamStore()
        with patch("builtins.input", side_effect=["y"]):
            changed = run_golem.walkthrough(store)
        assert changed is False

    def test_decline_then_all_enter(self, tmp_env):
        """Declining quick-start then pressing Enter for every param = no changes."""
        store = run_golem.ParamStore()
        # 'n' to decline quick-start, then Enter for each param
        inputs = ["n"] + [""] * len(run_golem.PARAM_DEFS)
        with patch("builtins.input", side_effect=inputs):
            changed = run_golem.walkthrough(store)
        assert changed is False

    def test_decline_then_change_one(self, tmp_env):
        """Declining quick-start, then changing one param returns changed=True."""
        store = run_golem.ParamStore()
        param_inputs = []
        for key, _, _, _, _ in run_golem.PARAM_DEFS:
            if key == "log_level":
                param_inputs.append("DEBUG")
            else:
                param_inputs.append("")
        inputs = ["n"] + param_inputs
        with patch("builtins.input", side_effect=inputs):
            changed = run_golem.walkthrough(store)
        assert changed is True
        assert store.get("log_level", "str") == "DEBUG"

    def test_invalid_input_keeps_old(self, tmp_env):
        """Invalid input for int should keep old value."""
        store = run_golem.ParamStore()
        param_inputs = []
        for key, _, _, _ptype, _ in run_golem.PARAM_DEFS:
            if key == "dashboard_port":
                param_inputs.append("not_a_number")
            else:
                param_inputs.append("")
        inputs = ["n"] + param_inputs
        with patch("builtins.input", side_effect=inputs):
            run_golem.walkthrough(store)
        assert store.get("dashboard_port", "int") == run_golem.DASHBOARD_SAFE_DEFAULT_PORT


def test_agent_defs_include_supplementary_council7() -> None:
    council7 = run_golem.AGENT_DEFS[-1]
    assert council7["initial_id"] == "Council-7"
    assert council7["ethical_vector"] == "good-faith adversarialism"


# ---------------------------------------------------------------------------
# /command dispatch
# ---------------------------------------------------------------------------


class TestCommandDispatch:
    """Verify the RuntimeConsole._dispatch_command routing works."""

    def _make_console(self, tmp_env):
        """Build a RuntimeConsole with no live agent."""
        store = run_golem.ParamStore()
        loop = asyncio.new_event_loop()
        console = run_golem.RuntimeConsole(store=store, loop_ref=None, async_loop=loop)
        return console, store, loop

    def test_help_prints(self, tmp_env, capsys):
        console, _, loop = self._make_console(tmp_env)
        console._dispatch_command("/help")
        captured = capsys.readouterr().out
        assert "/status" in captured
        assert "/set" in captured
        assert "/quit" in captured
        loop.close()

    def test_get_known_param(self, tmp_env, capsys):
        console, store, loop = self._make_console(tmp_env)
        store.set("log_level", "DEBUG", "str")
        console._dispatch_command("/get log_level")
        captured = capsys.readouterr().out
        assert "DEBUG" in captured
        loop.close()

    def test_get_unknown_param(self, tmp_env, capsys):
        console, _, loop = self._make_console(tmp_env)
        console._dispatch_command("/get nonexistent_key")
        captured = capsys.readouterr().out
        assert "Unknown parameter" in captured
        loop.close()

    def test_get_param_alias(self, tmp_env, capsys):
        console, store, loop = self._make_console(tmp_env)
        store.set("llm_discussion_model", "deepseek-reasoner", "str")
        console._dispatch_command("/get discussion_model")
        captured = capsys.readouterr().out
        assert "llm_discussion_model" in captured
        assert "deepseek-reasoner" in captured
        loop.close()

    def test_set_known_param(self, tmp_env, capsys):
        console, store, loop = self._make_console(tmp_env)
        console._dispatch_command("/set log_level WARNING")
        assert store.get("log_level", "str") == "WARNING"
        captured = capsys.readouterr().out
        assert "WARNING" in captured
        loop.close()

    def test_set_param_alias(self, tmp_env, capsys):
        console, store, loop = self._make_console(tmp_env)
        console._dispatch_command("/set code_model gpt-4.1")
        assert store.get("llm_code_model", "str") == "gpt-4.1"
        captured = capsys.readouterr().out
        assert "llm_code_model" in captured
        loop.close()

    def test_set_unknown_param(self, tmp_env, capsys):
        console, _, loop = self._make_console(tmp_env)
        console._dispatch_command("/set fake_param 123")
        captured = capsys.readouterr().out
        assert "Unknown parameter" in captured
        loop.close()

    def test_params_lists_all(self, tmp_env, capsys):
        console, _, loop = self._make_console(tmp_env)
        console._dispatch_command("/params")
        captured = capsys.readouterr().out
        assert "awake_duration_minutes" in captured
        assert "dashboard_port" in captured
        loop.close()

    def test_dashboard_shows_url(self, tmp_env, capsys):
        console, _, loop = self._make_console(tmp_env)
        with patch("webbrowser.open_new_tab"):
            console._dispatch_command("/dashboard")
        captured = capsys.readouterr().out
        assert "127.0.0.1" in captured
        assert str(run_golem.DASHBOARD_SAFE_DEFAULT_PORT) in captured
        loop.close()

    def test_private_message_command_targets_council_number(self, tmp_env, monkeypatch):
        store = run_golem.ParamStore()
        loop = asyncio.new_event_loop()
        agents = [
            SimpleNamespace(
                agent_name="Aurora",
                _initial_agent_name="Council-1",
                interrupt_manager=InterruptManager(),
                _conversation_paused=False,
            ),
            SimpleNamespace(
                agent_name="Basil",
                _initial_agent_name="Council-2",
                interrupt_manager=InterruptManager(),
                _conversation_paused=False,
            ),
        ]
        console = run_golem.RuntimeConsole(
            store=store,
            loop_ref=None,
            async_loop=loop,
            agents=agents,
            human_speaking_event=threading.Event(),
            transient_pause_event=threading.Event(),
        )

        class _ImmediateFuture:
            def result(self, timeout=None):
                return None

        def _run_sync(coro, async_loop):
            async_loop.run_until_complete(coro)
            return _ImmediateFuture()

        monkeypatch.setattr(run_golem.asyncio, "run_coroutine_threadsafe", _run_sync)

        console._dispatch_command("/a 2 hello there")

        assert agents[0].interrupt_manager.has_messages() is False
        assert agents[1].interrupt_manager.has_messages() is True
        loop.close()

    def test_bare_text_queues_one_natural_responder(self, tmp_env, monkeypatch):
        store = run_golem.ParamStore()
        loop = asyncio.new_event_loop()
        agents = [
            SimpleNamespace(
                agent_name="Council-1",
                _initial_agent_name="Council-1",
                interrupt_manager=InterruptManager(),
                _conversation_paused=False,
            ),
            SimpleNamespace(
                agent_name="Council-2",
                _initial_agent_name="Council-2",
                interrupt_manager=InterruptManager(),
                _conversation_paused=False,
            ),
        ]
        bus = SimpleNamespace(floor_holder="Council-2", recommend_responder=lambda: "Council-1")
        console = run_golem.RuntimeConsole(
            store=store,
            loop_ref=None,
            async_loop=loop,
            agents=agents,
            bus=bus,
            human_speaking_event=threading.Event(),
            transient_pause_event=threading.Event(),
        )

        class _ImmediateFuture:
            def result(self, timeout=None):
                return None

        def _run_sync(coro, async_loop):
            async_loop.run_until_complete(coro)
            return _ImmediateFuture()

        monkeypatch.setattr(run_golem.asyncio, "run_coroutine_threadsafe", _run_sync)

        console._cmd_message_all("join in")

        assert agents[0].interrupt_manager.has_messages() is False
        assert agents[1].interrupt_manager.has_messages() is True
        assert all(agent._conversation_paused for agent in agents)
        loop.close()

    def test_status_without_agent(self, tmp_env, capsys):
        console, _, loop = self._make_console(tmp_env)
        console._dispatch_command("/status")
        captured = capsys.readouterr().out
        assert "No agents running" in captured or "not started" in captured
        loop.close()

    def test_unknown_command(self, tmp_env, capsys):
        console, _, loop = self._make_console(tmp_env)
        console._dispatch_command("/banana")
        captured = capsys.readouterr().out
        assert "Unknown command" in captured
        assert "/help" in captured
        loop.close()


# ---------------------------------------------------------------------------
# PARAM_DEFS completeness
# ---------------------------------------------------------------------------


class TestParamDefs:
    def test_all_settings_yaml_keys_covered(self):
        """Every key in Settings model should have a PARAM_DEF entry."""
        from agentgolem.config.settings import Settings

        settings_fields = set(Settings.model_fields.keys())
        param_keys = {
            key
            for key, _, _, ptype, _ in run_golem.PARAM_DEFS
            if ptype not in ("secret", "str_env", "int_env")
        }
        launcher_keys = set(run_golem.LAUNCHER_DEFAULTS.keys())
        covered = param_keys | launcher_keys
        missing = settings_fields - covered
        assert missing == set(), f"Settings fields missing from PARAM_DEFS: {missing}"

    def test_all_env_keys_covered(self):
        """Every secret in Secrets model should have a PARAM_DEF entry."""
        from agentgolem.config.secrets import Secrets

        secret_fields = set(Secrets.model_fields.keys())
        env_param_keys = set(run_golem.ENV_KEY_MAP.keys())
        missing = secret_fields - env_param_keys
        assert missing == set(), f"Secrets fields missing from PARAM_DEFS: {missing}"

    def test_no_duplicate_keys(self):
        keys = [key for key, _, _, _, _ in run_golem.PARAM_DEFS]
        assert len(keys) == len(set(keys)), "Duplicate keys in PARAM_DEFS"

    def test_groups_are_defined(self):
        groups = {group for _, _, _, _, group in run_golem.PARAM_DEFS}
        expected = {
            "Identity",
            "Sleep",
            "LLM",
            "Logging",
            "Communication",
            "Niscalajyoti",
            "Retention",
            "Quarantine",
            "Browser",
            "Consciousness",
            "Dashboard",
            "Secrets",
            "Swarm",
            "Ethical Foundation",
            "LLM Inference",
        }
        assert groups == expected

    def test_llm_inference_params_exist(self):
        """New LLM inference parameters must all be in PARAM_DEFS."""
        keys = {key for key, _, _, _, _ in run_golem.PARAM_DEFS}
        expected_llm = {
            "llm_temperature",
            "llm_top_p",
            "llm_frequency_penalty",
            "llm_presence_penalty",
            "discussion_target_paragraphs",
            "reflection_max_tokens",
            "encoding_max_tokens",
        }
        missing = expected_llm - keys
        assert missing == set(), f"Missing LLM params: {missing}"

    def test_new_settings_have_defaults(self):
        """All new LLM inference settings should have defaults in Settings."""
        from agentgolem.config.settings import Settings
        s = Settings()
        assert s.llm_temperature == 0.7
        assert s.llm_top_p == 1.0
        assert s.llm_frequency_penalty == 0.0
        assert s.llm_presence_penalty == 0.0
        assert s.discussion_target_paragraphs == 5
        assert s.reflection_max_tokens == 1024
        assert s.encoding_max_tokens == 16384
        assert s.discussion_max_completion_tokens == 2048

    def test_help_text_documents_all_slash_commands(self):
        """The HELP_TEXT constant must mention every /command."""
        required = [
            "/help",
            "/status",
            "/params",
            "/get",
            "/set",
            "/wake",
            "/sleep",
            "/pause",
            "/resume",
            "/heartbeat",
            "/soul",
            "/logs",
            "/dashboard",
            "/a",
            "/restart",
            "/quit",
            "/exit",
        ]
        for cmd in required:
            assert cmd in run_golem.HELP_TEXT, f"{cmd} missing from HELP_TEXT"
