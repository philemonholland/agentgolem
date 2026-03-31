"""Declarative skill-pack framework — YAML-defined capabilities with trigger
metadata, prerequisites, examples, safety classes, and approval-gated actions.

Skills are loaded from ``config/skills/*.yaml`` at startup and mapped into the
audited :class:`ToolRegistry` without runtime plugin loading.  Each skill pack
describes *what* the agent can do, *when* to use it, *how* to invoke it, and
*what approvals* are needed — all in a single YAML manifest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from agentgolem.tools.base import Tool, ToolActionSpec, ToolArgument, ToolResult

if TYPE_CHECKING:
    from agentgolem.logging.audit import AuditLogger


# ---------------------------------------------------------------------------
# Dataclasses for the skill manifest
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SkillExample:
    """One example of invoking a skill action."""

    description: str
    invocation: str
    expected_output: str = ""


@dataclass(frozen=True, slots=True)
class SkillPrerequisite:
    """A prerequisite that must be satisfied before the skill is usable."""

    name: str
    check: str  # human-readable description of how to verify
    required: bool = True


@dataclass(frozen=True, slots=True)
class SkillAction:
    """One action within a skill pack (read, write, search, …)."""

    name: str
    description: str
    safety_class: str = "trusted_internal"
    side_effect_class: str = "none"
    requires_approval: bool = False
    supports_dry_run: bool = False
    arguments: tuple[ToolArgument, ...] = ()
    examples: tuple[SkillExample, ...] = ()
    usage_hint: str = ""


@dataclass(slots=True)
class SkillManifest:
    """Full metadata for one skill pack, loaded from YAML."""

    name: str
    description: str
    version: str = "0.1.0"
    author: str = ""
    domains: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()  # natural-language trigger descriptions
    prerequisites: tuple[SkillPrerequisite, ...] = ()
    actions: tuple[SkillAction, ...] = ()
    enabled: bool = True

    @property
    def trigger_pattern(self) -> str:
        """Combine triggers into a single prompt-facing block."""
        if not self.triggers:
            return ""
        return "Triggers: " + "; ".join(self.triggers)


# ---------------------------------------------------------------------------
# YAML → SkillManifest parsing
# ---------------------------------------------------------------------------

def _parse_arguments(raw: list[dict[str, Any]]) -> tuple[ToolArgument, ...]:
    return tuple(
        ToolArgument(
            name=a["name"],
            description=a.get("description", ""),
            kind=a.get("kind", "str"),
            required=a.get("required", True),
        )
        for a in raw
    )


def _parse_examples(raw: list[dict[str, Any]]) -> tuple[SkillExample, ...]:
    return tuple(
        SkillExample(
            description=e.get("description", ""),
            invocation=e.get("invocation", ""),
            expected_output=e.get("expected_output", ""),
        )
        for e in raw
    )


def _parse_prerequisites(raw: list[dict[str, Any]]) -> tuple[SkillPrerequisite, ...]:
    return tuple(
        SkillPrerequisite(
            name=p["name"],
            check=p.get("check", ""),
            required=p.get("required", True),
        )
        for p in raw
    )


def _parse_action(raw: dict[str, Any]) -> SkillAction:
    return SkillAction(
        name=raw["name"],
        description=raw.get("description", ""),
        safety_class=raw.get("safety_class", "trusted_internal"),
        side_effect_class=raw.get("side_effect_class", "none"),
        requires_approval=raw.get("requires_approval", False),
        supports_dry_run=raw.get("supports_dry_run", False),
        arguments=_parse_arguments(raw.get("arguments", [])),
        examples=_parse_examples(raw.get("examples", [])),
        usage_hint=raw.get("usage_hint", ""),
    )


def parse_skill_manifest(data: dict[str, Any]) -> SkillManifest:
    """Parse a raw YAML dict into a :class:`SkillManifest`."""
    return SkillManifest(
        name=data["name"],
        description=data.get("description", ""),
        version=data.get("version", "0.1.0"),
        author=data.get("author", ""),
        domains=tuple(data.get("domains", [])),
        triggers=tuple(data.get("triggers", [])),
        prerequisites=_parse_prerequisites(data.get("prerequisites", [])),
        actions=tuple(_parse_action(a) for a in data.get("actions", [])),
        enabled=data.get("enabled", True),
    )


def load_skill_manifests(skills_dir: Path) -> list[SkillManifest]:
    """Load all ``*.yaml`` skill manifests from *skills_dir*."""
    manifests: list[SkillManifest] = []
    if not skills_dir.is_dir():
        return manifests
    for path in sorted(skills_dir.glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict):
            manifests.append(parse_skill_manifest(data))
    return manifests


# ---------------------------------------------------------------------------
# SkillPackTool — wraps a manifest as a Tool for the ToolRegistry
# ---------------------------------------------------------------------------

class SkillPackTool(Tool):
    """Wraps a :class:`SkillManifest` into the :class:`Tool` interface.

    Execution routes through the browser (for web-based skills) or returns
    structured invocation instructions that the LLM can act on.  Write
    actions go through the approval gate automatically.
    """

    def __init__(
        self,
        manifest: SkillManifest,
        browser_execute: Any | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.manifest = manifest
        self._browser_execute = browser_execute
        self._audit_logger = audit_logger

        # Wire Tool base-class fields from the manifest
        self.name = _safe_tool_name(manifest.name)
        self.description = manifest.description
        self.domains = manifest.domains
        self.supported_actions = tuple(a.name for a in manifest.actions) or ("execute",)
        self.usage_hint = manifest.trigger_pattern

        # Per-action metadata
        self.action_descriptions = {a.name: a.description for a in manifest.actions}
        self.action_arguments = {a.name: a.arguments for a in manifest.actions}
        self._action_map = {a.name: a for a in manifest.actions}

        # Highest safety / side-effect class wins for the tool-level defaults
        self.safety_class = _max_safety_class(manifest.actions)
        self.side_effect_class = _max_side_effect_class(manifest.actions)
        self.requires_approval = any(a.requires_approval for a in manifest.actions)
        self.supports_dry_run = any(a.supports_dry_run for a in manifest.actions)

    def is_available(self) -> bool:
        return self.manifest.enabled

    def requires_approval_for(self, action: str) -> bool:
        act = self._action_map.get(action)
        return act.requires_approval if act else False

    def action_specs(self) -> tuple[ToolActionSpec, ...]:
        """Generate one ToolActionSpec per skill action with full metadata."""
        specs: list[ToolActionSpec] = []
        for act in self.manifest.actions:
            cap_name = (
                self.name if act.name in ("", "execute") else f"{self.name}.{act.name}"
            )
            # Build description with examples inline
            desc = act.description
            if act.examples:
                ex = act.examples[0]
                desc += f" Example: {ex.invocation}" if ex.invocation else ""

            specs.append(
                ToolActionSpec(
                    tool_name=self.name,
                    action_name=act.name,
                    capability_name=cap_name,
                    description=desc,
                    domains=self.manifest.domains,
                    argument_spec=act.arguments,
                    safety_class=act.safety_class,
                    side_effect_class=act.side_effect_class,
                    requires_approval=act.requires_approval,
                    supports_dry_run=act.supports_dry_run,
                    approval_action_name=(
                        self.approval_action_name(act.name) if act.requires_approval else None
                    ),
                    usage_hint=act.usage_hint or self.usage_hint,
                    available=self.is_available(),
                )
            )
        return tuple(specs)

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute a skill action.

        For web-based skills the browser is used.  Otherwise, structured
        invocation data is returned so the orchestrator can act on it.
        """
        action = kwargs.pop("action", "execute")
        act = self._action_map.get(action)
        if act is None:
            return ToolResult(success=False, error=f"Unknown action '{action}' for skill {self.name}")

        # Web-fetch actions: delegate to browser if wired
        if (
            act.side_effect_class in ("network_read", "none")
            and self._browser_execute is not None
            and "url" in kwargs
        ):
            try:
                result = await self._browser_execute(kwargs["url"])
                return ToolResult(success=True, data=result)
            except Exception as exc:
                return ToolResult(success=False, error=str(exc))

        # Structured invocation — return the instruction for the orchestrator
        return ToolResult(
            success=True,
            data={
                "skill": self.name,
                "action": action,
                "description": act.description,
                "arguments": kwargs,
                "examples": [
                    {"invocation": ex.invocation, "expected": ex.expected_output}
                    for ex in act.examples
                ],
                "prerequisites": [
                    {"name": p.name, "check": p.check}
                    for p in self.manifest.prerequisites
                ],
            },
        )

    async def dry_run(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action", "execute")
        act = self._action_map.get(action)
        if act is None:
            return ToolResult(success=False, error=f"Unknown action '{action}'")
        return ToolResult(
            success=True,
            data={
                "dry_run": True,
                "skill": self.name,
                "action": action,
                "would_do": act.description,
                "safety_class": act.safety_class,
                "requires_approval": act.requires_approval,
            },
        )


# ---------------------------------------------------------------------------
# SkillPackRegistry — loads manifests and registers them as tools
# ---------------------------------------------------------------------------

class SkillPackRegistry:
    """Load skill manifests from disk and register them with a ToolRegistry."""

    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir
        self._manifests: list[SkillManifest] = []

    def load(self) -> list[SkillManifest]:
        """Load (or reload) all YAML manifests from the skills directory."""
        self._manifests = load_skill_manifests(self._skills_dir)
        return self._manifests

    @property
    def manifests(self) -> list[SkillManifest]:
        return list(self._manifests)

    def register_all(
        self,
        registry: Any,  # ToolRegistry — avoid circular import
        *,
        browser_execute: Any | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> list[str]:
        """Create :class:`SkillPackTool` wrappers and register each one.

        Returns the list of registered skill names.
        """
        registered: list[str] = []
        for manifest in self._manifests:
            if not manifest.enabled:
                continue
            tool = SkillPackTool(
                manifest=manifest,
                browser_execute=browser_execute,
                audit_logger=audit_logger,
            )
            registry.register(tool)
            registered.append(tool.name)
        return registered

    def prompt_trigger_index(self) -> str:
        """Return a prompt-facing index of all skills and their triggers."""
        lines: list[str] = []
        for m in self._manifests:
            if not m.enabled:
                continue
            trigger_text = "; ".join(m.triggers) if m.triggers else "general"
            lines.append(f"- {m.name}: {m.description} [triggers: {trigger_text}]")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFETY_RANKING = {
    "trusted_internal": 0,
    "external_read": 1,
    "external_communication": 2,
    "hostile_external_surface": 3,
}

_SIDE_EFFECT_RANKING = {
    "none": 0,
    "network_read": 1,
    "local_write": 2,
    "external_write": 3,
}


def _max_safety_class(actions: tuple[SkillAction, ...] | tuple[()]) -> str:
    if not actions:
        return "trusted_internal"
    return max(actions, key=lambda a: _SAFETY_RANKING.get(a.safety_class, 0)).safety_class


def _max_side_effect_class(actions: tuple[SkillAction, ...] | tuple[()]) -> str:
    if not actions:
        return "none"
    return max(
        actions, key=lambda a: _SIDE_EFFECT_RANKING.get(a.side_effect_class, 0)
    ).side_effect_class


def _safe_tool_name(name: str) -> str:
    """Convert a human-readable skill name to a valid tool identifier."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower().strip()).strip("_")
