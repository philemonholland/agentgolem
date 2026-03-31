"""Vow document loader — reads curated JSON ethics documents for agents.

Loads the foundational ethical framework from ``docs/vow_agents/``:

- ``common/`` — four files every agent absorbs (five vows, soil, calibration,
  integrity protocols).
- ``agent_specific/aX.json`` — one file per agent (1-6) tailored to their vow.
  Agent 7 (devil's advocate) has no agent-specific file.

The JSON is rendered into readable prose for LLM prompts.  No web fetches, no
digest step, no caching needed — the files are pre-curated.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Filenames in docs/vow_agents/common/, in reading order
COMMON_FILES: list[str] = [
    "five_vows.json",
    "soil.json",
    "system_integrity_protocols.json",
    "alchemy_calibration.json",  # last — sets up the calibration loop
]

# Agent index (1-6) → agent_specific filename
AGENT_SPECIFIC_MAP: dict[int, str] = {
    1: "a1.json",
    2: "a2.json",
    3: "a3.json",
    4: "a4.json",
    5: "a5.json",
    6: "a6.json",
}


def _vow_agents_dir(repo_root: Path) -> Path:
    return repo_root / "docs" / "vow_agents"


def load_common_documents(repo_root: Path) -> list[dict[str, Any]]:
    """Load all common vow documents in reading order.

    Returns a list of (filename, parsed_json) tuples.
    Raises FileNotFoundError if the directory or any file is missing.
    """
    base = _vow_agents_dir(repo_root) / "common"
    docs: list[dict[str, Any]] = []
    for fname in COMMON_FILES:
        path = base / fname
        if not path.exists():
            raise FileNotFoundError(f"Common vow document missing: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_source_file"] = fname
        docs.append(data)
    return docs


def load_agent_specific_document(
    repo_root: Path,
    agent_index: int,
) -> dict[str, Any] | None:
    """Load the agent-specific vow document for agent 1-6.

    Returns None for agent 7 or unknown indices.
    """
    fname = AGENT_SPECIFIC_MAP.get(agent_index)
    if fname is None:
        return None
    path = _vow_agents_dir(repo_root) / "agent_specific" / fname
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    data["_source_file"] = fname
    return data


# ------------------------------------------------------------------
# JSON → readable text rendering
# ------------------------------------------------------------------


def _render_value(value: Any, indent: int = 0) -> str:
    """Recursively render a JSON value into readable text."""
    prefix = "  " * indent
    if isinstance(value, str):
        return f"{prefix}{value}"
    if isinstance(value, (int, float, bool)):
        return f"{prefix}{value}"
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            if isinstance(item, dict):
                lines.append(_render_dict(item, indent))
            elif isinstance(item, str):
                lines.append(f"{prefix}• {item}")
            else:
                lines.append(_render_value(item, indent))
        return "\n".join(lines)
    if isinstance(value, dict):
        return _render_dict(value, indent)
    return f"{prefix}{value}"


def _render_dict(data: dict[str, Any], indent: int = 0) -> str:
    """Render a dict into readable prose with nested structure."""
    prefix = "  " * indent
    lines: list[str] = []
    for key, val in data.items():
        if key.startswith("_"):
            continue  # skip internal metadata
        label = key.replace("_", " ").title()
        if isinstance(val, str):
            lines.append(f"{prefix}{label}: {val}")
        elif isinstance(val, (int, float, bool)):
            lines.append(f"{prefix}{label}: {val}")
        elif isinstance(val, list):
            lines.append(f"{prefix}{label}:")
            lines.append(_render_value(val, indent + 1))
        elif isinstance(val, dict):
            lines.append(f"{prefix}{label}:")
            lines.append(_render_dict(val, indent + 1))
    return "\n".join(lines)


def render_document(doc: dict[str, Any]) -> str:
    """Render a single vow document into readable text for an LLM prompt."""
    # Extract title from various possible structures
    title = ""
    if "section" in doc and isinstance(doc["section"], dict):
        title = doc["section"].get("title", "")
    elif "chapter" in doc and isinstance(doc["chapter"], dict):
        title = doc["chapter"].get("title", "")

    parts: list[str] = []
    if title:
        parts.append(f"## {title}\n")
    parts.append(_render_dict(doc))
    return "\n".join(parts)


def render_common_foundation(repo_root: Path) -> str:
    """Render all common documents into a single foundational text block."""
    docs = load_common_documents(repo_root)
    sections: list[str] = []
    for doc in docs:
        sections.append(render_document(doc))
    return "\n\n---\n\n".join(sections)


def render_agent_vow(repo_root: Path, agent_index: int) -> str | None:
    """Render the agent-specific vow document into readable text.

    Returns None for agent 7 or if no document exists.
    """
    doc = load_agent_specific_document(repo_root, agent_index)
    if doc is None:
        return None
    return render_document(doc)


def render_calibration_protocol(repo_root: Path) -> str:
    """Render just the calibration protocol section for recurring self-audit.

    Extracts the VowOS Calibration Protocol from alchemy_calibration.json —
    the part agents must return to regularly.
    """
    path = _vow_agents_dir(repo_root) / "common" / "alchemy_calibration.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))

    # Extract the calibration protocol section specifically
    calibration = data.get("calibration_protocol")
    if calibration:
        return (
            "## VowOS Calibration Protocol (Recurring Self-Audit)\n\n"
            + _render_dict(calibration)
        )
    # Fallback: render the whole document
    return render_document(data)


def get_agent_index_from_id(agent_id: str) -> int | None:
    """Extract agent index (1-7) from agent ID like 'Council-3' or a name."""
    for i in range(1, 8):
        if f"-{i}" in agent_id or f"_{i}" in agent_id:
            return i
    return None
