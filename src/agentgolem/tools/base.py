"""Core tool abstractions: Tool, ToolResult, ToolRegistry, ApprovalGate."""
from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agentgolem.logging.audit import AuditLogger


class ToolResult(BaseModel):
    """Structured result returned by every tool invocation."""

    success: bool
    data: Any = None
    error: str | None = None


class Tool(ABC):
    """Abstract base for all agent tools."""

    name: str
    description: str
    requires_approval: bool = False
    supports_dry_run: bool = False

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult: ...

    async def dry_run(self, **kwargs: Any) -> ToolResult:
        """Default dry_run returns what would happen."""
        return ToolResult(success=True, data={"dry_run": True, "kwargs": kwargs})


class ApprovalGate:
    """File-based human-in-the-loop approval mechanism."""

    def __init__(self, approvals_dir: Path, required_actions: list[str]) -> None:
        self._dir = approvals_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._required = required_actions

    def requires_approval(self, action_name: str) -> bool:
        """Check if *action_name* is in the required-actions list."""
        return action_name in self._required

    def request_approval(self, action_name: str, context: dict[str, Any]) -> str:
        """Write an approval request file and return its request_id."""
        request_id = uuid.uuid4().hex
        payload = {
            "request_id": request_id,
            "action": action_name,
            "context": context,
            "status": "pending",
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
        data["resolved_at"] = datetime.now(timezone.utc).isoformat()
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

    async def invoke(self, name: str, *, dry_run: bool = False, **kwargs: Any) -> ToolResult:
        """Invoke a tool by name with optional dry-run and approval checks."""
        tool = self.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"Unknown tool: {name}")

        # Dry-run path
        if dry_run and tool.supports_dry_run:
            result = await tool.dry_run(**kwargs)
            self._log(name, "dry_run", kwargs, result)
            return result

        # Approval path
        if tool.requires_approval and self._gate is not None:
            if self._gate.requires_approval(name):
                request_id = self._gate.request_approval(name, kwargs)
                result = ToolResult(
                    success=True,
                    data={"approval_pending": True, "request_id": request_id},
                )
                self._log(name, "approval_requested", kwargs, result)
                return result

        # Normal execution
        result = await tool.execute(**kwargs)
        self._log(name, "execute", kwargs, result)
        return result

    # ------------------------------------------------------------------
    def _log(self, tool_name: str, action: str, kwargs: dict[str, Any], result: ToolResult) -> None:
        self._audit.log(
            mutation_type=f"tool.{action}",
            target_id=tool_name,
            evidence={"kwargs": kwargs, "result": result.model_dump()},
        )
