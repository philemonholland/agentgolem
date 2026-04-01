"""Tests for the shared agent_creations workspace tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentgolem.tools.workspace import WorkspaceTool

if TYPE_CHECKING:
    from pathlib import Path


async def test_workspace_tool_can_write_read_append_and_list(tmp_path: Path) -> None:
    tool = WorkspaceTool(tmp_path / "agent_creations")

    initial = await tool.execute(action="list")
    assert initial.success is True
    assert initial.data["entries"] == []

    write_result = await tool.execute(
        action="write",
        path="shared\\story.txt",
        content="Once",
    )
    assert write_result.success is True
    assert write_result.data["created"] is True

    append_result = await tool.execute(
        action="append",
        path="shared\\story.txt",
        content="\nupon a time",
    )
    assert append_result.success is True
    assert append_result.data["action"] == "append"

    read_result = await tool.execute(action="read", path="shared\\story.txt")
    assert read_result.success is True
    assert read_result.data["content"] == "Once\nupon a time"

    listing = await tool.execute(action="list", path="shared")
    assert listing.success is True
    assert listing.data["entries"] == ["shared\\story.txt"]


async def test_workspace_tool_blocks_path_escape(tmp_path: Path) -> None:
    tool = WorkspaceTool(tmp_path / "agent_creations")

    result = await tool.execute(
        action="write",
        path="..\\outside.txt",
        content="should fail",
    )

    assert result.success is False
    assert result.error == "Workspace path escapes agent_creations"
