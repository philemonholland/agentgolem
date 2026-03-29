"""Command routing and dispatch."""
from __future__ import annotations

from typing import Any

from agentgolem.runtime.state import AgentMode, RuntimeState
from agentgolem.runtime.interrupts import InterruptManager


class CommandRouter:
    """Routes human commands to appropriate subsystems."""

    def __init__(
        self,
        runtime_state: RuntimeState,
        interrupt_manager: InterruptManager,
    ) -> None:
        self.runtime_state = runtime_state
        self.interrupt_manager = interrupt_manager

    async def wake(self) -> str:
        await self.runtime_state.transition(AgentMode.AWAKE)
        self.interrupt_manager.signal_resume()
        return "Agent is now AWAKE."

    async def sleep(self) -> str:
        await self.runtime_state.transition(AgentMode.ASLEEP)
        return "Agent is now ASLEEP."

    async def pause(self) -> str:
        await self.runtime_state.transition(AgentMode.PAUSED)
        await self.interrupt_manager.request_interrupt("pause")
        return "Agent is now PAUSED."

    async def resume(self) -> str:
        await self.runtime_state.transition(AgentMode.AWAKE)
        self.interrupt_manager.signal_resume()
        return "Agent RESUMED (now AWAKE)."

    async def status(self) -> dict[str, Any]:
        return self.runtime_state.to_dict()

    async def send_message(self, text: str) -> str:
        await self.interrupt_manager.send_message(text)
        return f"Message queued: {text[:80]}"
