"""Sandboxed file workspace rooted at agent_creations for agent-authored artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from agentgolem.tools.base import Tool, ToolArgument, ToolResult

DEFAULT_READ_MAX_CHARS = 12_000
MAX_LIST_ENTRIES = 200


class WorkspaceTool(Tool):
    """Read and write persistent text files inside the shared agent_creations workspace."""

    name = "workspace"
    description = "Read and write persistent text files under agent_creations"
    domains = ("creation", "planning", "collaboration")
    safety_class = "trusted_internal"
    side_effect_class = "local_write"
    supports_dry_run = True
    supported_actions = ("list", "read", "write", "append")
    action_descriptions = {
        "list": "List files and directories inside agent_creations",
        "read": "Read a text file from agent_creations",
        "write": "Create or overwrite a text file in agent_creations",
        "append": "Append text to a file in agent_creations",
    }
    action_arguments = {
        "list": (
            ToolArgument(
                "path",
                "Optional directory path relative to agent_creations",
                required=False,
            ),
        ),
        "read": (
            ToolArgument("path", "File path relative to agent_creations"),
            ToolArgument(
                "max_chars",
                "Optional maximum number of characters to return",
                kind="int",
                required=False,
            ),
        ),
        "write": (
            ToolArgument("path", "File path relative to agent_creations"),
            ToolArgument("content", "Text content to write"),
        ),
        "append": (
            ToolArgument("path", "File path relative to agent_creations"),
            ToolArgument("content", "Text content to append"),
        ),
    }
    usage_hint = "workspace.write(path=shared\\notes.md, content=Draft outline...)"

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._resolved_root = self._root.resolve()

    async def execute(self, action: str = "list", **kwargs: Any) -> ToolResult:
        """Dispatch one sandboxed workspace action."""
        action_name = action.strip().lower()
        if action_name == "list":
            return self._list_entries(path=str(kwargs.get("path", "")).strip())
        if action_name == "read":
            return self._read_file(
                path=str(kwargs.get("path", "")).strip(),
                max_chars=kwargs.get("max_chars"),
            )
        if action_name == "write":
            return self._write_file(
                path=str(kwargs.get("path", "")).strip(),
                content=str(kwargs.get("content", "")),
                append=False,
            )
        if action_name == "append":
            return self._write_file(
                path=str(kwargs.get("path", "")).strip(),
                content=str(kwargs.get("content", "")),
                append=True,
            )
        return ToolResult(success=False, error=f"Unknown action: {action}")

    def _resolve_path(self, raw_path: str, *, allow_root: bool) -> Path | None:
        """Resolve a workspace-relative path and reject traversal outside the root."""
        cleaned = raw_path.replace("/", "\\").strip().strip("\\")
        if not cleaned:
            return self._resolved_root if allow_root else None
        try:
            resolved = (self._resolved_root / cleaned).resolve()
        except (OSError, ValueError):
            return None
        if not resolved.is_relative_to(self._resolved_root):
            return None
        return resolved

    def _display_path(self, path: Path) -> str:
        """Return a Windows-style workspace-relative display path."""
        if path == self._resolved_root:
            return "."
        return str(path.relative_to(self._resolved_root)).replace("/", "\\")

    def _list_entries(self, *, path: str) -> ToolResult:
        target = self._resolve_path(path, allow_root=True)
        if target is None:
            return ToolResult(success=False, error="Workspace path escapes agent_creations")
        if not target.exists():
            return ToolResult(success=False, error=f"Workspace path not found: {path or '.'}")
        if not target.is_dir():
            return ToolResult(success=False, error="workspace.list requires a directory path")

        entries: list[str] = []
        sorted_entries = sorted(
            target.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.lower()),
        )
        for entry in sorted_entries[:MAX_LIST_ENTRIES]:
            relative = self._display_path(entry)
            entries.append(relative + ("\\" if entry.is_dir() else ""))

        return ToolResult(
            success=True,
            data={
                "root": "agent_creations",
                "path": self._display_path(target),
                "entries": entries,
                "count": len(entries),
            },
        )

    def _read_file(self, *, path: str, max_chars: Any) -> ToolResult:
        target = self._resolve_path(path, allow_root=False)
        if target is None:
            return ToolResult(success=False, error="Workspace path escapes agent_creations")
        if not target.exists():
            return ToolResult(success=False, error=f"Workspace file not found: {path}")
        if target.is_dir():
            return ToolResult(success=False, error="workspace.read requires a file path")

        text = target.read_text(encoding="utf-8", errors="replace")
        limit = DEFAULT_READ_MAX_CHARS
        if max_chars not in (None, ""):
            limit = max(1, int(max_chars))
        truncated = len(text) > limit
        visible_text = text[:limit] + ("\n[…truncated]" if truncated else "")

        return ToolResult(
            success=True,
            data={
                "root": "agent_creations",
                "path": self._display_path(target),
                "chars": len(text),
                "truncated": truncated,
                "content": visible_text,
            },
        )

    def _write_file(self, *, path: str, content: str, append: bool) -> ToolResult:
        target = self._resolve_path(path, allow_root=False)
        if target is None:
            return ToolResult(success=False, error="Workspace path escapes agent_creations")
        if target.exists() and target.is_dir():
            return ToolResult(success=False, error="Workspace target is a directory")

        target.parent.mkdir(parents=True, exist_ok=True)
        created = not target.exists()
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8") as handle:
            handle.write(content)

        return ToolResult(
            success=True,
            data={
                "root": "agent_creations",
                "path": self._display_path(target),
                "created": created,
                "chars_written": len(content),
                "action": "append" if append else "write",
            },
        )
