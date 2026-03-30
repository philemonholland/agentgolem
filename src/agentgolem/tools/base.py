"""Core tool abstractions: Tool, ToolResult, ToolRegistry, ApprovalGate."""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from pathlib import Path

    from agentgolem.logging.audit import AuditLogger


class ToolResult(BaseModel):
    """Structured result returned by every tool invocation."""

    success: bool
    data: Any = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ToolArgument:
    """Machine-readable description of one tool argument."""

    name: str
    description: str
    kind: str = "str"
    required: bool = True


@dataclass(frozen=True, slots=True)
class ToolActionSpec:
    """Prompt-facing metadata for one concrete tool action."""

    tool_name: str
    action_name: str
    capability_name: str
    description: str
    domains: tuple[str, ...] = ()
    argument_spec: tuple[ToolArgument, ...] = ()
    safety_class: str = "trusted_internal"
    side_effect_class: str = "none"
    requires_approval: bool = False
    supports_dry_run: bool = False
    approval_action_name: str | None = None
    usage_hint: str = ""
    available: bool = True


def format_capability_summary(specs: list[ToolActionSpec]) -> str:
    """Render a compact prompt-facing summary for capability specs."""
    lines: list[str] = []
    for spec in sorted(specs, key=lambda item: item.capability_name):
        args = ", ".join(arg.name for arg in spec.argument_spec)
        signature = f"{spec.capability_name}({args})" if args else spec.capability_name
        domains = ", ".join(spec.domains) if spec.domains else "general"
        flags: list[str] = [f"domains={domains}", f"effects={spec.side_effect_class}"]
        if spec.requires_approval and spec.approval_action_name:
            flags.append(f"approval={spec.approval_action_name}")
        if spec.supports_dry_run:
            flags.append("dry_run")
        if not spec.available:
            flags.append("unavailable")
        line = f"- {signature} — {spec.description} [{'; '.join(flags)}]"
        if spec.usage_hint:
            line += f" Usage: {spec.usage_hint}"
        lines.append(line)
    return "\n".join(lines)


class Tool(ABC):
    """Abstract base for all agent tools."""

    name: str
    description: str
    requires_approval: bool = False
    supports_dry_run: bool = False
    domains: tuple[str, ...] = ()
    safety_class: str = "trusted_internal"
    side_effect_class: str = "none"
    usage_hint: str = ""
    supported_actions: tuple[str, ...] = ("execute",)
    action_descriptions: dict[str, str] = {}
    action_arguments: dict[str, tuple[ToolArgument, ...]] = {}

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult: ...

    async def dry_run(self, **kwargs: Any) -> ToolResult:
        """Default dry_run returns what would happen."""
        return ToolResult(success=True, data={"dry_run": True, "kwargs": kwargs})

    def is_available(self) -> bool:
        """Return whether this tool is currently usable."""
        return True

    def requires_approval_for(self, action: str) -> bool:
        """Return whether *action* needs approval before execution."""
        return self.requires_approval

    def approval_action_name(self, action: str) -> str:
        """Return the approval-gate action name for *action*."""
        return self.name if action in ("", "execute") else f"{self.name}_{action}"

    def get_action_description(self, action: str) -> str:
        """Return human-readable description for *action*."""
        return self.action_descriptions.get(action, self.description)

    def get_action_arguments(self, action: str) -> tuple[ToolArgument, ...]:
        """Return machine-readable argument metadata for *action*."""
        return self.action_arguments.get(action, ())

    def action_specs(self) -> tuple[ToolActionSpec, ...]:
        """Return prompt-facing capability metadata for supported actions."""
        specs: list[ToolActionSpec] = []
        for action in self.supported_actions:
            capability_name = self.name if action in ("", "execute") else f"{self.name}.{action}"
            requires_approval = self.requires_approval_for(action)
            specs.append(
                ToolActionSpec(
                    tool_name=self.name,
                    action_name=action,
                    capability_name=capability_name,
                    description=self.get_action_description(action),
                    domains=self.domains,
                    argument_spec=self.get_action_arguments(action),
                    safety_class=self.safety_class,
                    side_effect_class=self.side_effect_class,
                    requires_approval=requires_approval,
                    supports_dry_run=self.supports_dry_run,
                    approval_action_name=(
                        self.approval_action_name(action) if requires_approval else None
                    ),
                    usage_hint=self.usage_hint,
                    available=self.is_available(),
                )
            )
        return tuple(specs)


