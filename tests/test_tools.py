"""Tests for the tool framework: Tool, ToolResult, ToolRegistry, ApprovalGate."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentgolem.logging.audit import AuditLogger
from agentgolem.tools.base import ApprovalGate, Tool, ToolRegistry, ToolResult


# ── Concrete test tools ─────────────────────────────────────────────


class EchoTool(Tool):
    name = "echo"
    description = "Echoes input"

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data=kwargs)


class DryRunTool(Tool):
    name = "dry_echo"
    description = "Echoes with dry-run support"
    supports_dry_run = True

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data=kwargs)


class ApprovalTool(Tool):
    name = "danger"
    description = "Dangerous action requiring approval"
    requires_approval = True

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data=kwargs)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "approvals").mkdir()
    return tmp_path


@pytest.fixture
def audit(data_dir: Path) -> AuditLogger:
    return AuditLogger(data_dir)


@pytest.fixture
def gate(data_dir: Path) -> ApprovalGate:
    return ApprovalGate(data_dir / "approvals", required_actions=["danger"])


@pytest.fixture
def registry(audit: AuditLogger, gate: ApprovalGate) -> ToolRegistry:
    reg = ToolRegistry(audit_logger=audit, approval_gate=gate)
    reg.register(EchoTool())
    reg.register(DryRunTool())
    reg.register(ApprovalTool())
    return reg


# ── ToolResult ───────────────────────────────────────────────────────


def test_tool_result_model() -> None:
    r = ToolResult(success=True, data={"key": "value"})
    assert r.success is True
    assert r.data == {"key": "value"}
    assert r.error is None

    r2 = ToolResult(success=False, error="boom")
    assert r2.success is False
    assert r2.error == "boom"


# ── Tool ABC ─────────────────────────────────────────────────────────


def test_tool_abstract_requires_execute() -> None:
    with pytest.raises(TypeError):
        Tool()  # type: ignore[abstract]


async def test_tool_dry_run_default() -> None:
    tool = EchoTool()
    result = await tool.dry_run(msg="hi")
    assert result.success is True
    assert result.data["dry_run"] is True
    assert result.data["kwargs"] == {"msg": "hi"}


# ── ToolRegistry ─────────────────────────────────────────────────────


def test_registry_register_and_get(registry: ToolRegistry) -> None:
    tool = registry.get("echo")
    assert tool is not None
    assert tool.name == "echo"
    assert registry.get("nonexistent") is None


def test_registry_list_tools(registry: ToolRegistry) -> None:
    names = registry.list_tools()
    assert "danger" in names
    assert "dry_echo" in names
    assert "echo" in names


async def test_registry_invoke(registry: ToolRegistry) -> None:
    result = await registry.invoke("echo", msg="hello")
    assert result.success is True
    assert result.data == {"msg": "hello"}


async def test_registry_invoke_dry_run(registry: ToolRegistry) -> None:
    result = await registry.invoke("dry_echo", dry_run=True, msg="test")
    assert result.success is True
    assert result.data["dry_run"] is True


async def test_registry_invoke_unknown_tool(registry: ToolRegistry) -> None:
    result = await registry.invoke("no_such_tool")
    assert result.success is False
    assert "Unknown tool" in (result.error or "")


# ── ApprovalGate ─────────────────────────────────────────────────────


def test_approval_gate_requires(gate: ApprovalGate) -> None:
    assert gate.requires_approval("danger") is True
    assert gate.requires_approval("echo") is False


def test_approval_gate_request_and_check(gate: ApprovalGate) -> None:
    req_id = gate.request_approval("danger", {"reason": "test"})
    assert isinstance(req_id, str)
    assert gate.check_approval(req_id) == "pending"


def test_approval_gate_approve(gate: ApprovalGate) -> None:
    req_id = gate.request_approval("danger", {"reason": "test"})
    gate.approve(req_id, reason="lgtm")
    assert gate.check_approval(req_id) == "approved"


def test_approval_gate_deny(gate: ApprovalGate) -> None:
    req_id = gate.request_approval("danger", {"reason": "test"})
    gate.deny(req_id, reason="nope")
    assert gate.check_approval(req_id) == "denied"


def test_approval_gate_get_pending(gate: ApprovalGate) -> None:
    id1 = gate.request_approval("danger", {"a": 1})
    id2 = gate.request_approval("danger", {"b": 2})
    gate.approve(id1, reason="ok")

    pending = gate.get_pending()
    assert len(pending) == 1
    assert pending[0]["request_id"] == id2


# ── Registry + Approval integration ─────────────────────────────────


async def test_registry_invoke_requires_approval(registry: ToolRegistry) -> None:
    result = await registry.invoke("danger", target="x")
    assert result.success is True
    assert result.data["approval_pending"] is True
    assert "request_id" in result.data
