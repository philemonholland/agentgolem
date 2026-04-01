"""Prompt template registry — versioned, named prompt templates.

Enables Meta-Harness-style experimentation: agents can propose template
modifications during calibration, and outcomes are tracked per-version.

Templates are stored as plain format strings with named placeholders.
Each agent maintains its own template history so personalization doesn't
interfere across the council.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class TemplateVersion:
    """A single versioned prompt template."""

    name: str
    version: int
    content: str
    created_at: str = ""
    proposed_by: str = ""
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemplateVersion:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class TemplateOutcome:
    """Outcome measurement for a template version over a trace window."""

    name: str
    version: int
    trace_count: int = 0
    avg_response_length: float = 0.0
    retrieval_hit_rate: float = 0.0
    peer_engagement_rate: float = 0.0
    measured_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TemplateRegistry:
    """Per-agent registry of versioned prompt templates.

    Templates are stored in ``data_dir/templates/``.  Each template name
    maps to a stack of versions, only one of which is active.
    """

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "templates"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._templates: dict[str, list[TemplateVersion]] = {}
        self._outcomes: list[TemplateOutcome] = []
        self._load()

    def _state_path(self) -> Path:
        return self._dir / "registry.json"

    def _outcomes_path(self) -> Path:
        return self._dir / "outcomes.json"

    def _load(self) -> None:
        path = self._state_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for name, versions in data.items():
                    self._templates[name] = [
                        TemplateVersion.from_dict(v) for v in versions
                    ]
            except (json.JSONDecodeError, TypeError):
                pass

        opath = self._outcomes_path()
        if opath.exists():
            try:
                raw = json.loads(opath.read_text(encoding="utf-8"))
                self._outcomes = [TemplateOutcome(**o) for o in raw]
            except (json.JSONDecodeError, TypeError):
                pass

    def _save(self) -> None:
        data = {
            name: [v.to_dict() for v in versions]
            for name, versions in self._templates.items()
        }
        self._state_path().write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )

    def _save_outcomes(self) -> None:
        self._outcomes_path().write_text(
            json.dumps([o.to_dict() for o in self._outcomes], indent=2),
            encoding="utf-8",
        )

    # ── Public API ────────────────────────────────────────────────────

    def register_default(self, name: str, content: str) -> None:
        """Register a default template (version 0) if not already present."""
        if name not in self._templates:
            tv = TemplateVersion(
                name=name,
                version=0,
                content=content,
                created_at=datetime.now(UTC).isoformat(),
                proposed_by="system",
                active=True,
            )
            self._templates[name] = [tv]
            self._save()

    def get_active(self, name: str) -> TemplateVersion | None:
        """Return the active version of a named template."""
        versions = self._templates.get(name, [])
        for v in reversed(versions):
            if v.active:
                return v
        return None

    def get_active_content(self, name: str) -> str | None:
        """Return the active template content string, or None."""
        v = self.get_active(name)
        return v.content if v else None

    def propose_edit(
        self,
        name: str,
        new_content: str,
        proposed_by: str = "agent",
    ) -> TemplateVersion:
        """Propose a new version of a template.

        Deactivates the current active version and creates a new one.
        """
        versions = self._templates.get(name, [])
        next_version = max((v.version for v in versions), default=-1) + 1

        # Deactivate current
        for v in versions:
            v.active = False

        tv = TemplateVersion(
            name=name,
            version=next_version,
            content=new_content,
            created_at=datetime.now(UTC).isoformat(),
            proposed_by=proposed_by,
            active=True,
        )
        if name not in self._templates:
            self._templates[name] = []
        self._templates[name].append(tv)
        self._save()
        return tv

    def revert_to(self, name: str, version: int) -> bool:
        """Revert a template to a specific prior version."""
        versions = self._templates.get(name, [])
        target = None
        for v in versions:
            if v.version == version:
                target = v
            v.active = False

        if target is None:
            return False
        target.active = True
        self._save()
        return True

    def record_outcome(self, outcome: TemplateOutcome) -> None:
        """Record a measured outcome for a template version."""
        self._outcomes.append(outcome)
        # Keep only last 100 outcomes
        if len(self._outcomes) > 100:
            self._outcomes = self._outcomes[-100:]
        self._save_outcomes()

    def outcomes_for(self, name: str) -> list[TemplateOutcome]:
        """Return recorded outcomes for a template name."""
        return [o for o in self._outcomes if o.name == name]

    def list_templates(self) -> dict[str, TemplateVersion | None]:
        """Return all template names with their active version."""
        return {name: self.get_active(name) for name in self._templates}

    def version_count(self, name: str) -> int:
        """Return the number of versions for a template."""
        return len(self._templates.get(name, []))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full registry state."""
        return {
            name: [v.to_dict() for v in versions]
            for name, versions in self._templates.items()
        }