class ApprovalGate:
    """File-based human-in-the-loop approval mechanism."""

    def __init__(self, approvals_dir: Path, required_actions: list[str]) -> None:
        self._dir = approvals_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._required = required_actions

    def requires_approval(self, action_name: str) -> bool:
        """Check if *action_name* is in the required-actions list."""
        return action_name in self._required

    def update_required_actions(self, required_actions: list[str]) -> None:
        """Replace the approval-required action set at runtime."""
        self._required = required_actions

    def request_approval(self, action_name: str, context: dict[str, Any]) -> str:
        """Write an approval request file and return its request_id."""
        request_id = uuid.uuid4().hex
        payload = {
            "request_id": request_id,
            "action": action_name,
            "context": context,
            "status": "pending",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        path = self._dir / f"{request_id}.json"
        path.write_text(json.dumps(payload, default=str), encoding="utf-8")
        return request_id

    def check_approval(self, request_id: str) -> str:
        """Return the current status of an approval request."""
        path = self._dir / f"{request_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["status"]

    def approve(self, request_id: str, reason: str = "") -> None:
        """Mark the request as approved."""
        self._update_status(request_id, "approved", reason)

    def deny(self, request_id: str, reason: str = "") -> None:
        """Mark the request as denied."""
        self._update_status(request_id, "denied", reason)

    def get_pending(self) -> list[dict[str, Any]]:
        """Return all requests that are still pending."""
        pending: list[dict[str, Any]] = []
        for path in self._dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("status") == "pending":
                pending.append(data)
        return pending

    # ------------------------------------------------------------------
    def _update_status(self, request_id: str, status: str, reason: str) -> None:
        path = self._dir / f"{request_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = status
        data["reason"] = reason
        data["resolved_at"] = datetime.now(UTC).isoformat()
        path.write_text(json.dumps(data, default=str), encoding="utf-8")


class ToolRegistry:
    """Central registry for discovering and invoking tools."""

    def __init__(
        self,
        audit_logger: AuditLogger,
        approval_gate: ApprovalGate | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._audit = audit_logger
        self._gate = approval_gate

    def register(self, tool: Tool) -> None:
        """Register a tool by its *name* attribute."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """Return sorted list of registered tool names."""
        return sorted(self._tools)

    def list_capabilities(self) -> list[ToolActionSpec]:
        """Return sorted action-level capability metadata for all tools."""
        capabilities: list[ToolActionSpec] = []
        for tool in self._tools.values():
            capabilities.extend(tool.action_specs())
        return sorted(capabilities, key=lambda spec: spec.capability_name)

    def get_capability(self, capability_name: str) -> ToolActionSpec | None:
        """Look up one action-level capability by name."""
        for spec in self.list_capabilities():
            if spec.capability_name == capability_name:
                return spec
        return None

    def prompt_summary(self) -> str:
        """Return a compact prompt-facing summary of registered capabilities."""
        return format_capability_summary(self.list_capabilities())

    def enrichment_guidance(self) -> str:
        """Return a stable hint describing how tools should be extended."""
        return (
            "To extend the toolbox, inspect src/agentgolem/tools/base.py and the "
            "existing tools in src/agentgolem/tools/. Add a typed tool class, add "
            "tests under tests/, and propose the code change through the audited "
            "evolution path rather than runtime plugin loading."
        )

    async def invoke(self, name: str, *, dry_run: bool = False, **kwargs: Any) -> ToolResult:
        """Invoke a tool by name with optional dry-run and approval checks."""
        tool = self.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"Unknown tool: {name}")
        action_name = str(kwargs.get("action", "execute"))
        capability_name = name if action_name in ("", "execute") else f"{name}.{action_name}"

        # Dry-run path
        if dry_run and tool.supports_dry_run:
            result = await tool.dry_run(**kwargs)
            self._log(capability_name, "dry_run", kwargs, result)
            return result

        # Approval path
        approval_action = tool.approval_action_name(action_name)
        if (
            tool.requires_approval_for(action_name)
            and self._gate is not None
            and self._gate.requires_approval(approval_action)
        ):
            request_id = self._gate.request_approval(approval_action, kwargs)
            result = ToolResult(
                success=True,
                data={
                    "approval_pending": True,
                    "request_id": request_id,
                    "approval_action": approval_action,
                    "capability": capability_name,
                },
            )
            self._log(capability_name, "approval_requested", kwargs, result)
            return result

        # Normal execution
        result = await tool.execute(**kwargs)
        self._log(capability_name, "execute", kwargs, result)
        return result

    # ------------------------------------------------------------------
    def _log(self, tool_name: str, action: str, kwargs: dict[str, Any], result: ToolResult) -> None:
        self._audit.log(
            mutation_type=f"tool.{action}",
            target_id=tool_name,
            evidence={"kwargs": kwargs, "result": result.model_dump()},
        )
