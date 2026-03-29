"""Tool framework: abstract base, registry, and approval gate."""
from __future__ import annotations

from agentgolem.tools.base import ApprovalGate, Tool, ToolRegistry, ToolResult

__all__ = ["ApprovalGate", "Tool", "ToolRegistry", "ToolResult"]