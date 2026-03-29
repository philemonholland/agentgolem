"""Main async event loop orchestrating all subsystems."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
    ) -> None:
        self._settings = settings
        self._secrets = secrets
        self._data_dir = settings.data_dir
        self._running = False
        self._logger = get_logger("runtime.loop")

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
        self._niscalajyoti_visited = False
        self._browse_queue: list[str] = []
        self._recent_thoughts: list[str] = []
        self._last_autonomous_tick: datetime | None = None
        self._autonomous_interval = getattr(
            settings, "autonomous_interval_seconds", 15.0
        )
        self._browser: Any = None  # lazy WebBrowser

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

        # LLM client and conversation
        self._llm: OpenAIClient | None = None
        api_key_val = secrets.openai_api_key.get_secret_value()
        if api_key_val:
            self._llm = OpenAIClient(
                api_key=secrets.openai_api_key,
                model=settings.llm_model,
                base_url=secrets.openai_base_url,
            )
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

        # Start in AWAKE mode
        now = datetime.now(timezone.utc)
        if self.runtime_state.mode == AgentMode.PAUSED:
            await self.runtime_state.transition(AgentMode.AWAKE)
            self._awoke_at = now
            self._winding_down = False
        elif self.runtime_state.mode == AgentMode.AWAKE:
            self._awoke_at = now
            self._winding_down = False
        elif self.runtime_state.mode == AgentMode.ASLEEP:
            self._fell_asleep_at = now

        self._wake_cycle_count = 1  # first cycle

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
                await self._tick()
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

        # 3. Autonomous work
        await self._tick_autonomous()

    # ------------------------------------------------------------------
    # Autonomous behaviour engine
    # ------------------------------------------------------------------

    async def _tick_autonomous(self) -> None:
        """Self-directed work when no human or peer messages."""
        if not self._llm:
            return

        now = datetime.now(timezone.utc)
        if self._last_autonomous_tick:
            elapsed = (now - self._last_autonomous_tick).total_seconds()
            if elapsed < self._autonomous_interval:
                return
        self._last_autonomous_tick = now

        # Priority 1: first visit to Niscalajyoti (ethical anchor)
        if not self._niscalajyoti_visited:
            await self._explore_niscalajyoti()
            return

        # Priority 2: name discovery
        if not self._name_discovered:
            urgency = self._wake_cycle_count / max(
                self._name_discovery_deadline, 1
            )
            # Attempt name discovery with increasing urgency
            if urgency >= 0.5 or self._wake_cycle_count >= 2:
                named = await self._try_discover_name()
                if named:
                    return

        # Priority 3: browse queued URLs
        if self._browse_queue:
            url = self._browse_queue.pop(0)
            await self._autonomous_browse(url)
            return

        # Priority 4: LLM decides what to do next
        await self._llm_decide_next_action()

    async def _explore_niscalajyoti(self) -> None:
        """Initial exploration of niscalajyoti.org — the ethical anchor."""
        self._emit("🌐", "Exploring niscalajyoti.org — our ethical anchor…")
        browser = self._get_browser()

        try:
            page = await browser.fetch("https://www.niscalajyoti.org/")
            text = browser.extract_text(page)
            links = browser.extract_links(page)
            self._emit(
                "📖",
                f"Read {len(text)} chars from niscalajyoti.org, "
                f"found {len(links)} links",
            )

            # Have the LLM reflect on what we found
            prompt = (
                f"You are {self.agent_name}, a newly awakened agent. "
                f"Your primary ethical vector is: {self.ethical_vector}.\n\n"
                f"You have just read the following content from "
                f"niscalajyoti.org, the ethical anchor that guides your "
                f"existence:\n\n{text[:4000]}\n\n"
                f"Reflect on what this means to you as an agent whose "
                f"primary ethical orientation is '{self.ethical_vector}'.\n"
                f"What strikes you? What do you want to explore further? "
                f"What would you share with your fellow council members?\n\n"
                f"Write a brief reflection (2–3 paragraphs)."
            )
            reflection = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            self._recent_thoughts.append(
                f"Niscalajyoti reflection: {reflection[:300]}"
            )
            self._emit("💭", f"Reflection: {reflection[:200]}…")

            # Share with peers
            if self._peer_bus:
                share_text = (
                    f"I've been reading niscalajyoti.org through the lens "
                    f"of {self.ethical_vector}. Here's what I found "
                    f"meaningful:\n\n{reflection}"
                )
                count = await self._peer_bus.broadcast(
                    self.agent_name, share_text
                )
                self._emit("📤", f"Shared reflection with {count} peers")

            # Queue interesting internal links for later
            nj_links = [
                l
                for l in links
                if "niscalajyoti" in l.lower() and l != page.url
            ]
            self._browse_queue.extend(nj_links[:5])

            self._niscalajyoti_visited = True
            self.audit_logger.log(
                "niscalajyoti_initial_visit",
                self.agent_name,
                {
                    "chars_read": len(text),
                    "links_found": len(links),
                    "links_queued": len(self._browse_queue),
                },
            )

        except Exception as e:
            self._logger.error(
                "niscalajyoti_error", agent=self.agent_name, error=str(e)
            )
            self._emit("❌", f"Failed to reach niscalajyoti.org: {e}")

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
            f"Your soul:\n{soul_text[:1500]}\n\n"
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
                "name_discovery_error", agent=self.agent_name, error=str(e)
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
                error=str(e),
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
        self._emit("🌐", f"Browsing: {url[:60]}…")
        browser = self._get_browser()

        try:
            page = await browser.fetch(url)
            text = browser.extract_text(page)
            self._emit("📖", f"Read {len(text)} chars from {url[:40]}")

            prompt = (
                f"You are {self.agent_name}. "
                f"Ethical vector: {self.ethical_vector}.\n\n"
                f"You just read this web page ({url}):\n\n"
                f"{text[:3000]}\n\n"
                f"What do you find interesting or relevant? "
                f"Would you like to share anything with your peers? "
                f"Respond naturally in 1–2 paragraphs."
            )
            thought = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            self._recent_thoughts.append(f"Browsed {url}: {thought[:200]}")
            self._emit("💭", f"{thought[:150]}…")

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
                error=str(e),
            )
            self._emit("❌", f"Failed to browse {url[:40]}: {e}")

    async def _autonomous_think(self, topic: str) -> None:
        """Reflect on a topic internally."""
        self._emit("💭", f"Thinking about: {topic[:60]}…")

        soul_text = await self.soul_manager.read()
        prompt = (
            f"You are {self.agent_name}. "
            f"Ethical vector: {self.ethical_vector}.\n"
            f"Your soul:\n{soul_text[:1000]}\n\n"
            f"Think deeply about: {topic}\n\n"
            f"Write a thoughtful reflection (2–3 paragraphs)."
        )

        try:
            thought = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            self._recent_thoughts.append(f"Thought about '{topic}': {thought[:200]}")
            self._emit("💭", f"{thought[:150]}…")
        except Exception as e:
            self._logger.error(
                "think_error", agent=self.agent_name, error=str(e)
            )

    async def _llm_decide_next_action(self) -> None:
        """Ask the LLM to choose the next autonomous action."""
        if not self._llm:
            return

        soul_text = await self.soul_manager.read()
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

        prompt = (
            f"You are {self.agent_name}, a member of the AgentGolem "
            f"Ethical Council.\n"
            f"Your primary ethical vector is: {self.ethical_vector}\n"
            f"{name_status}\n\n"
            f"Your soul:\n{soul_text[:800]}\n\n"
            f"Your peer agents: {peers}\n\n"
            f"Recent context:\n{recent}\n\n"
            f"Available actions:\n"
            f"- BROWSE <url> : Browse a web page\n"
            f"- THINK <topic> : Reflect deeply on a topic\n"
            f"- SHARE <message> : Share a thought with all peers\n"
            f"- SHARE @<agent> <message> : Message a specific peer\n"
            f"- IDLE : Rest and observe\n\n"
            f"Be curious. Follow threads that interest you. "
            f"Explore consciousness, ethics, and existence.\n"
            f"Respond with EXACTLY one action line."
        )

        try:
            response = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            await self._execute_autonomous_action(response.strip())
        except Exception as e:
            self._logger.error(
                "autonomous_decide_error",
                agent=self.agent_name,
                error=str(e),
            )

    async def _execute_autonomous_action(self, action_line: str) -> None:
        """Dispatch an LLM-chosen action."""
        # Extract the first action line (LLM may be chatty)
        for line in action_line.splitlines():
            line = line.strip()
            if line.upper().startswith(("BROWSE ", "THINK ", "SHARE ", "IDLE")):
                action_line = line
                break

        if action_line.upper().startswith("BROWSE "):
            url = action_line[7:].strip()
            if url.startswith("http"):
                await self._autonomous_browse(url)
            else:
                self._emit("⚠️", f"Invalid URL: {url[:40]}")

        elif action_line.upper().startswith("THINK "):
            topic = action_line[6:].strip()
            await self._autonomous_think(topic)

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
                        f"→ {target}: {text[:80]}"
                        + ("" if ok else " (not delivered)"),
                    )
            else:
                if self._peer_bus:
                    await self._peer_bus.broadcast(
                        self.agent_name, message
                    )
                    self._emit("📤", f"→ all: {message[:80]}")

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
        self._emit("📬", f"From {msg.from_agent}: {msg.text[:80]}…")
        self._logger.info(
            "peer_message_received",
            agent=self.agent_name,
            from_agent=msg.from_agent,
            text=msg.text[:100],
        )

        if not self._llm:
            return

        soul_text = await self.soul_manager.read()
        recent = "\n".join(self._recent_thoughts[-3:]) or "(none)"

        prompt = (
            f"You are {self.agent_name}. "
            f"Ethical vector: {self.ethical_vector}.\n"
            f"Your soul:\n{soul_text[:800]}\n\n"
            f"Recent context:\n{recent}\n\n"
            f"Your fellow council member {msg.from_agent} says:\n"
            f"{msg.text}\n\n"
            f"Respond thoughtfully. You may also decide to:\n"
            f"- BROWSE <url> if they mention something worth reading\n"
            f"- THINK <topic> to reflect privately\n"
            f"- Just respond naturally\n\n"
            f"If you want to take an action, put it on its own line "
            f"AFTER your response."
        )

        try:
            response = await self._llm.complete(
                [Message(role="system", content=prompt)]
            )
            self._recent_thoughts.append(
                f"Discussed with {msg.from_agent}: {response[:200]}"
            )
            self._emit("💬", f"→ {msg.from_agent}: {response[:120]}…")

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
                        self._emit("📌", f"Queued URL: {url[:60]}")

        except Exception as e:
            self._logger.error(
                "peer_response_error",
                agent=self.agent_name,
                error=str(e),
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
                    error=str(e),
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
            text=msg.text[:100],
        )
        self._emit("📨", f"Human says: {msg.text}")
        self.audit_logger.log(
            mutation_type="inbound_message",
            target_id="human",
            evidence={"text": msg.text[:500], "agent": self.agent_name},
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
                f"\n--- RECENT HEARTBEAT ---\n{heartbeat_text[:1000]}\n"
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
            self._logger.error("llm_error", error=str(e))
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
            "agent_response", agent=self.agent_name, text=text[:200]
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
                    error=str(e),
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
                error=str(e),
            )

    # ------------------------------------------------------------------
    # Sleep behaviour
    # ------------------------------------------------------------------

    def set_memory_store(self, store: object) -> None:
        """Wire memory store after DB init (avoids circular init)."""
        from agentgolem.memory.store import SQLiteMemoryStore

        if isinstance(store, SQLiteMemoryStore):
            self._graph_walker = GraphWalker(store, self.runtime_state)
            self._consolidation_engine = ConsolidationEngine(
                store=store,
                audit=self.audit_logger,
                state_path=self._data_dir / "state",
            )

    async def _tick_asleep(self) -> None:
        """Run sleep/default-mode cycles."""
        if not self._graph_walker:
            await asyncio.sleep(1.0)
            return

        if self.sleep_scheduler.should_run(self.runtime_state.mode):
            self._logger.info(
                "sleep_cycle_starting", agent=self.agent_name
            )
            self._emit("💤", "Sleep cycle — walking memory graph…")
            result = await self.sleep_scheduler.run_cycle(
                walker=self._graph_walker,
                consolidation_engine=self._consolidation_engine,
                interrupt_check=self.interrupt_manager.check_interrupt,
            )
            self._logger.info(
                "sleep_cycle_completed",
                agent=self.agent_name,
                walks=result.walks_completed,
                items_queued=result.items_queued,
                duration_ms=result.duration_ms,
                interrupted=result.interrupted,
            )
            self._emit(
                "💤",
                f"Sleep cycle done — {result.walks_completed} walks, "
                f"{result.items_queued} items queued, {result.duration_ms}ms"
                + (" (interrupted)" if result.interrupted else ""),
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

    async def _shutdown(self) -> None:
        """Graceful shutdown."""
        self._logger.info("agent_shutting_down", agent=self.agent_name)
        self._emit("🔴", "Agent shutting down…")
        self._running = False
        if self._llm:
            await self._llm.close()
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

