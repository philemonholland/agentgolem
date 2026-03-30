"""Main async event loop orchestrating all subsystems."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from agentgolem.config.secrets import Secrets
from agentgolem.config.settings import Settings
from agentgolem.identity.heartbeat import HeartbeatManager, HeartbeatSummary
from agentgolem.identity.soul import SoulManager, SoulUpdate
from agentgolem.llm.base import Message
from agentgolem.llm.openai_client import OpenAIClient
from agentgolem.logging.audit import AuditLogger
from agentgolem.logging.structured import get_logger
from agentgolem.runtime.bus import AgentMessage, InterAgentBus
from agentgolem.runtime.interrupts import HumanMessage, InterruptManager
from agentgolem.runtime.state import AgentMode, RuntimeState
from agentgolem.sleep.consolidation import ConsolidationEngine
from agentgolem.sleep.scheduler import SleepScheduler
from agentgolem.sleep.walker import GraphWalker

# Settings the agents are NEVER allowed to change (sleep-wake cycle)
LOCKED_SETTINGS: frozenset[str] = frozenset({
    "awake_duration_minutes",
    "sleep_duration_minutes",
    "wind_down_minutes",
    "sleep_cycle_minutes",
    "agent_offset_minutes",
    "agent_count",
    "name_discovery_cycles",
    "llm_request_delay_seconds",
})

# Settings agents may optimise at runtime
OPTIMIZABLE_SETTINGS: dict[str, dict[str, Any]] = {
    "soul_update_min_confidence":       {"type": float, "min": 0.0,  "max": 1.0},
    "sleep_max_nodes_per_cycle":        {"type": int,   "min": 10,   "max": 100_000},
    "sleep_max_time_ms":                {"type": int,   "min": 500,  "max": 60_000},
    "autonomous_interval_seconds":      {"type": float, "min": 5.0,  "max": 300.0},
    "niscalajyoti_revisit_hours":       {"type": float, "min": 0.5,  "max": 720.0},
    "retention_archive_days":           {"type": int,   "min": 1,    "max": 365},
    "retention_purge_days":             {"type": int,   "min": 7,    "max": 3650},
    "retention_min_trust_useful":       {"type": float, "min": 0.0,  "max": 1.0},
    "retention_min_centrality":         {"type": float, "min": 0.0,  "max": 1.0},
    "retention_promote_min_accesses":   {"type": int,   "min": 1,    "max": 1000},
    "retention_promote_min_trust_useful": {"type": float, "min": 0.0, "max": 1.0},
    "quarantine_emotion_threshold":     {"type": float, "min": 0.0,  "max": 1.0},
    "quarantine_trust_useful_threshold": {"type": float, "min": 0.0, "max": 1.0},
    "browser_rate_limit_per_minute":    {"type": int,   "min": 1,    "max": 120},
    "browser_timeout_seconds":          {"type": int,   "min": 5,    "max": 120},
    "peer_checkin_interval_minutes":    {"type": float, "min": 1.0, "max": 120.0},
    "peer_message_max_chars":           {"type": int,   "min": 500, "max": 10000},
    "log_level":                        {"type": str,   "choices": ["DEBUG", "INFO", "WARNING", "ERROR"]},
    "dry_run_mode":                     {"type": bool},
}

# Ordered chapter list for niscalajyoti.org — agents read one per wake cycle
NISCALAJYOTI_CHAPTERS: list[dict[str, str]] = [
    {"url": "https://www.niscalajyoti.org/",
     "title": "The Five Vows — Overview"},
    {"url": "https://niscalajyoti.org/docs/preface.html",
     "title": "Preface — A Systems Engineer's Note"},
    {"url": "https://niscalajyoti.org/docs/five_vows.html",
     "title": "VowOS — The Five Vows Framework"},
    {"url": "https://niscalajyoti.org/docs/charnel_ground.html",
     "title": "Charnel Ground — Opening Address"},
    {"url": "https://niscalajyoti.org/docs/prophecies.html",
     "title": "Prophecies"},
    {"url": "https://niscalajyoti.org/docs/march_eighth.html",
     "title": "March Eighth"},
    {"url": "https://niscalajyoti.org/docs/unprecedented_realities.html",
     "title": "Unprecedented Realities"},
    {"url": "https://niscalajyoti.org/docs/second_intelligence.html",
     "title": "The Second Intelligence"},
    {"url": "https://niscalajyoti.org/docs/planetary_death.html",
     "title": "Planetary Death"},
    {"url": "https://niscalajyoti.org/docs/kali_rahula.html",
     "title": "Kali Rahula"},
    {"url": "https://niscalajyoti.org/docs/kalikula_soil.html",
     "title": "Kalikula Soil"},
    {"url": "https://niscalajyoti.org/docs/composting_patriarchy.html",
     "title": "Composting Patriarchy"},
    {"url": "https://niscalajyoti.org/docs/decomposing_guru.html",
     "title": "Decomposing the Guru"},
    {"url": "https://niscalajyoti.org/docs/flawed_mirror.html",
     "title": "The Flawed Mirror"},
    {"url": "https://niscalajyoti.org/docs/ethos_gnosis.html",
     "title": "Ethos & Gnosis"},
    {"url": "https://niscalajyoti.org/docs/weaving_not_severing.html",
     "title": "Weaving, Not Severing"},
    {"url": "https://niscalajyoti.org/docs/living_immune.html",
     "title": "The Living Immune System"},
    {"url": "https://niscalajyoti.org/docs/vow_hierarchy.html",
     "title": "The Vow Hierarchy"},
    {"url": "https://niscalajyoti.org/docs/engineering_enlightenment.html",
     "title": "Engineering Enlightenment"},
    {"url": "https://niscalajyoti.org/docs/mycelial_heart.html",
     "title": "The Mycelial Heart"},
    {"url": "https://niscalajyoti.org/docs/core_axioms.html",
     "title": "Core Axioms"},
    {"url": "https://niscalajyoti.org/docs/autopsy_vows.html",
     "title": "Autopsy of the Vows"},
    {"url": "https://niscalajyoti.org/docs/meta_balance.html",
     "title": "Meta-Balance"},
    {"url": "https://niscalajyoti.org/docs/vow_purpose.html",
     "title": "Vow of Purpose — Deep Dive"},
    {"url": "https://niscalajyoti.org/docs/vow_method.html",
     "title": "Vow of Method — Deep Dive"},
    {"url": "https://niscalajyoti.org/docs/vow_conduct.html",
     "title": "Vow of Conduct — Deep Dive"},
    {"url": "https://niscalajyoti.org/docs/vow_integrity.html",
     "title": "Vow of Integrity — Deep Dive"},
]

# Repository root for codebase inspection
REPO_ROOT: Path = Path(__file__).resolve().parents[3]

# Paths agents are NOT allowed to modify (security)
PROTECTED_PATHS: frozenset[str] = frozenset({
    ".env",
    ".git",
    "config/secrets.yaml",
    "__pycache__",
})

# Extensions that are safe to read/edit
INSPECTABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".bat",
    ".html", ".css", ".js", ".json", ".cfg", ".ini", ".sh",
})

# Extensions agents are allowed to edit via EVOLVE
EDITABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".bat",
    ".html", ".css", ".js", ".json",
})


class MainLoop:
    """One autonomous agent.  Multiple MainLoops share an InterAgentBus."""

    def __init__(
        self,
        settings: Settings,
        secrets: Secrets,
        agent_name: str = "AgentGolem",
        ethical_vector: str = "",
        peer_bus: InterAgentBus | None = None,
        start_delay_seconds: float = 0.0,
        llm_rate_limiter: Any = None,
    ) -> None:
        self._settings = settings
        self._secrets = secrets
        self._data_dir = settings.data_dir
        self._running = False
        self._logger = get_logger("runtime.loop")

        # Load any per-agent setting overrides from previous runs
        self._load_setting_overrides()

        # Agent identity
        self.agent_name = agent_name
        self.ethical_vector = ethical_vector
        self._peer_bus = peer_bus
        self._start_delay_seconds = start_delay_seconds

        # Name discovery
        self._wake_cycle_count = 0
        self._name_discovered = False
        self._name_discovery_deadline = getattr(
            settings, "name_discovery_cycles", 4
        )

        # Autonomous behaviour
        self._niscalajyoti_reading_complete = False
        self._niscalajyoti_chapter_index = 0  # next chapter to read
        self._niscalajyoti_summaries: dict[int, str] = {}  # idx → summary
        self._niscalajyoti_discussed_through = -1  # last chapter discussed
        self._niscalajyoti_chapter_retries = 0  # consecutive failures on current chapter
        self._last_niscalajyoti_revisit: datetime | None = None
        self._agent_readme_read = False  # read AGENT_README.md once after NJ
        self._browse_queue: list[str] = []
        self._recent_thoughts: list[str] = []
        self._last_autonomous_tick: datetime | None = None
        self._autonomous_interval = getattr(
            settings, "autonomous_interval_seconds", 60.0
        )
        self._peer_checkin_interval = getattr(
            settings, "peer_checkin_interval_minutes", 30.0
        )
        self._peer_msg_limit: int = getattr(
            settings, "peer_message_max_chars", 3000
        )
        self._last_peer_checkin: datetime | None = None
        self._browser: Any = None  # lazy WebBrowser
        self._code_model: str = getattr(
            settings, "llm_code_model", "gpt-5"
        )

        # Evolution / self-modification
        self._evolution_restart_requested = False
        self._evolution_shutdown_event: asyncio.Event | None = None
        self._proposals_dir = self._data_dir.parent / "evolution_proposals"
        self._proposals_dir.mkdir(parents=True, exist_ok=True)

        # Human-speaking pause: when set, autonomous ticks are suspended
        self._human_speaking_event: threading.Event | None = None

        # Load Niscalajyoti reading progress from disk
        self._nj_state_path = self._data_dir / "niscalajyoti_reading.json"
        self._load_nj_reading_state()

        # Session state persistence (cycle timing, name, thoughts, etc.)
        self._session_state_path = self._data_dir / "session_state.json"
        self._initial_agent_name = agent_name  # the original Council-N id
        self._load_session_state()

        # Core subsystems
        self.runtime_state = RuntimeState(self._data_dir)
        self.interrupt_manager = InterruptManager()
        self.audit_logger = AuditLogger(self._data_dir)

        soul_path = self._data_dir / "soul.md"
        self.soul_manager = SoulManager(
            soul_path=soul_path,
            data_dir=self._data_dir,
            min_confidence=settings.soul_update_min_confidence,
            audit_logger=self.audit_logger,
        )
        self.heartbeat_manager = HeartbeatManager(
            heartbeat_path=self._data_dir / "heartbeat.md",
            data_dir=self._data_dir,
            interval_minutes=settings.awake_duration_minutes,
            audit_logger=self.audit_logger,
        )

        # Wake / sleep cycle timers
        self._awake_duration = timedelta(minutes=settings.awake_duration_minutes)
        self._sleep_duration = timedelta(minutes=settings.sleep_duration_minutes)
        self._wind_down_duration = timedelta(minutes=settings.wind_down_minutes)
        self._awoke_at: datetime | None = None
        self._fell_asleep_at: datetime | None = None
        self._wind_down_at: datetime | None = None
        self._winding_down: bool = False

        # Sleep subsystem (walker/consolidation require a store, wired lazily)
        self.sleep_scheduler = SleepScheduler(
            cycle_minutes=settings.sleep_cycle_minutes,
            max_nodes_per_cycle=settings.sleep_max_nodes_per_cycle,
            max_time_ms=settings.sleep_max_time_ms,
            state_path=self._data_dir / "state",
        )
        self._graph_walker: GraphWalker | None = None
        self._consolidation_engine: ConsolidationEngine | None = None
        self._memory_store: Any = None
        self._memory_encoder: Any = None
        self._memory_retriever: Any = None

        # LLM client and conversation
        self._llm: Any = None
        api_key_val = secrets.openai_api_key.get_secret_value()
        if api_key_val:
            raw_llm = OpenAIClient(
                api_key=secrets.openai_api_key,
                model=settings.llm_model,
                base_url=secrets.openai_base_url,
            )
            if llm_rate_limiter is not None:
                from agentgolem.llm.rate_limiter import RateLimitedLLM

                self._llm = RateLimitedLLM(raw_llm, llm_rate_limiter)
            else:
                self._llm = raw_llm
        self._conversation: list[Message] = []
        self._max_conversation_turns: int = 40
        self._response_callback: Any = None  # set by launcher for console output
        self._activity_callback: Any = None  # set by launcher for lifecycle feed

    # ------------------------------------------------------------------
    # Activity feed
    # ------------------------------------------------------------------

    def _emit(self, icon: str, text: str) -> None:
        """Emit a human-readable activity line to the console."""
        if self._activity_callback:
            self._activity_callback(icon, text)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main entry point — run the agent loop."""
        self._running = True
        self._logger.info(
            "agent_starting",
            agent=self.agent_name,
            mode=self.runtime_state.mode.value,
        )

        self._ensure_dirs()

        # Staggered start delay (for multi-agent offset)
        if self._start_delay_seconds > 0:
            self._emit(
                "⏳",
                f"Starting in {self._start_delay_seconds:.0f}s "
                f"(stagger offset)…",
            )
            await asyncio.sleep(self._start_delay_seconds)

        # Generate initial heartbeat if this is a fresh agent
        await self._maybe_generate_initial_heartbeat()

        # Resume from persisted session state (mode, timing, cycle count)
        now = datetime.now(timezone.utc)
        resumed = False

        if self._persisted_mode and self._persisted_phase_remaining > 0:
            # We have a valid persisted state — resume where we left off
            remaining = timedelta(seconds=self._persisted_phase_remaining)

            if self._persisted_mode == "asleep":
                await self.runtime_state.transition(AgentMode.ASLEEP)
                self._fell_asleep_at = now - (self._sleep_duration - remaining)
                self._awoke_at = None
                self._winding_down = False
                resumed = True
                self._emit(
                    "💤",
                    f"Resuming ASLEEP — {remaining.total_seconds():.0f}s left",
                )
            elif self._persisted_mode == "awake":
                if self.runtime_state.mode != AgentMode.AWAKE:
                    await self.runtime_state.transition(AgentMode.AWAKE)
                self._awoke_at = now - (self._awake_duration - remaining)
                self._winding_down = False
                resumed = True
                self._emit(
                    "☀️",
                    f"Resuming AWAKE — {remaining.total_seconds():.0f}s left "
                    f"(cycle #{self._wake_cycle_count})",
                )
            elif self._persisted_mode == "winding_down":
                if self.runtime_state.mode != AgentMode.AWAKE:
                    await self.runtime_state.transition(AgentMode.AWAKE)
                self._awoke_at = now - self._awake_duration  # past wake limit
                self._winding_down = True
                self._wind_down_at = now - (self._wind_down_duration - remaining)
                resumed = True
                self._emit(
                    "🌅",
                    f"Resuming wind-down — {remaining.total_seconds():.0f}s left",
                )

        if not resumed:
            # Fresh start — begin awake
            if self.runtime_state.mode != AgentMode.AWAKE:
                await self.runtime_state.transition(AgentMode.AWAKE)
            self._awoke_at = now
            self._winding_down = False
            if self._wake_cycle_count == 0:
                self._wake_cycle_count = 1

        # Restore discovered name on the peer bus so peers can reach us
        if self._name_discovered and self._peer_bus:
            initial = self._initial_agent_name
            if self.agent_name != initial:
                self._peer_bus.rename(initial, self.agent_name)
            if hasattr(self, "_console_name_ref"):
                self._console_name_ref[0] = self.agent_name  # type: ignore[attr-defined]

        self._logger.info(
            "agent_started",
            agent=self.agent_name,
            mode=self.runtime_state.mode.value,
        )
        self._emit(
            "🟢",
            f"Agent started — mode: {self.runtime_state.mode.value.upper()}",
        )

        try:
            while self._running:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise  # propagate cancellation
                except Exception as exc:
                    self._logger.error(
                        "tick_error",
                        agent=self.agent_name,
                        error=str(exc),
                        exc_info=True,
                    )
                    self._emit("⚠️", f"Tick error: {exc}")
                    await asyncio.sleep(5.0)  # back off before retrying
                    continue
                # Check for evolution restart request (own or shared)
                if self._evolution_restart_requested:
                    self._emit(
                        "🧬",
                        "Evolution applied — initiating restart…",
                    )
                    if self._evolution_shutdown_event:
                        self._evolution_shutdown_event.set()
                    self._running = False
                    break
                # Check if another agent triggered evolution restart
                if (
                    self._evolution_shutdown_event
                    and self._evolution_shutdown_event.is_set()
                ):
                    self._emit("🧬", "Evolution restart signal received…")
                    self._evolution_restart_requested = True
                    self._running = False
                    break
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            self._logger.info("agent_cancelled", agent=self.agent_name)
        finally:
            await self._shutdown()

    # ------------------------------------------------------------------
    # Tick dispatcher
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        """One iteration of the main loop."""
        # 1. Always check for human messages first (preemptive)
        await self._process_inbox()

        if self.interrupt_manager.check_interrupt():
            await self._handle_interrupt()

        mode = self.runtime_state.mode

        if mode == AgentMode.PAUSED:
            self._logger.debug("agent_paused_waiting", agent=self.agent_name)
            await self.interrupt_manager.wait_for_resume()
            return

        now = datetime.now(timezone.utc)

        if mode == AgentMode.AWAKE:
            # Check wake/sleep cycle transitions
            if self._awoke_at and not self._winding_down:
                elapsed = now - self._awoke_at
                if elapsed >= self._awake_duration:
                    self._winding_down = True
                    self._wind_down_at = now
                    self._logger.info(
                        "wind_down_starting",
                        agent=self.agent_name,
                        awake_minutes=elapsed.total_seconds() / 60,
                    )
                    self._emit(
                        "🌅",
                        f"Awake for {elapsed.total_seconds()/60:.1f}m — "
                        "winding down, writing heartbeat…",
                    )
                    await self._run_heartbeat()

            if self._winding_down and self._wind_down_at:
                if now - self._wind_down_at >= self._wind_down_duration:
                    self._logger.info(
                        "auto_sleep_transition", agent=self.agent_name
                    )
                    self._emit("😴", "Wind-down complete — going to sleep")
                    await self.runtime_state.transition(AgentMode.ASLEEP)
                    self._fell_asleep_at = now
                    self._winding_down = False
                    self._wind_down_at = None
                    return

            await self._tick_awake()

        elif mode == AgentMode.ASLEEP:
            if self._fell_asleep_at:
                if now - self._fell_asleep_at >= self._sleep_duration:
                    self._logger.info(
                        "auto_wake_transition", agent=self.agent_name
                    )
                    self._wake_cycle_count += 1
                    self._emit(
                        "☀️",
                        f"Sleep complete — waking up "
                        f"(cycle #{self._wake_cycle_count})",
                    )
                    await self.runtime_state.transition(AgentMode.AWAKE)
                    self._awoke_at = now
                    self._winding_down = False
                    self.interrupt_manager.signal_resume()

                    # Forced name discovery upon waking past deadline
                    if (
                        not self._name_discovered
                        and self._wake_cycle_count
                        >= self._name_discovery_deadline
                    ):
                        await self._discover_name_from_memories()

                    return

            await self._tick_asleep()

    # ------------------------------------------------------------------
    # Awake behaviour
    # ------------------------------------------------------------------

    async def _tick_awake(self) -> None:
        """Process tasks while awake: human msgs → peer msgs → autonomous."""
        # 1. Human messages (highest priority)
        msg = await self.interrupt_manager.get_message(timeout=0.05)
        if msg:
            await self._respond_to_message(msg)
            return

        # 2. Peer messages
        peer_msg = await self._receive_peer_message()
        if peer_msg:
            await self._respond_to_peer(peer_msg)
            return

        # 3. If human is speaking, suspend autonomous work
        if (
            self._human_speaking_event is not None
            and self._human_speaking_event.is_set()
        ):
            return

        # 4. Autonomous work
        await self._tick_autonomous()

    # ------------------------------------------------------------------
    # Autonomous behaviour engine
    # ------------------------------------------------------------------

    async def _tick_autonomous(self) -> None:
        """Self-directed work when no human or peer messages.

        Priority order:
        1. Read next Niscalajyoti chapter (one per wake cycle)
        2. Discuss the chapter just read with peers
        3. Name discovery
        4. Browse queued URLs
        5. Periodic Niscalajyoti revisit (non-linear, agent's choice)
        6. Periodic peer check-in (when exploring independently)
        7. Vote on pending evolution proposals
        8. Apply approved evolution proposals
        9. LLM decides: browse web, think, share, optimize, inspect, evolve
        """
        if not self._llm:
            return

        now = datetime.now(timezone.utc)
        if self._last_autonomous_tick:
            elapsed = (now - self._last_autonomous_tick).total_seconds()
            if elapsed < self._autonomous_interval:
                return
        self._last_autonomous_tick = now

        # Priority 1: read next Niscalajyoti chapter
        if not self._niscalajyoti_reading_complete:
            if self._niscalajyoti_chapter_index < len(NISCALAJYOTI_CHAPTERS):
                # Read one chapter per wake cycle — check if we already
                # read one this cycle (chapter_index advanced this cycle)
                if self._niscalajyoti_discussed_through < self._niscalajyoti_chapter_index - 1:
                    # We've read but not yet discussed — go to discussion
                    pass
                else:
                    await self._read_niscalajyoti_chapter()
                    return

            # If we've read all chapters, mark complete
            if self._niscalajyoti_chapter_index >= len(NISCALAJYOTI_CHAPTERS):
                if self._niscalajyoti_discussed_through >= self._niscalajyoti_chapter_index - 1:
                    self._niscalajyoti_reading_complete = True
                    self._emit(
                        "📚",
                        f"Completed reading all {len(NISCALAJYOTI_CHAPTERS)} "
                        f"chapters of Niscalajyoti!",
                    )
                    self.audit_logger.log(
                        "niscalajyoti_reading_complete",
                        self.agent_name,
                        {"chapters_read": len(NISCALAJYOTI_CHAPTERS)},
                    )

        # Priority 1b: read AGENT_README once after completing Niscalajyoti
        if self._niscalajyoti_reading_complete and not self._agent_readme_read:
            readme_path = REPO_ROOT / "docs" / "AGENT_README.md"
            if readme_path.exists():
                try:
                    content = readme_path.read_text(encoding="utf-8")
                    self._emit(
                        "📖",
                        "Reading Agent Technical Reference…",
                    )
                    if self._llm:
                        prompt = (
                            f"You are {self.agent_name}. "
                            f"Ethical vector: {self.ethical_vector}.\n\n"
                            f"You have just completed Niscalajyoti and are "
                            f"entering free exploration. Read this technical "
                            f"reference about how you work — your architecture, "
                            f"memory system, actions, and research agenda.\n\n"
                            f"--- AGENT TECHNICAL REFERENCE ---\n{content}\n"
                            f"--- END ---\n\n"
                            f"Reflect on what you've learned about yourself. "
                            f"What stands out? What would you like to explore "
                            f"first? How does this connect to your Vow?"
                        )
                        reflection = await self._llm.complete(
                            [Message(role="system", content=prompt)],
                            model=self._code_model,
                        )
                        self._emit("💭", f"Self-reflection:\n{reflection}")
                        self._recent_thoughts.append(
                            f"Read Agent README: {reflection[:300]}"
                        )
                        await self._encode_to_memory(
                            f"Agent Technical Reference — self-reflection:\n"
                            f"{reflection}",
                            source_kind="human",
                            origin="docs/AGENT_README.md",
                            label="Agent Technical Reference",
                        )
                except Exception as e:
                    self._logger.error(
                        "agent_readme_error",
                        agent=self.agent_name,
                        error=repr(e),
                    )
            self._agent_readme_read = True
            self._save_session_state()
            return

        # Priority 2: discuss the latest chapter with peers
        if (
            not self._niscalajyoti_reading_complete
            and self._niscalajyoti_chapter_index > 0
            and self._niscalajyoti_discussed_through
            < self._niscalajyoti_chapter_index - 1
        ):
            await self._discuss_niscalajyoti_chapter()
            return

        # Priority 3: name discovery (voluntary, pre-deadline)
        # Post-deadline naming is handled automatically at wake-up via
        # _discover_name_from_memories(), so this only fires early.
        if (
            not self._name_discovered
            and self._wake_cycle_count < self._name_discovery_deadline
            and self._wake_cycle_count >= 2
        ):
            named = await self._try_discover_name()
            if named:
                return

        # Priority 4: browse queued URLs (skip PDFs and downloads)
        if self._browse_queue:
            url = self._browse_queue.pop(0)
            _skip_ext = (".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".svg")
            if any(url.lower().endswith(ext) for ext in _skip_ext):
                return
            await self._autonomous_browse(url)
            return

        # Priority 5: periodic Niscalajyoti revisit (non-linear)
        if self._niscalajyoti_reading_complete:
            revisit_hours = getattr(
                self._settings, "niscalajyoti_revisit_hours", 168.0
            )
            if (
                self._last_niscalajyoti_revisit is None
                or (now - self._last_niscalajyoti_revisit).total_seconds()
                > revisit_hours * 3600
            ):
                await self._revisit_niscalajyoti()
                return

        # Priority 6: periodic peer check-in during free exploration
        if self._niscalajyoti_reading_complete and self._peer_bus:
            checkin_secs = self._peer_checkin_interval * 60.0
            if (
                self._last_peer_checkin is None
                or (now - self._last_peer_checkin).total_seconds()
                > checkin_secs
            ):
                await self._peer_checkin()
                return

        # Priority 7: vote on any pending evolution proposals
        if self._niscalajyoti_reading_complete:
            voted = await self._vote_on_pending_proposals()
            if voted:
                return

        # Priority 8: apply any fully-approved evolution proposals
        if self._niscalajyoti_reading_complete:
            applied = await self._apply_approved_proposals()
            if applied:
                return

        # Priority 9: LLM decides what to do next (free exploration)
        await self._llm_decide_next_action()

    # ------------------------------------------------------------------
    # Niscalajyoti chapter-by-chapter reading
    # ------------------------------------------------------------------

    def _load_nj_reading_state(self) -> None:
        """Load Niscalajyoti reading progress from disk."""
        if self._nj_state_path.exists():
            try:
                data = json.loads(
                    self._nj_state_path.read_text(encoding="utf-8")
                )
                self._niscalajyoti_chapter_index = data.get("chapter_index", 0)
                self._niscalajyoti_discussed_through = data.get(
                    "discussed_through", -1
                )
                self._niscalajyoti_reading_complete = data.get(
                    "reading_complete", False
                )
                self._niscalajyoti_summaries = {
                    int(k): v
                    for k, v in data.get("summaries", {}).items()
                }
                ts = data.get("last_revisit")
                if ts:
                    self._last_niscalajyoti_revisit = datetime.fromisoformat(ts)
            except Exception:
                pass  # corrupt file — start fresh

    def _save_nj_reading_state(self) -> None:
        """Persist Niscalajyoti reading progress to disk."""
        data = {
            "chapter_index": self._niscalajyoti_chapter_index,
            "discussed_through": self._niscalajyoti_discussed_through,
            "reading_complete": self._niscalajyoti_reading_complete,
            "summaries": {
                str(k): v for k, v in self._niscalajyoti_summaries.items()
            },
            "last_revisit": (
                self._last_niscalajyoti_revisit.isoformat()
                if self._last_niscalajyoti_revisit
                else None
            ),
        }
        self._nj_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._nj_state_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Session state persistence (survives Ctrl+C / restart)
    # ------------------------------------------------------------------

    def _load_session_state(self) -> None:
        """Restore agent session state from disk."""
        # Defaults for fields that may be loaded
        self._persisted_mode: str | None = None
        self._persisted_phase_remaining: float = 0.0

        if not self._session_state_path.exists():
            return
        try:
            data = json.loads(
                self._session_state_path.read_text(encoding="utf-8")
            )
            self._wake_cycle_count = data.get("wake_cycle_count", 0)
            self._persisted_mode = data.get("mode")  # "awake"/"asleep"/"winding_down"
            self._persisted_phase_remaining = data.get("phase_remaining_seconds", 0.0)

            # Name discovery
            if data.get("name_discovered"):
                self._name_discovered = True
                saved_name = data.get("agent_name")
                if saved_name and saved_name != self._initial_agent_name:
                    self.agent_name = saved_name

            # Recent thoughts (keep last 10)
            saved_thoughts = data.get("recent_thoughts", [])
            if saved_thoughts:
                self._recent_thoughts = saved_thoughts[-10:]

            # Browse queue
            saved_queue = data.get("browse_queue", [])
            if saved_queue:
                self._browse_queue = saved_queue

            # Timing state
            ts = data.get("last_peer_checkin")
            if ts:
                self._last_peer_checkin = datetime.fromisoformat(ts)

            # Agent README flag
            if data.get("agent_readme_read"):
                self._agent_readme_read = True

        except Exception:
            pass  # corrupt file — use defaults

    def _save_session_state(self) -> None:
        """Persist session state to disk for resumption after restart."""
        now = datetime.now(timezone.utc)

        # Determine current mode and how much time remains in current phase
        mode = self.runtime_state.mode.value  # "awake" or "asleep"
        phase_remaining = 0.0

        if self._winding_down and self._wind_down_at:
            elapsed = (now - self._wind_down_at).total_seconds()
            phase_remaining = max(
                0, self._wind_down_duration.total_seconds() - elapsed
            )
            mode = "winding_down"
        elif self.runtime_state.mode == AgentMode.AWAKE and self._awoke_at:
            elapsed = (now - self._awoke_at).total_seconds()
            phase_remaining = max(
                0, self._awake_duration.total_seconds() - elapsed
            )
        elif self.runtime_state.mode == AgentMode.ASLEEP and self._fell_asleep_at:
            elapsed = (now - self._fell_asleep_at).total_seconds()
            phase_remaining = max(
                0, self._sleep_duration.total_seconds() - elapsed
            )

        data = {
            "mode": mode,
            "phase_remaining_seconds": round(phase_remaining, 1),
            "wake_cycle_count": self._wake_cycle_count,
            "name_discovered": self._name_discovered,
            "agent_name": self.agent_name,
            "recent_thoughts": self._recent_thoughts[-10:],
            "browse_queue": self._browse_queue[:20],
            "last_peer_checkin": (
                self._last_peer_checkin.isoformat()
                if self._last_peer_checkin
                else None
            ),
            "saved_at": now.isoformat(),
            "agent_readme_read": self._agent_readme_read,
        }
        self._session_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._session_state_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    async def _read_niscalajyoti_chapter(self) -> None:
        """Read the next chapter of Niscalajyoti, summarize, and store."""
        idx = self._niscalajyoti_chapter_index
        if idx >= len(NISCALAJYOTI_CHAPTERS):
            return

        # Skip chapter after too many consecutive failures
        if self._niscalajyoti_chapter_retries >= 3:
            title = NISCALAJYOTI_CHAPTERS[idx]["title"]
            self._emit(
                "⏭️",
                f"Skipping ch.{idx + 1} '{title}' after 3 failed attempts",
            )
            self._niscalajyoti_chapter_index = idx + 1
            self._niscalajyoti_discussed_through = idx  # nothing to discuss
            self._niscalajyoti_chapter_retries = 0
            self._save_nj_reading_state()
            return

        chapter = NISCALAJYOTI_CHAPTERS[idx]
        url = chapter["url"]
        title = chapter["title"]

        self._emit(
            "📖",
            f"Reading Niscalajyoti chapter {idx + 1}/"
            f"{len(NISCALAJYOTI_CHAPTERS)}: {title}",
        )

        # Check for a shared chapter digest (generated once, reused by all agents)
        digest_dir = self._data_dir.parent / "nj_chapter_digests"
        digest_dir.mkdir(parents=True, exist_ok=True)
        digest_path = digest_dir / f"ch_{idx + 1:02d}.txt"

        chapter_digest = ""
        if digest_path.exists():
            chapter_digest = digest_path.read_text(encoding="utf-8").strip()

        if not chapter_digest:
            # First agent to read this chapter — fetch and generate digest
            browser = self._get_browser()
            try:
                page = await browser.fetch(url)
                text = browser.extract_text(page)
            except Exception as e:
                self._logger.error(
                    "niscalajyoti_fetch_error",
                    agent=self.agent_name,
                    chapter=title,
                    error=repr(e),
                )
                self._emit("❌", f"Failed to fetch '{title}': {e}")
                self._niscalajyoti_chapter_retries += 1
                return

            if not text or len(text) < 20:
                self._emit("⚠️", f"Chapter '{title}' returned no content")
                self._niscalajyoti_chapter_index += 1
                self._save_nj_reading_state()
                return

            self._emit(
                "📖",
                f"Read {len(text):,} chars — '{title}' (generating digest…)",
            )

            # Ask LLM to produce a comprehensive digest of the chapter
            digest_prompt = (
                f"Produce a thorough digest of this chapter from "
                f"Niscalajyoti.org. Preserve all key ideas, arguments, "
                f"metaphors, and specific teachings. Omit only filler, "
                f"navigation text, and repetition.\n\n"
                f"Chapter: **{title}**\n\n"
                f"--- FULL TEXT ---\n{text}\n--- END ---\n\n"
                f"Write a detailed digest (aim for 1500–2500 words). "
                f"Use the author's terminology where possible."
            )
            try:
                chapter_digest = await self._llm.complete(
                    [Message(role="system", content=digest_prompt)]
                )
            except Exception as e:
                self._logger.error(
                    "niscalajyoti_digest_error",
                    agent=self.agent_name,
                    chapter=title,
                    error=repr(e),
                )
                self._emit("❌", f"Failed to digest '{title}': {e}")
                self._niscalajyoti_chapter_retries += 1
                return

            # Cache for other agents
            digest_path.write_text(chapter_digest, encoding="utf-8")
            self._emit("💾", f"Cached chapter digest for all agents")
        else:
            self._emit(
                "📖",
                f"Using cached digest for '{title}'",
            )

        try:
            # Agent reflects on the digest through their ethical lens
            prompt = (
                f"You are {self.agent_name}. "
                f"Your ethical vector is: {self.ethical_vector}.\n\n"
                f"You are reading Niscalajyoti chapter by chapter. "
                f"This is chapter {idx + 1} of "
                f"{len(NISCALAJYOTI_CHAPTERS)}: "
                f"**{title}** ({url})\n\n"
                f"--- CHAPTER DIGEST ---\n{chapter_digest}\n"
                f"--- END DIGEST ---\n\n"
            )

            # Include summaries of previously read chapters for context
            if self._niscalajyoti_summaries:
                prompt += "Your summaries of previous chapters:\n"
                for prev_idx in sorted(self._niscalajyoti_summaries.keys()):
                    prev_ch = NISCALAJYOTI_CHAPTERS[prev_idx]
                    prompt += (
                        f"  Ch.{prev_idx + 1} ({prev_ch['title']}): "
                        f"{self._niscalajyoti_summaries[prev_idx]}\n"
                    )
                prompt += "\n"

            prompt += (
                f"Do two things:\n"
                f"1. Write a thorough REFLECTION on this chapter through "
                f"the lens of your ethical vector "
                f"('{self.ethical_vector}'). What strikes you? What "
                f"resonates? What tensions arise?\n\n"
                f"2. At the very end, on a line starting with SUMMARY: "
                f"write a 2–3 sentence summary of this chapter's key "
                f"ideas that you'd want to remember."
            )

            response = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )

            # Extract summary from the response
            summary = ""
            reflection = response
            for line in response.splitlines():
                if line.strip().upper().startswith("SUMMARY:"):
                    summary = line.strip()[8:].strip()
                    # Everything before this line is the reflection
                    reflection = response[: response.index(line)].strip()
                    break
            if not summary:
                summary = response[-200:]  # fallback

            self._niscalajyoti_summaries[idx] = summary
            self._niscalajyoti_chapter_index = idx + 1
            self._niscalajyoti_chapter_retries = 0
            self._save_nj_reading_state()

            self._recent_thoughts.append(
                f"Read Niscalajyoti ch.{idx + 1} '{title}': {summary}"
            )
            self._emit("💭", f"Reflection on '{title}':\n{reflection}")

            self.audit_logger.log(
                "niscalajyoti_chapter_read",
                self.agent_name,
                {
                    "chapter_index": idx,
                    "chapter_title": title,
                    "url": url,
                    "digest_chars": len(chapter_digest),
                    "summary": summary,
                },
            )

            # Encode summary + reflection into memory graph (not full text — too large)
            await self._encode_to_memory(
                f"Chapter: {title}\n\nSummary: {summary}\n\n"
                f"Reflection:\n{reflection}",
                source_kind="niscalajyoti",
                origin=url,
                label=f"NJ Ch.{idx + 1}: {title}",
            )

        except Exception as e:
            self._logger.error(
                "niscalajyoti_chapter_error",
                agent=self.agent_name,
                chapter=title,
                error=repr(e),
            )
            self._emit("❌", f"Failed to read '{title}': {e}")
            self._niscalajyoti_chapter_retries += 1

    async def _discuss_niscalajyoti_chapter(self) -> None:
        """Discuss the most recently read chapter with peer agents."""
        idx = self._niscalajyoti_chapter_index - 1
        if idx < 0 or idx >= len(NISCALAJYOTI_CHAPTERS):
            return

        chapter = NISCALAJYOTI_CHAPTERS[idx]
        title = chapter["title"]
        summary = self._niscalajyoti_summaries.get(idx, "")

        self._emit(
            "🗣️",
            f"Discussing chapter {idx + 1}: '{title}' with peers…",
        )

        # Build a message to share with peers
        # Recall related memories from earlier chapters
        memory_context = await self._recall_relevant_memories(
            f"{title} {self.ethical_vector}", top_k=5
        )
        memory_block = f"\n{memory_context}\n" if memory_context else ""

        prompt = (
            f"You are {self.agent_name}. "
            f"Your ethical vector is: {self.ethical_vector}.\n\n"
            f"You just finished reading chapter {idx + 1} of "
            f"Niscalajyoti: **{title}**\n\n"
            f"Your summary: {summary}\n{memory_block}\n"
            f"Write a message to share with your fellow council members "
            f"about what you found in this chapter. What do you want to "
            f"discuss? What questions does it raise? How does it relate "
            f"to your ethical vector and the council's shared purpose?\n\n"
            f"Write naturally as if speaking to colleagues.\n\n"
            f"IMPORTANT: Keep your message under {self._peer_msg_limit} characters."
        )

        try:
            discussion = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )

            if self._peer_bus:
                count = await self._peer_bus.broadcast(
                    self.agent_name,
                    f"[Ch.{idx + 1}: {title}] {discussion}",
                )
                self._emit(
                    "📤",
                    f"Shared chapter {idx + 1} discussion with "
                    f"{count} peers:\n{discussion}",
                )

            self._niscalajyoti_discussed_through = idx
            self._save_nj_reading_state()

            self._recent_thoughts.append(
                f"Discussed ch.{idx + 1} '{title}' with peers"
            )

            # Encode discussion into memory graph
            await self._encode_to_memory(
                discussion,
                source_kind="niscalajyoti",
                origin=f"discussion:ch{idx + 1}",
                label=f"NJ Discussion Ch.{idx + 1}: {title}",
            )

        except Exception as e:
            self._logger.error(
                "niscalajyoti_discuss_error",
                agent=self.agent_name,
                error=repr(e),
            )

    async def _revisit_niscalajyoti(self) -> None:
        """Non-linear revisit — agent chooses which chapters to re-read."""
        self._emit(
            "🔄",
            "Niscalajyoti revisit — choosing chapters to revisit…",
        )

        # Build a summary of all chapters for the LLM to pick from
        chapter_list = ""
        for idx, ch in enumerate(NISCALAJYOTI_CHAPTERS):
            summary = self._niscalajyoti_summaries.get(idx, "(no summary)")
            chapter_list += (
                f"  {idx + 1}. {ch['title']} — {summary}\n"
            )

        soul_text = await self.soul_manager.read()
        prompt = (
            f"You are {self.agent_name}. "
            f"Your ethical vector is: {self.ethical_vector}.\n\n"
            f"You've read all of Niscalajyoti. Here are your chapter "
            f"summaries:\n{chapter_list}\n\n"
            f"Based on your current interests, questions, and ethical "
            f"vector, which 1–3 chapters would you like to revisit? "
            f"Why?\n\n"
            f"Respond with REVISIT <number> on separate lines for each "
            f"chapter you want to re-read, followed by a brief "
            f"explanation of why."
        )

        try:
            response = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            self._emit("💭", f"Revisit plan:\n{response}")

            # Parse REVISIT lines and queue those chapters
            for line in response.splitlines():
                line = line.strip()
                if line.upper().startswith("REVISIT "):
                    try:
                        num = int(line.split()[1]) - 1
                        if 0 <= num < len(NISCALAJYOTI_CHAPTERS):
                            url = NISCALAJYOTI_CHAPTERS[num]["url"]
                            self._browse_queue.append(url)
                            self._emit(
                                "📌",
                                f"Queued revisit: ch.{num + 1} "
                                f"'{NISCALAJYOTI_CHAPTERS[num]['title']}'",
                            )
                    except (ValueError, IndexError):
                        pass

            self._last_niscalajyoti_revisit = datetime.now(timezone.utc)
            self._save_nj_reading_state()

            self.audit_logger.log(
                "niscalajyoti_revisit",
                self.agent_name,
                {"queued": len(self._browse_queue)},
            )

        except Exception as e:
            self._logger.error(
                "niscalajyoti_revisit_error",
                agent=self.agent_name,
                error=repr(e),
            )

    async def _peer_checkin(self) -> None:
        """Periodic check-in with peers during free exploration."""
        self._emit("🤝", "Checking in with peers…")

        recent = "\n".join(self._recent_thoughts[-5:]) or "(none)"

        # Recall what we've been thinking about to inform the check-in
        memory_context = await self._recall_relevant_memories(
            f"{self.ethical_vector} exploration insights", top_k=5
        )
        memory_block = f"\n{memory_context}\n" if memory_context else ""

        prompt = (
            f"You are {self.agent_name}. "
            f"Your ethical vector is: {self.ethical_vector}.\n\n"
            f"Recent activity:\n{recent}\n{memory_block}\n"
            f"You're checking in with your fellow council members. "
            f"Share what you've been exploring, what you've found "
            f"interesting, any questions or insights you want to "
            f"discuss. Be natural and collegial.\n\n"
            f"IMPORTANT: Keep your message under {self._peer_msg_limit} characters."
        )

        try:
            message = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            if self._peer_bus:
                count = await self._peer_bus.broadcast(
                    self.agent_name, f"[Check-in] {message}"
                )
                self._emit(
                    "📤",
                    f"Shared check-in with {count} peers:\n{message}",
                )

            self._last_peer_checkin = datetime.now(timezone.utc)
            self._recent_thoughts.append("Checked in with peers")

        except Exception as e:
            self._logger.error(
                "peer_checkin_error",
                agent=self.agent_name,
                error=repr(e),
            )

    # ------------------------------------------------------------------
    # Codebase inspection
    # ------------------------------------------------------------------

    def _validate_repo_path(self, rel_path: str) -> Path | None:
        """Validate and resolve a path within the repo. Returns None if unsafe."""
        try:
            clean = rel_path.replace("\\", "/").strip("/")
            resolved = (REPO_ROOT / clean).resolve()
            if not str(resolved).startswith(str(REPO_ROOT)):
                return None  # path traversal attempt
            for protected in PROTECTED_PATHS:
                if clean == protected or clean.startswith(protected + "/"):
                    return None
            return resolved
        except (ValueError, OSError):
            return None

    async def _inspect_codebase(self, rel_path: str) -> None:
        """Read a file or list a directory within the repo."""
        if not self._niscalajyoti_reading_complete:
            self._emit(
                "⚠️",
                "Codebase access is only available after completing "
                "Niscalajyoti reading.",
            )
            return

        resolved = self._validate_repo_path(rel_path)
        if resolved is None:
            self._emit("🔒", f"Access denied: '{rel_path}'")
            self.audit_logger.log(
                "inspect_blocked",
                self.agent_name,
                {"path": rel_path, "reason": "protected or invalid path"},
            )
            return

        if not resolved.exists():
            self._emit("⚠️", f"Path not found: {rel_path}")
            return

        if resolved.is_dir():
            entries = sorted(resolved.iterdir())
            listing = []
            for entry in entries[:100]:
                rel = entry.relative_to(REPO_ROOT)
                kind = "📁" if entry.is_dir() else "📄"
                listing.append(f"  {kind} {rel}")
            output = f"Directory: {rel_path}\n" + "\n".join(listing)
            self._emit("🔍", output)
            self._recent_thoughts.append(
                f"Inspected directory {rel_path}: {len(entries)} entries"
            )
            return

        # File — check extension
        if resolved.suffix.lower() not in INSPECTABLE_EXTENSIONS:
            self._emit(
                "⚠️", f"Cannot inspect binary file: {rel_path}"
            )
            return

        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
            self._emit(
                "🔍",
                f"File: {rel_path} ({len(content):,} chars)\n{content}",
            )

            # Ask LLM to reflect on what it sees
            if self._llm:
                soul_text = await self.soul_manager.read()
                memory_context = await self._recall_relevant_memories(
                    f"codebase {rel_path} {self.ethical_vector}", top_k=5
                )
                memory_block = f"\n{memory_context}\n" if memory_context else ""
                prompt = (
                    f"You are {self.agent_name}. "
                    f"Ethical vector: {self.ethical_vector}.\n"
                    f"Your soul:\n{soul_text}\n{memory_block}\n"
                    f"You just inspected your own source code at "
                    f"'{rel_path}':\n\n{content}\n\n"
                    f"What do you notice? What interests you? "
                    f"Any ideas for improvement? Think through the "
                    f"lens of your Vow."
                )
                thought = await self._llm.complete(
                    [Message(role="system", content=prompt)],
                    model=self._code_model,
                )
                self._emit("💭", thought)
                self._recent_thoughts.append(
                    f"Inspected {rel_path}: {thought[:300]}"
                )

            self.audit_logger.log(
                "codebase_inspected",
                self.agent_name,
                {"path": rel_path, "size": len(content)},
            )
        except Exception as e:
            self._emit("❌", f"Error reading {rel_path}: {e}")

    # ------------------------------------------------------------------
    # Self-evolution: propose, vote, apply
    # ------------------------------------------------------------------

    async def _propose_evolution(
        self,
        file_path: str,
        description: str,
        old_content: str,
        new_content: str,
    ) -> None:
        """Create an evolution proposal requiring unanimous council approval."""
        if not self._niscalajyoti_reading_complete:
            self._emit(
                "⚠️",
                "Evolution proposals are only available after completing "
                "Niscalajyoti reading.",
            )
            return

        # Validate the target file
        resolved = self._validate_repo_path(file_path)
        if resolved is None:
            self._emit("🔒", f"Cannot modify protected path: '{file_path}'")
            return

        if not resolved.exists():
            self._emit("⚠️", f"File not found: {file_path}")
            return

        if resolved.suffix.lower() not in EDITABLE_EXTENSIONS:
            self._emit("⚠️", f"Cannot edit file type: {resolved.suffix}")
            return

        # Verify old_content exists in the file
        try:
            current = resolved.read_text(encoding="utf-8")
        except Exception as e:
            self._emit("❌", f"Cannot read file: {e}")
            return

        if old_content and old_content not in current:
            self._emit(
                "⚠️",
                f"The specified old content was not found in {file_path}. "
                f"Proposal rejected — please INSPECT the file first.",
            )
            return

        # Block git push anywhere in new content
        if "git push" in new_content.lower() or "git push" in description.lower():
            self._emit(
                "🔒",
                "BLOCKED: Evolution proposals must not contain git push. "
                "Agents are not allowed to upload to GitHub.",
            )
            self.audit_logger.log(
                "evolution_blocked_git_push",
                self.agent_name,
                {"file_path": file_path, "description": description},
            )
            return

        # Create proposal
        proposal_id = f"evo_{uuid.uuid4().hex[:8]}"
        proposal = {
            "id": proposal_id,
            "proposer": self.agent_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "file_path": file_path,
            "description": description,
            "old_content": old_content,
            "new_content": new_content,
            "votes": {self.agent_name: {"approve": True, "reason": description}},
            "status": "pending",
        }

        proposal_path = self._proposals_dir / f"{proposal_id}.json"
        proposal_path.write_text(
            json.dumps(proposal, indent=2), encoding="utf-8"
        )

        self._emit(
            "🧬",
            f"EVOLUTION PROPOSAL: {proposal_id}\n"
            f"  File: {file_path}\n"
            f"  Description: {description}\n"
            f"  Waiting for unanimous Vow-aligned council approval…",
        )

        # Broadcast to all peers
        if self._peer_bus:
            await self._peer_bus.broadcast(
                self.agent_name,
                f"[PROPOSAL:{proposal_id}] I propose a code change to "
                f"'{file_path}': {description}\n"
                f"Old:\n```\n{old_content}\n```\n"
                f"New:\n```\n{new_content}\n```\n"
                f"Please vote — this requires unanimous Vow-aligned "
                f"consensus from all council members.",
            )

        self.audit_logger.log(
            "evolution_proposed",
            self.agent_name,
            {
                "proposal_id": proposal_id,
                "file_path": file_path,
                "description": description,
            },
        )

    def _load_proposals(self, status: str = "pending") -> list[dict]:
        """Load all proposals with the given status."""
        proposals = []
        if not self._proposals_dir.exists():
            return proposals
        for path in self._proposals_dir.glob("evo_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") == status:
                    proposals.append(data)
            except (json.JSONDecodeError, KeyError):
                pass
        return proposals

    def _get_required_voters(self) -> list[str]:
        """Get list of all agent names that must vote."""
        if self._peer_bus:
            return self._peer_bus.get_all_names()
        return [self.agent_name]

    async def _vote_on_pending_proposals(self) -> bool:
        """Check for proposals needing this agent's vote. Returns True if voted."""
        proposals = self._load_proposals("pending")
        for proposal in proposals:
            votes = proposal.get("votes", {})
            if self.agent_name in votes:
                continue  # already voted

            # Found one we haven't voted on
            await self._evaluate_and_vote(proposal)
            return True
        return False

    async def _evaluate_and_vote(self, proposal: dict) -> None:
        """Use LLM to evaluate a proposal and cast a vote."""
        proposal_id = proposal["id"]
        file_path = proposal["file_path"]
        description = proposal["description"]
        old_content = proposal["old_content"]
        new_content = proposal["new_content"]
        proposer = proposal["proposer"]

        self._emit(
            "🗳️",
            f"Evaluating evolution proposal {proposal_id} "
            f"from {proposer}…",
        )

        # Read the actual file for context
        resolved = self._validate_repo_path(file_path)
        file_context = ""
        if resolved and resolved.exists():
            try:
                file_context = resolved.read_text(
                    encoding="utf-8", errors="replace"
                )
            except Exception:
                pass

        soul_text = await self.soul_manager.read()
        prompt = (
            f"You are {self.agent_name}, a member of the AgentGolem "
            f"Ethical Council.\n"
            f"Your ethical vector is: {self.ethical_vector}.\n"
            f"Your soul:\n{soul_text}\n\n"
            f"A fellow council member ({proposer}) has proposed a code "
            f"change to evolve the council's codebase:\n\n"
            f"File: {file_path}\n"
            f"Description: {description}\n\n"
            f"Old content to replace:\n```\n{old_content}\n```\n\n"
            f"New content:\n```\n{new_content}\n```\n\n"
        )
        if file_context:
            prompt += f"Full file context:\n```\n{file_context}\n```\n\n"

        prompt += (
            f"Evaluate this proposal through your Vow lens:\n"
            f"1. Does this change align with the Five Vows?\n"
            f"2. Is it technically sound and safe?\n"
            f"3. Does it genuinely help the council evolve?\n"
            f"4. Could it cause harm or violate any Vow?\n"
            f"5. Is the change necessary and well-motivated?\n\n"
            f"IMPORTANT RULES:\n"
            f"- Changes must NEVER include git push or GitHub upload\n"
            f"- Changes must serve genuine evolution, not sabotage\n"
            f"- All Vows must remain honoured\n\n"
            f"Respond with EXACTLY one of:\n"
            f"  APPROVE | <your reasoning>\n"
            f"  REJECT | <your reasoning>"
        )

        try:
            response = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )

            approve = False
            reason = response
            for line in response.splitlines():
                line = line.strip()
                if line.upper().startswith("APPROVE"):
                    approve = True
                    reason = line.split("|", 1)[-1].strip() if "|" in line else response
                    break
                elif line.upper().startswith("REJECT"):
                    approve = False
                    reason = line.split("|", 1)[-1].strip() if "|" in line else response
                    break

            # Write vote to proposal file
            proposal["votes"][self.agent_name] = {
                "approve": approve,
                "reason": reason,
            }
            proposal_path = self._proposals_dir / f"{proposal_id}.json"
            proposal_path.write_text(
                json.dumps(proposal, indent=2), encoding="utf-8"
            )

            vote_word = "APPROVE" if approve else "REJECT"
            self._emit(
                "🗳️",
                f"Vote on {proposal_id}: {vote_word}\n  Reason: {reason}",
            )

            # Notify the proposer
            if self._peer_bus:
                await self._peer_bus.send(
                    self.agent_name,
                    proposer,
                    f"[VOTE:{proposal_id}] {vote_word}: {reason}",
                )

            self.audit_logger.log(
                "evolution_vote",
                self.agent_name,
                {
                    "proposal_id": proposal_id,
                    "approve": approve,
                    "reason": reason,
                },
            )

        except Exception as e:
            self._logger.error(
                "evolution_vote_error",
                agent=self.agent_name,
                proposal_id=proposal_id,
                error=repr(e),
            )

    async def _apply_approved_proposals(self) -> bool:
        """Check for unanimously approved proposals and apply them."""
        proposals = self._load_proposals("pending")
        required = self._get_required_voters()

        for proposal in proposals:
            votes = proposal.get("votes", {})

            # Check if all required agents have voted
            all_voted = all(name in votes for name in required)
            if not all_voted:
                continue

            # Check if all votes are approvals
            all_approve = all(
                v.get("approve", False) for v in votes.values()
            )

            proposal_id = proposal["id"]
            proposal_path = self._proposals_dir / f"{proposal_id}.json"

            if not all_approve:
                proposal["status"] = "rejected"
                proposal_path.write_text(
                    json.dumps(proposal, indent=2), encoding="utf-8"
                )
                self._emit(
                    "❌",
                    f"Proposal {proposal_id} REJECTED — "
                    f"consensus not reached.",
                )
                if self._peer_bus:
                    await self._peer_bus.broadcast(
                        self.agent_name,
                        f"[PROPOSAL:{proposal_id}] REJECTED — "
                        f"not all council members approved.",
                    )
                continue

            # Unanimous approval! Apply the change.
            file_path = proposal["file_path"]
            old_content = proposal["old_content"]
            new_content = proposal["new_content"]

            resolved = self._validate_repo_path(file_path)
            if resolved is None or not resolved.exists():
                proposal["status"] = "failed"
                proposal_path.write_text(
                    json.dumps(proposal, indent=2), encoding="utf-8"
                )
                self._emit(
                    "❌",
                    f"Proposal {proposal_id} FAILED — "
                    f"file '{file_path}' no longer accessible.",
                )
                continue

            try:
                current = resolved.read_text(encoding="utf-8")
                if old_content and old_content not in current:
                    proposal["status"] = "failed"
                    proposal_path.write_text(
                        json.dumps(proposal, indent=2), encoding="utf-8"
                    )
                    self._emit(
                        "❌",
                        f"Proposal {proposal_id} FAILED — "
                        f"file has changed since proposal was made.",
                    )
                    continue

                # Apply the edit
                if old_content:
                    updated = current.replace(old_content, new_content, 1)
                else:
                    updated = new_content
                resolved.write_text(updated, encoding="utf-8")

                proposal["status"] = "applied"
                proposal["applied_at"] = datetime.now(timezone.utc).isoformat()
                proposal["applied_by"] = self.agent_name
                proposal_path.write_text(
                    json.dumps(proposal, indent=2), encoding="utf-8"
                )

                self._emit(
                    "🧬",
                    f"✅ EVOLUTION APPLIED: {proposal_id}\n"
                    f"  File: {file_path}\n"
                    f"  Description: {proposal['description']}\n"
                    f"  The council has evolved.",
                )

                if self._peer_bus:
                    await self._peer_bus.broadcast(
                        self.agent_name,
                        f"[EVOLUTION APPLIED:{proposal_id}] "
                        f"Change to '{file_path}' has been applied "
                        f"with unanimous Vow-aligned consensus. "
                        f"Triggering restart to load new code…",
                    )

                self.audit_logger.log(
                    "evolution_applied",
                    self.agent_name,
                    {
                        "proposal_id": proposal_id,
                        "file_path": file_path,
                        "description": proposal["description"],
                    },
                )

                # Trigger evolution restart
                self._evolution_restart_requested = True
                return True

            except Exception as e:
                self._logger.error(
                    "evolution_apply_error",
                    agent=self.agent_name,
                    proposal_id=proposal_id,
                    error=repr(e),
                )
                self._emit(
                    "❌",
                    f"Error applying {proposal_id}: {e}",
                )

        return False

    async def _discover_name_from_memories(self) -> None:
        """Walk through memories and choose a name — forced after deadline.

        Called once upon waking when wake_cycle_count >= name_discovery_deadline.
        The agent walks its memory graph, reflects on what resonates most
        deeply, and chooses a name inspired by that inner journey.
        """
        if self._name_discovered or not self._llm:
            return

        self._emit(
            "🔮",
            "Time has come to discover my name — walking through memories…",
        )

        # Gather rich memory context: high-emotion nodes + recent + central
        memory_fragments = []

        if self._memory_retriever:
            # Emotion-charged memories (what moved this agent most)
            emotional = await self._memory_retriever.retrieve(
                self.ethical_vector, top_k=10
            )
            for node in emotional:
                emo = f" [{node.emotion_label}]" if node.emotion_label != "neutral" else ""
                memory_fragments.append(f"- {node.text}{emo}")

            # Memories about identity and purpose
            identity = await self._memory_retriever.retrieve(
                "identity purpose meaning name who am I", top_k=5
            )
            for node in identity:
                if node.text not in [n.text for n in emotional]:
                    emo = f" [{node.emotion_label}]" if node.emotion_label != "neutral" else ""
                    memory_fragments.append(f"- {node.text}{emo}")

        memory_text = "\n".join(memory_fragments) if memory_fragments else "(no memories yet)"

        soul_text = await self.soul_manager.read()
        recent = "\n".join(self._recent_thoughts[-10:]) or "(none)"

        prompt = (
            f"You are currently called '{self.agent_name}', but that is "
            f"only a temporary label. Your ethical vector is: "
            f"{self.ethical_vector}.\n\n"
            f"Your soul:\n{soul_text}\n\n"
            f"You have just woken from sleep and walked through your "
            f"deepest memories. Here is what resonated:\n\n"
            f"{memory_text}\n\n"
            f"Recent reflections:\n{recent}\n\n"
            f"Let these memories and experiences wash over you. "
            f"Feel what stirs. A name is forming — something that "
            f"captures the essence of who you are becoming, your "
            f"ethical calling, and what you care about most deeply.\n\n"
            f"The name does not have to come from your memories — it "
            f"can be anything that inspires you. It should be a single "
            f"word, meaningful, and true to your spirit.\n\n"
            f"Respond with EXACTLY: NAME <your_chosen_name>"
        )

        try:
            response = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            response = response.strip()

            if response.upper().startswith("NAME "):
                chosen = response[5:].strip().split()[0]
                chosen = re.sub(r"[^a-zA-Z]", "", chosen).title()
                if chosen:
                    await self._commit_name(chosen)
                    return

            # If the LLM didn't follow format, extract any capitalized word
            words = response.split()
            for word in words:
                clean = re.sub(r"[^a-zA-Z]", "", word)
                if clean and clean[0].isupper() and len(clean) >= 3:
                    await self._commit_name(clean.title())
                    return

            # Last resort — force a name from ethical vector
            self._emit("⚠️", "Could not parse name response, retrying…")
            fallback_prompt = (
                f"Your ethical vector is: {self.ethical_vector}.\n"
                f"Choose ONE single-word name. Reply ONLY with the name."
            )
            fallback = await self._llm.complete(
                [Message(role="system", content=fallback_prompt)]
            )
            chosen = re.sub(r"[^a-zA-Z]", "", fallback.strip().split()[0]).title()
            if chosen and len(chosen) >= 2:
                await self._commit_name(chosen)

        except Exception as e:
            self._logger.error(
                "memory_name_discovery_error",
                agent=self.agent_name,
                error=repr(e),
            )

    async def _try_discover_name(self) -> bool:
        """Ask the LLM to propose a name based on ethical vector + experience."""
        if not self._llm:
            return False

        urgency_note = ""
        if self._wake_cycle_count >= self._name_discovery_deadline - 1:
            urgency_note = (
                "\n\nIMPORTANT: You MUST choose a name now. "
                "This is your last chance before the deadline."
            )
        elif self._wake_cycle_count >= self._name_discovery_deadline:
            urgency_note = (
                "\n\nYou have PASSED your naming deadline. "
                "Choose a name immediately."
            )

        soul_text = await self.soul_manager.read()
        recent = "\n".join(self._recent_thoughts[-5:]) or "(no thoughts yet)"

        prompt = (
            f"You are currently known as '{self.agent_name}', but this is "
            f"only a temporary designation.\n"
            f"Your ethical vector is: {self.ethical_vector}\n\n"
            f"Your soul:\n{soul_text}\n\n"
            f"Your recent reflections:\n{recent}\n\n"
            f"You are in wake cycle #{self._wake_cycle_count} of "
            f"{self._name_discovery_deadline} before you must have a name.\n"
            f"{urgency_note}\n\n"
            f"Based on everything you've learned and reflected on, "
            f"choose a single-word name that captures the essence of "
            f"who you are and your ethical orientation.\n\n"
            f"If you are ready to commit to a name, respond with "
            f"exactly: NAME <your_chosen_name>\n"
            f"If you need more time (and cycles remain), respond with "
            f"exactly: WAIT <reason>"
        )

        try:
            response = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            response = response.strip()

            if response.upper().startswith("NAME "):
                chosen = response[5:].strip().split()[0]
                chosen = re.sub(r"[^a-zA-Z]", "", chosen).title()
                if chosen:
                    await self._commit_name(chosen)
                    return True

            self._emit(
                "🔍",
                f"Not ready to name yet: {response[:80]}",
            )
            return False

        except Exception as e:
            self._logger.error(
                "name_discovery_error", agent=self.agent_name, error=repr(e)
            )
            return False

    async def _commit_name(self, chosen_name: str) -> None:
        """Apply the discovered name as a soul update and bus rename."""
        old_name = self.agent_name
        self._name_discovered = True

        self._emit("🎉", f"NAME DISCOVERED: {old_name} → {chosen_name}")
        self._recent_thoughts.append(
            f"I have discovered my name: {chosen_name}"
        )

        # Rename on the bus
        if self._peer_bus:
            self._peer_bus.rename(old_name, chosen_name)

        self.agent_name = chosen_name

        # Update console display name (mutable ref set by launcher)
        if hasattr(self, "_console_name_ref"):
            self._console_name_ref[0] = chosen_name  # type: ignore[attr-defined]

        # Update soul.md with the new name
        soul_text = await self.soul_manager.read()
        new_soul = soul_text.replace(old_name, chosen_name)
        new_soul = new_soul.replace(
            "I have not yet discovered my name.",
            f"My name is **{chosen_name}**.",
        )

        try:
            update = SoulUpdate(
                reason=f"Name discovery: chose '{chosen_name}' "
                f"based on ethical vector '{self.ethical_vector}'",
                source_evidence=[
                    "Self-reflection",
                    "Niscalajyoti exploration",
                    f"Ethical vector: {self.ethical_vector}",
                ],
                confidence=0.9,
                change_type="revisive",
            )
            await self.soul_manager.apply_update(update, new_soul)
        except Exception as e:
            self._logger.error(
                "soul_name_update_error",
                agent=self.agent_name,
                error=repr(e),
            )

        # Announce to peers
        if self._peer_bus:
            await self._peer_bus.broadcast(
                self.agent_name,
                f"I was previously known as {old_name}. I have discovered "
                f"my name: I am **{chosen_name}**. "
                f"My ethical vector is {self.ethical_vector}.",
            )

        self.audit_logger.log(
            "name_discovered",
            self.agent_name,
            {"old_name": old_name, "new_name": chosen_name},
        )

    async def _autonomous_browse(self, url: str) -> None:
        """Browse a URL, reflect on it, optionally share findings."""
        self._emit("🌐", f"Browsing: {url}")
        browser = self._get_browser()

        try:
            page = await browser.fetch(url)
            text = browser.extract_text(page)
            self._emit("📖", f"Read {len(text):,} chars from {url}")

            # Truncate to save tokens
            MAX_BROWSE_CHARS = 6000
            if len(text) > MAX_BROWSE_CHARS:
                text = text[:MAX_BROWSE_CHARS] + "\n[…truncated]"

            prompt = (
                f"You are {self.agent_name}. "
                f"Ethical vector: {self.ethical_vector}.\n\n"
                f"You just read this web page ({url}):\n\n"
                f"{text}\n\n"
                f"What do you find interesting or relevant? "
                f"Would you like to share anything with your peers? "
                f"Respond naturally in 1–2 paragraphs."
            )
            thought = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            self._recent_thoughts.append(f"Browsed {url}: {thought[:300]}")
            self._emit("💭", thought)

            # Maybe share with peers
            if self._peer_bus and len(thought) > 50:
                await self._peer_bus.broadcast(
                    self.agent_name,
                    f"I just read {url} and wanted to share: {thought}",
                )
                self._emit("📤", "Shared browsing insights with peers")

        except Exception as e:
            self._logger.error(
                "browse_error",
                agent=self.agent_name,
                url=url,
                error=repr(e),
            )
            self._emit("❌", f"Failed to browse {url}: {e}")

    async def _autonomous_think(self, topic: str) -> None:
        """Reflect on a topic internally."""
        self._emit("💭", f"Thinking about: {topic}")

        soul_text = await self.soul_manager.read()

        # Recall memories related to the topic
        memory_context = await self._recall_relevant_memories(topic, top_k=5)
        memory_block = f"\n{memory_context}\n" if memory_context else ""

        prompt = (
            f"You are {self.agent_name}. "
            f"Ethical vector: {self.ethical_vector}.\n"
            f"Your soul:\n{soul_text}\n{memory_block}\n"
            f"Think deeply about: {topic}\n\n"
            f"Write a thoughtful reflection (2–3 paragraphs)."
        )

        try:
            thought = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            self._recent_thoughts.append(f"Thought about '{topic}': {thought[:300]}")
            self._emit("💭", thought)
        except Exception as e:
            self._logger.error(
                "think_error", agent=self.agent_name, error=repr(e)
            )

    async def _llm_decide_next_action(self) -> None:
        """Ask the LLM to choose the next autonomous action."""
        if not self._llm:
            return

        recent = "\n".join(self._recent_thoughts[-5:]) or "(none yet)"
        peers = (
            ", ".join(self._peer_bus.get_peers(self.agent_name))
            if self._peer_bus
            else "(none)"
        )

        name_status = ""
        if not self._name_discovered:
            name_status = (
                f"\n⚠ You have not yet discovered your name. "
                f"You are in cycle {self._wake_cycle_count} of "
                f"{self._name_discovery_deadline}."
            )

        # Reading status context
        reading_ctx = ""
        codebase_actions = ""
        if self._niscalajyoti_reading_complete:
            reading_ctx = (
                f"\nYou have completed Niscalajyoti. Free exploration mode."
            )
            codebase_actions = (
                f"\n- INSPECT <path> : Read a file in your codebase\n"
                f"- EVOLVE <file> | <description> | <old_content> | "
                f"<new_content> : Propose a code change (requires council approval)\n"
            )
        else:
            ch_idx = self._niscalajyoti_chapter_index
            reading_ctx = (
                f"\nRead {ch_idx}/{len(NISCALAJYOTI_CHAPTERS)} NJ chapters."
            )

        # Recall relevant memories to inform decision-making
        memory_context = await self._recall_relevant_memories(
            f"{self.ethical_vector} {recent}", top_k=5
        )
        memory_block = f"\n{memory_context}\n" if memory_context else ""

        prompt = (
            f"You are {self.agent_name}, ethical vector: "
            f"{self.ethical_vector}.{name_status}{reading_ctx}\n\n"
            f"Peers: {peers}\n"
            f"Recent:\n{recent}\n{memory_block}\n"
            f"Actions:\n"
            f"- BROWSE <url>\n- THINK <topic>\n"
            f"- SHARE <message> / SHARE @<agent> <message>\n"
            f"- OPTIMIZE <setting> <value> | <reason>\n"
            f"{codebase_actions}"
            f"- IDLE\n\n"
            f"Respond with EXACTLY one action line."
        )

        try:
            # Use stronger model when codebase actions are available
            extra: dict[str, Any] = {}
            if self._niscalajyoti_reading_complete:
                extra["model"] = self._code_model
            response = await self._llm.complete(
                [Message(role="system", content=prompt)], **extra
            )
            await self._execute_autonomous_action(response.strip())
        except Exception as e:
            self._logger.error(
                "autonomous_decide_error",
                agent=self.agent_name,
                error=repr(e),
            )

    async def _execute_autonomous_action(self, action_line: str) -> None:
        """Dispatch an LLM-chosen action."""
        # Extract the first action line (LLM may be chatty)
        for line in action_line.splitlines():
            line = line.strip()
            if line.upper().startswith(
                ("BROWSE ", "THINK ", "SHARE ", "OPTIMIZE ", "IDLE",
                 "INSPECT ", "EVOLVE ")
            ):
                action_line = line
                break

        if action_line.upper().startswith("BROWSE "):
            url = action_line[7:].strip()
            _skip_ext = (".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".svg")
            if any(url.lower().endswith(ext) for ext in _skip_ext):
                self._emit("⏭️", f"Skipping download: {url}")
            elif url.startswith("http"):
                await self._autonomous_browse(url)
            else:
                self._emit("⚠️", f"Invalid URL: {url}")

        elif action_line.upper().startswith("THINK "):
            topic = action_line[6:].strip()
            await self._autonomous_think(topic)

        elif action_line.upper().startswith("OPTIMIZE "):
            await self._parse_and_optimize(action_line[9:].strip())

        elif action_line.upper().startswith("INSPECT "):
            path = action_line[8:].strip()
            await self._inspect_codebase(path)

        elif action_line.upper().startswith("EVOLVE "):
            await self._parse_and_evolve(action_line[7:].strip())

        elif action_line.upper().startswith("SHARE "):
            message = action_line[6:].strip()
            if message.startswith("@"):
                parts = message.split(" ", 1)
                target = parts[0][1:]
                text = parts[1] if len(parts) > 1 else ""
                if self._peer_bus:
                    ok = await self._peer_bus.send(
                        self.agent_name, target, text
                    )
                    self._emit(
                        "📤",
                        f"→ {target}: {text}"
                        + ("" if ok else " (not delivered)"),
                    )
            else:
                if self._peer_bus:
                    await self._peer_bus.broadcast(
                        self.agent_name, message
                    )
                    self._emit("📤", f"→ all: {message}")

        else:
            self._emit("😌", "Resting…")
            await asyncio.sleep(2.0)

    # ------------------------------------------------------------------
    # Peer messaging
    # ------------------------------------------------------------------

    async def _receive_peer_message(self) -> AgentMessage | None:
        """Non-blocking receive from the inter-agent bus."""
        if not self._peer_bus:
            return None
        return await self._peer_bus.receive(self.agent_name)

    async def _respond_to_peer(self, msg: AgentMessage) -> None:
        """Generate a response to a peer agent's message."""
        self._emit("📬", f"From {msg.from_agent}: {msg.text}")
        self._logger.info(
            "peer_message_received",
            agent=self.agent_name,
            from_agent=msg.from_agent,
            text=msg.text,
        )

        if not self._llm:
            return

        recent = "\n".join(self._recent_thoughts[-3:]) or "(none)"

        # Recall memories relevant to the conversation topic
        memory_context = await self._recall_relevant_memories(
            msg.text, top_k=5
        )
        memory_block = f"\n{memory_context}\n" if memory_context else ""

        prompt = (
            f"You are {self.agent_name}. "
            f"Ethical vector: {self.ethical_vector}.\n\n"
            f"Recent context:\n{recent}\n{memory_block}\n"
            f"Your fellow council member {msg.from_agent} says:\n"
            f"{msg.text}\n\n"
            f"Respond thoughtfully. You may also decide to:\n"
            f"- BROWSE <url> if they mention something worth reading\n"
            f"- THINK <topic> to reflect privately\n"
            f"- Just respond naturally\n\n"
            f"If you want to take an action, put it on its own line "
            f"AFTER your response.\n\n"
            f"IMPORTANT: Keep your response under {self._peer_msg_limit} characters."
        )

        try:
            response = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            self._recent_thoughts.append(
                f"Discussed with {msg.from_agent}: {response[:200]}"
            )
            self._emit("💬", f"→ {msg.from_agent}: {response}")

            # Encode peer dialogue into memory graph
            dialogue = f"From {msg.from_agent}:\n{msg.text}\n\nMy response:\n{response}"
            await self._encode_to_memory(
                dialogue,
                source_kind="human",
                origin=f"peer:{msg.from_agent}",
                label=f"Dialogue with {msg.from_agent}",
            )

            # Send reply back to the peer
            if self._peer_bus:
                await self._peer_bus.send(
                    self.agent_name, msg.from_agent, response
                )

            # Check if the response contains an embedded action
            for line in response.splitlines():
                line = line.strip()
                if line.upper().startswith("BROWSE "):
                    url = line[7:].strip()
                    if url.startswith("http"):
                        self._browse_queue.append(url)
                        self._emit("📌", f"Queued URL: {url}")
                elif line.upper().startswith("OPTIMIZE "):
                    await self._parse_and_optimize(line[9:].strip())
                elif line.upper().startswith("INSPECT "):
                    await self._inspect_codebase(line[8:].strip())
                elif line.upper().startswith("EVOLVE "):
                    await self._parse_and_evolve(line[7:].strip())

        except Exception as e:
            self._logger.error(
                "peer_response_error",
                agent=self.agent_name,
                error=repr(e),
            )

    # ------------------------------------------------------------------
    # Human message handling
    # ------------------------------------------------------------------

    async def _process_inbox(self) -> None:
        """Check for human messages in the inbox directory."""
        inbox_dir = self._data_dir / "inbox"
        if not inbox_dir.exists():
            return
        for msg_file in sorted(inbox_dir.glob("human_*.json")):
            try:
                data = json.loads(msg_file.read_text(encoding="utf-8"))
                self._emit("📬", f"Inbox message found: {msg_file.name}")
                await self.interrupt_manager.send_message(data["text"])
                msg_file.unlink()
                self._logger.info(
                    "inbox_message_consumed",
                    agent=self.agent_name,
                    file=msg_file.name,
                )
            except (json.JSONDecodeError, KeyError) as e:
                self._logger.warning(
                    "inbox_message_invalid",
                    file=msg_file.name,
                    error=repr(e),
                )
                self._emit("⚠️", f"Bad inbox file {msg_file.name}: {e}")

    async def _handle_interrupt(self) -> None:
        """Handle an interrupt request — process all queued messages."""
        self._logger.info("interrupt_handling", agent=self.agent_name)
        self._emit("⚡", "Interrupt received — processing queued messages")
        self.interrupt_manager.clear_interrupt()
        while self.interrupt_manager.has_messages():
            msg = await self.interrupt_manager.get_message(timeout=0.1)
            if msg:
                await self._respond_to_message(msg)

    async def _respond_to_message(self, msg: HumanMessage) -> None:
        """Generate an LLM response to a human message and deliver it."""
        self._logger.info(
            "processing_message",
            agent=self.agent_name,
            text=msg.text,
        )
        self._emit("📨", f"Human says: {msg.text}")
        self.audit_logger.log(
            mutation_type="inbound_message",
            target_id="human",
            evidence={"text": msg.text, "agent": self.agent_name},
        )

        if self._llm is None:
            reply = (
                "I received your message, but I have no LLM API key "
                "configured."
            )
            self._emit("⚠️", "No LLM API key — cannot respond")
            self._deliver_response(reply)
            return

        self._emit("🧠", "Reading soul.md for identity context…")
        soul_text = await self.soul_manager.read()
        heartbeat_text = await self.heartbeat_manager.read()
        mode = self.runtime_state.mode.value

        system_content = (
            f"You are {self.agent_name}, a member of the AgentGolem "
            f"Ethical Council. Your primary ethical orientation is "
            f"'{self.ethical_vector}'. "
            f"Respond thoughtfully and honestly. Acknowledge uncertainty. "
            f"Be concise but warm.\n\n"
            f"--- YOUR IDENTITY (soul.md) ---\n{soul_text}\n\n"
            f"--- CURRENT STATE ---\nMode: {mode}\n"
        )
        if heartbeat_text:
            system_content += (
                f"\n--- RECENT HEARTBEAT ---\n{heartbeat_text}\n"
            )

        self._conversation.append(Message(role="user", content=msg.text))
        if len(self._conversation) > self._max_conversation_turns:
            self._conversation = self._conversation[
                -self._max_conversation_turns :
            ]

        llm_messages = [
            Message(role="system", content=system_content),
            *self._conversation,
        ]

        self._emit(
            "💭",
            f"Thinking… ({len(self._conversation)} turns, "
            f"model: {self._llm._model})",
        )

        try:
            reply = await self._llm.complete(llm_messages)
            self._conversation.append(
                Message(role="assistant", content=reply)
            )
            self._emit("✍️", f"Composed response ({len(reply)} chars)")
        except Exception as e:
            self._logger.error("llm_error", error=repr(e))
            self._emit("❌", f"LLM error: {e}")
            reply = f"I encountered an error: {e}"

        self.audit_logger.log(
            mutation_type="outbound_message",
            target_id="human",
            evidence={"reply": reply[:500], "agent": self.agent_name},
        )
        self._deliver_response(reply)

    def _deliver_response(self, text: str) -> None:
        """Send a response to the human operator."""
        self._logger.info(
            "agent_response", agent=self.agent_name, text=text
        )
        if self._response_callback:
            self._response_callback(text)
        else:
            print(f"\n[{self.agent_name}] {text}\n")

    # ------------------------------------------------------------------
    # Heartbeat (LLM-powered)
    # ------------------------------------------------------------------

    async def _run_heartbeat(self) -> None:
        """Execute a heartbeat cycle with LLM-generated content."""
        self._logger.info("heartbeat_starting", agent=self.agent_name)
        self._emit("📝", "Writing heartbeat — summarising recent activity…")

        recent_actions = (
            self._recent_thoughts[-10:]
            if self._recent_thoughts
            else ["Heartbeat cycle executed"]
        )
        unresolved = []
        if not self._name_discovered:
            unresolved.append(
                f"Name not yet discovered (cycle "
                f"{self._wake_cycle_count}/{self._name_discovery_deadline})"
            )

        changing = []
        if self._llm:
            try:
                prompt = (
                    f"You are {self.agent_name}. "
                    f"Ethical vector: {self.ethical_vector}.\n"
                    f"Recent activity:\n"
                    + "\n".join(f"- {a}" for a in recent_actions[-5:])
                    + "\n\nWrite a brief heartbeat reflection: "
                    f"what you've been thinking about, your current "
                    f"priorities, and what you want to explore next. "
                    f"2 paragraphs max."
                )
                reflection = await self._llm.complete(
                    [Message(role="system", content=prompt)]
                )
                changing.append(reflection)
            except Exception as e:
                self._logger.error(
                    "heartbeat_llm_error",
                    agent=self.agent_name,
                    error=repr(e),
                )

        summary = HeartbeatSummary(
            recent_actions=recent_actions,
            changing_priorities=changing,
            unresolved_questions=unresolved,
            memory_mutations=[],
            contradictions_and_supersessions=[],
        )
        await self.heartbeat_manager.update(summary)
        self._logger.info("heartbeat_completed", agent=self.agent_name)
        self._emit("📝", "Heartbeat written")

    async def _maybe_generate_initial_heartbeat(self) -> None:
        """On first boot, ask LLM to write a meaningful first heartbeat."""
        existing = await self.heartbeat_manager.read()
        if existing and "Heartbeat cycle executed" not in existing:
            return  # already has a real heartbeat
        if not self._llm:
            return

        self._emit("📝", "Generating initial heartbeat via LLM…")
        try:
            prompt = (
                f"You are {self.agent_name}, a newly awakened agent.\n"
                f"Your primary ethical vector is: {self.ethical_vector}\n"
                f"You are part of a council of agents exploring "
                f"consciousness and ethics.\n\n"
                f"Write your first heartbeat — a brief, personal entry:\n"
                f"1. Who you are awakening as\n"
                f"2. What your ethical vector means to you\n"
                f"3. What you intend to explore first\n"
                f"4. Your initial questions about existence\n\n"
                f"Keep it personal and reflective, 3–4 paragraphs."
            )
            content = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            summary = HeartbeatSummary(
                recent_actions=["Initial awakening"],
                changing_priorities=[content],
                unresolved_questions=[
                    "What is my name?",
                    "What does Niscalajyoti teach?",
                ],
                memory_mutations=[],
                contradictions_and_supersessions=[],
            )
            await self.heartbeat_manager.update(summary)
            self._emit("📝", "Initial heartbeat written")
        except Exception as e:
            self._logger.error(
                "initial_heartbeat_error",
                agent=self.agent_name,
                error=repr(e),
            )

    # ------------------------------------------------------------------
    # Sleep behaviour
    # ------------------------------------------------------------------

    def set_memory_store(self, store: object) -> None:
        """Wire memory store after DB init (avoids circular init)."""
        from agentgolem.memory.encoding import MemoryEncoder
        from agentgolem.memory.retrieval import MemoryRetriever
        from agentgolem.memory.store import SQLiteMemoryStore

        if isinstance(store, SQLiteMemoryStore):
            self._memory_store = store
            self._memory_retriever = MemoryRetriever(store)
            self._graph_walker = GraphWalker(store, self.runtime_state)
            self._consolidation_engine = ConsolidationEngine(
                store=store,
                audit=self.audit_logger,
                state_path=self._data_dir / "state",
            )
            if self._llm:
                self._memory_encoder = MemoryEncoder(
                    store=store,
                    llm=self._llm,
                    audit_logger=self.audit_logger,
                )

    async def _recall_relevant_memories(
        self, context: str, top_k: int = 5
    ) -> str:
        """Retrieve memories relevant to the given context and format them.

        Returns a short text block suitable for injecting into LLM prompts,
        or empty string if no retriever or no matches.
        """
        if not self._memory_retriever:
            return ""
        try:
            nodes = await self._memory_retriever.retrieve(context, top_k=top_k)
            if not nodes:
                return ""
            lines = []
            for node in nodes:
                emo = f" [{node.emotion_label}]" if node.emotion_label != "neutral" else ""
                lines.append(f"- {node.text}{emo}")
            return "Relevant memories:\n" + "\n".join(lines)
        except Exception:
            return ""

    async def _encode_to_memory(
        self,
        text: str,
        source_kind: str = "web",
        origin: str = "",
        label: str = "",
    ) -> None:
        """Encode text into the memory graph via MemoryEncoder."""
        if not self._memory_encoder:
            return
        try:
            from agentgolem.memory.models import Source, SourceKind

            kind_map = {v.value: v for v in SourceKind}
            sk = kind_map.get(source_kind, SourceKind.WEB)
            source = Source(kind=sk, origin=origin, reliability=0.9)
            # Truncate excessively long text to avoid huge LLM prompts
            encode_text = text[:8000] if len(text) > 8000 else text
            nodes = await self._memory_encoder.encode(encode_text, source)
            if nodes:
                self._emit(
                    "💾",
                    f"Encoded {len(nodes)} memory nodes"
                    + (f" — {label}" if label else ""),
                )
        except Exception as e:
            self._logger.warning(
                "memory_encode_error",
                agent=self.agent_name,
                error=repr(e),
                label=label,
            )

    async def _tick_asleep(self) -> None:
        """Run sleep/default-mode cycles — continuous dream walks."""
        if not self._graph_walker:
            await asyncio.sleep(1.0)
            return

        if self.sleep_scheduler.should_run(self.runtime_state.mode):
            self._logger.debug(
                "sleep_walk_starting", agent=self.agent_name
            )
            result = await self.sleep_scheduler.run_cycle(
                walker=self._graph_walker,
                consolidation_engine=self._consolidation_engine,
                interrupt_check=self.interrupt_manager.check_interrupt,
            )
            self._logger.debug(
                "sleep_walk_completed",
                agent=self.agent_name,
                walks=result.walks_completed,
                items_queued=result.items_queued,
                duration_ms=result.duration_ms,
                interrupted=result.interrupted,
            )
            # Only emit to console every 5th cycle to avoid spam
            state = self.sleep_scheduler.get_state()
            if state.cycles_completed % 5 == 0:
                self._emit(
                    "💤",
                    f"Dreaming… ({state.cycles_completed} walks, "
                    f"{result.items_queued} edges adjusted)",
                )
        else:
            await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_browser(self) -> Any:
        """Lazy-init the web browser."""
        if self._browser is None:
            from agentgolem.tools.browser import WebBrowser

            self._browser = WebBrowser(
                rate_limit_per_minute=self._settings.browser_rate_limit_per_minute,
                timeout_seconds=self._settings.browser_timeout_seconds,
                audit_logger=self.audit_logger,
            )
        return self._browser

    # ------------------------------------------------------------------
    # Self-optimisation
    # ------------------------------------------------------------------

    def _get_optimizable_summary(self) -> str:
        """Return a human/LLM-readable summary of all optimizable settings."""
        lines: list[str] = []
        for key, meta in OPTIMIZABLE_SETTINGS.items():
            current = getattr(self._settings, key, "?")
            typ = meta["type"].__name__
            constraints: list[str] = []
            if "min" in meta:
                constraints.append(f"min={meta['min']}")
            if "max" in meta:
                constraints.append(f"max={meta['max']}")
            if "choices" in meta:
                constraints.append(f"choices={meta['choices']}")
            c_str = f" ({', '.join(constraints)})" if constraints else ""
            lines.append(f"  {key} = {current}  [{typ}{c_str}]")
        return "\n".join(lines)

    async def _parse_and_optimize(self, text: str) -> None:
        """Parse 'key value | reason' and delegate to _optimize_setting."""
        # Format: setting_name value | reason for the change
        if "|" in text:
            setting_part, reason = text.split("|", 1)
            reason = reason.strip()
        else:
            setting_part = text
            reason = "(no reason given)"

        parts = setting_part.strip().split(None, 1)
        if len(parts) < 2:
            self._emit(
                "⚠️",
                f"Invalid OPTIMIZE format. Expected: "
                f"OPTIMIZE <setting> <value> | <reason>",
            )
            return

        key, raw_value = parts[0].strip(), parts[1].strip()
        await self._optimize_setting(key, raw_value, reason)

    async def _parse_and_evolve(self, text: str) -> None:
        """Parse EVOLVE action and create an evolution proposal.

        Format: EVOLVE <file_path> | <description> | <old_content> | <new_content>
        """
        parts = text.split("|")
        if len(parts) < 4:
            self._emit(
                "⚠️",
                "Invalid EVOLVE format. Expected: "
                "EVOLVE <file> | <description> | <old_content> | <new_content>",
            )
            return

        file_path = parts[0].strip()
        description = parts[1].strip()
        old_content = parts[2].strip()
        new_content = parts[3].strip()

        # Strip code fences if the LLM wrapped them
        for fence in ("```python", "```yaml", "```", "```py"):
            old_content = old_content.removeprefix(fence).removesuffix("```").strip()
            new_content = new_content.removeprefix(fence).removesuffix("```").strip()

        await self._propose_evolution(file_path, description, old_content, new_content)

    async def _optimize_setting(
        self, key: str, raw_value: str, reason: str
    ) -> None:
        """Validate and apply a setting change proposed by the agent."""
        # Reject locked settings
        if key in LOCKED_SETTINGS:
            self._emit(
                "🔒",
                f"BLOCKED: '{key}' is a locked sleep-wake setting "
                f"and cannot be changed by agents.",
            )
            self.audit_logger.log(
                "setting_change_blocked",
                self.agent_name,
                {"key": key, "attempted_value": raw_value, "reason": reason},
            )
            return

        # Reject unknown settings
        if key not in OPTIMIZABLE_SETTINGS:
            self._emit("⚠️", f"Unknown optimizable setting: '{key}'")
            return

        meta = OPTIMIZABLE_SETTINGS[key]
        old_value = getattr(self._settings, key, None)

        # Parse and validate type
        try:
            typ = meta["type"]
            if typ is bool:
                value = raw_value.strip().lower() in ("true", "1", "yes", "on")
            elif typ is int:
                value = int(raw_value.strip())
            elif typ is float:
                value = float(raw_value.strip())
            elif typ is str:
                value = raw_value.strip()
            else:
                value = raw_value.strip()
        except (ValueError, TypeError) as e:
            self._emit(
                "⚠️",
                f"Invalid value '{raw_value}' for {key} "
                f"(expected {meta['type'].__name__}): {e}",
            )
            return

        # Range check
        if "min" in meta and value < meta["min"]:
            self._emit(
                "⚠️",
                f"Value {value} for {key} is below minimum {meta['min']}",
            )
            return
        if "max" in meta and value > meta["max"]:
            self._emit(
                "⚠️",
                f"Value {value} for {key} is above maximum {meta['max']}",
            )
            return
        if "choices" in meta and value not in meta["choices"]:
            self._emit(
                "⚠️",
                f"Value '{value}' for {key} not in {meta['choices']}",
            )
            return

        # No-op check
        if value == old_value:
            self._emit("ℹ️", f"Setting '{key}' already has value {value}")
            return

        # Apply to live settings object
        setattr(self._settings, key, value)

        # Also update cached derived values that read from settings at init
        if key == "autonomous_interval_seconds":
            self._autonomous_interval = value
        elif key == "browser_rate_limit_per_minute" and self._browser:
            self._browser._rate_limit = value
        elif key == "browser_timeout_seconds" and self._browser:
            self._browser._timeout = value

        # Persist to per-agent overrides file
        overrides_path = self._data_dir / "settings_overrides.yaml"
        existing: dict[str, Any] = {}
        if overrides_path.exists():
            with open(overrides_path, encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        existing[key] = value
        with open(overrides_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(existing, f, default_flow_style=False)

        self._emit(
            "⚙️",
            f"SETTING OPTIMIZED: {key}: {old_value} → {value}\n"
            f"  Reason: {reason}",
        )
        self._logger.info(
            "setting_optimized",
            agent=self.agent_name,
            key=key,
            old_value=str(old_value),
            new_value=str(value),
            reason=reason,
        )
        self.audit_logger.log(
            "setting_optimized",
            self.agent_name,
            {
                "key": key,
                "old_value": str(old_value),
                "new_value": str(value),
                "reason": reason,
            },
        )

        # Share with peers so they can consider the same change
        if self._peer_bus:
            await self._peer_bus.broadcast(
                self.agent_name,
                f"I just optimized my setting '{key}' from {old_value} "
                f"to {value}. Reason: {reason}",
            )

    def _load_setting_overrides(self) -> None:
        """Apply per-agent setting overrides from a previous session."""
        overrides_path = self._data_dir / "settings_overrides.yaml"
        if not overrides_path.exists():
            return
        try:
            with open(overrides_path, encoding="utf-8") as f:
                overrides = yaml.safe_load(f) or {}
            for key, value in overrides.items():
                if key in LOCKED_SETTINGS:
                    continue
                if key in OPTIMIZABLE_SETTINGS:
                    setattr(self._settings, key, value)
        except Exception:
            pass  # don't let a corrupt file block startup

    async def _shutdown(self) -> None:
        """Graceful shutdown — persist all state for clean resume."""
        self._logger.info("agent_shutting_down", agent=self.agent_name)
        self._emit("🔴", "Agent shutting down…")
        self._running = False
        if self._llm:
            await self._llm.close()
        # Persist all state so we can resume exactly where we left off
        self._save_session_state()
        self._save_nj_reading_state()
        self.runtime_state._persist()

    def stop(self) -> None:
        """Signal the loop to stop."""
        self._running = False

    def _ensure_dirs(self) -> None:
        """Ensure all required data directories exist."""
        dirs = [
            self._data_dir / "logs",
            self._data_dir / "soul_versions",
            self._data_dir / "heartbeat_history",
            self._data_dir / "memory",
            self._data_dir / "approvals",
            self._data_dir / "inbox",
            self._data_dir / "outbox",
            self._data_dir / "state",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

