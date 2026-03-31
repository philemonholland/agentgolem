"""Tests for the skill-pack framework."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from agentgolem.tools.skill_pack import (
    SkillAction,
    SkillExample,
    SkillManifest,
    SkillPackRegistry,
    SkillPackTool,
    _safe_tool_name,
    load_skill_manifests,
    parse_skill_manifest,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_manifest_data() -> dict:
    return {
        "name": "test_skill",
        "description": "A test skill for unit tests",
        "version": "1.0.0",
        "author": "test",
        "domains": ["testing", "research"],
        "triggers": ["agent wants to test", "user asks for test data"],
        "prerequisites": [
            {"name": "internet", "check": "Network must be available", "required": True},
        ],
        "actions": [
            {
                "name": "fetch",
                "description": "Fetch test data from a URL",
                "safety_class": "external_read",
                "side_effect_class": "network_read",
                "requires_approval": False,
                "supports_dry_run": True,
                "arguments": [
                    {"name": "url", "description": "URL to fetch", "kind": "str", "required": True},
                ],
                "examples": [
                    {
                        "description": "Fetch example",
                        "invocation": "fetch(url='https://example.com')",
                        "expected_output": "Page text",
                    }
                ],
                "usage_hint": "Use when testing network access",
            },
            {
                "name": "post",
                "description": "Post test data",
                "safety_class": "external_communication",
                "side_effect_class": "external_write",
                "requires_approval": True,
                "supports_dry_run": True,
                "arguments": [
                    {"name": "url", "description": "URL to post to", "required": True},
                    {"name": "body", "description": "Body content", "required": True},
                ],
                "examples": [],
            },
        ],
        "enabled": True,
    }


@pytest.fixture()
def sample_manifest(sample_manifest_data: dict) -> SkillManifest:
    return parse_skill_manifest(sample_manifest_data)


@pytest.fixture()
def skills_dir(tmp_path: Path, sample_manifest_data: dict) -> Path:
    d = tmp_path / "skills"
    d.mkdir()
    with open(d / "test_skill.yaml", "w") as f:
        yaml.dump(sample_manifest_data, f)
    # Also write a disabled skill
    disabled = {**sample_manifest_data, "name": "disabled_skill", "enabled": False}
    with open(d / "disabled_skill.yaml", "w") as f:
        yaml.dump(disabled, f)
    return d


# ---------------------------------------------------------------------------
# parse_skill_manifest
# ---------------------------------------------------------------------------


class TestParseSkillManifest:
    def test_basic_fields(self, sample_manifest: SkillManifest) -> None:
        assert sample_manifest.name == "test_skill"
        assert sample_manifest.version == "1.0.0"
        assert sample_manifest.author == "test"
        assert sample_manifest.enabled is True

    def test_domains(self, sample_manifest: SkillManifest) -> None:
        assert sample_manifest.domains == ("testing", "research")

    def test_triggers(self, sample_manifest: SkillManifest) -> None:
        assert len(sample_manifest.triggers) == 2
        assert "agent wants to test" in sample_manifest.triggers

    def test_trigger_pattern(self, sample_manifest: SkillManifest) -> None:
        pat = sample_manifest.trigger_pattern
        assert pat.startswith("Triggers:")
        assert "agent wants to test" in pat

    def test_prerequisites(self, sample_manifest: SkillManifest) -> None:
        assert len(sample_manifest.prerequisites) == 1
        assert sample_manifest.prerequisites[0].name == "internet"
        assert sample_manifest.prerequisites[0].required is True

    def test_actions(self, sample_manifest: SkillManifest) -> None:
        assert len(sample_manifest.actions) == 2
        fetch, post = sample_manifest.actions
        assert fetch.name == "fetch"
        assert fetch.safety_class == "external_read"
        assert fetch.requires_approval is False
        assert post.name == "post"
        assert post.requires_approval is True

    def test_action_arguments(self, sample_manifest: SkillManifest) -> None:
        fetch = sample_manifest.actions[0]
        assert len(fetch.arguments) == 1
        assert fetch.arguments[0].name == "url"
        assert fetch.arguments[0].kind == "str"

    def test_action_examples(self, sample_manifest: SkillManifest) -> None:
        fetch = sample_manifest.actions[0]
        assert len(fetch.examples) == 1
        assert "example.com" in fetch.examples[0].invocation

    def test_minimal_manifest(self) -> None:
        minimal = parse_skill_manifest({"name": "bare", "actions": []})
        assert minimal.name == "bare"
        assert minimal.description == ""
        assert minimal.actions == ()
        assert minimal.enabled is True


# ---------------------------------------------------------------------------
# load_skill_manifests
# ---------------------------------------------------------------------------


class TestLoadSkillManifests:
    def test_loads_from_dir(self, skills_dir: Path) -> None:
        manifests = load_skill_manifests(skills_dir)
        assert len(manifests) == 2
        names = {m.name for m in manifests}
        assert "test_skill" in names
        assert "disabled_skill" in names

    def test_empty_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty_skills"
        empty.mkdir()
        assert load_skill_manifests(empty) == []

    def test_missing_dir(self, tmp_path: Path) -> None:
        assert load_skill_manifests(tmp_path / "nonexistent") == []


# ---------------------------------------------------------------------------
# SkillPackTool
# ---------------------------------------------------------------------------


class TestSkillPackTool:
    def test_tool_name(self, sample_manifest: SkillManifest) -> None:
        tool = SkillPackTool(sample_manifest)
        assert tool.name == "test_skill"

    def test_supported_actions(self, sample_manifest: SkillManifest) -> None:
        tool = SkillPackTool(sample_manifest)
        assert tool.supported_actions == ("fetch", "post")

    def test_is_available(self, sample_manifest: SkillManifest) -> None:
        tool = SkillPackTool(sample_manifest)
        assert tool.is_available() is True

    def test_disabled_manifest(self, sample_manifest_data: dict) -> None:
        data = {**sample_manifest_data, "enabled": False}
        manifest = parse_skill_manifest(data)
        tool = SkillPackTool(manifest)
        assert tool.is_available() is False

    def test_requires_approval_per_action(self, sample_manifest: SkillManifest) -> None:
        tool = SkillPackTool(sample_manifest)
        assert tool.requires_approval_for("fetch") is False
        assert tool.requires_approval_for("post") is True
        assert tool.requires_approval_for("nonexistent") is False

    def test_action_specs(self, sample_manifest: SkillManifest) -> None:
        tool = SkillPackTool(sample_manifest)
        specs = tool.action_specs()
        assert len(specs) == 2

        fetch_spec = next(s for s in specs if s.action_name == "fetch")
        assert fetch_spec.capability_name == "test_skill.fetch"
        assert fetch_spec.safety_class == "external_read"
        assert fetch_spec.requires_approval is False
        assert fetch_spec.domains == ("testing", "research")

        post_spec = next(s for s in specs if s.action_name == "post")
        assert post_spec.capability_name == "test_skill.post"
        assert post_spec.requires_approval is True
        assert post_spec.approval_action_name == "test_skill_post"

    def test_action_specs_include_examples(self, sample_manifest: SkillManifest) -> None:
        tool = SkillPackTool(sample_manifest)
        fetch_spec = next(s for s in tool.action_specs() if s.action_name == "fetch")
        assert "example.com" in fetch_spec.description

    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, sample_manifest: SkillManifest) -> None:
        tool = SkillPackTool(sample_manifest)
        result = await tool.execute(action="nonexistent")
        assert result.success is False
        assert "Unknown action" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_structured_invocation(self, sample_manifest: SkillManifest) -> None:
        tool = SkillPackTool(sample_manifest)
        result = await tool.execute(action="post", url="https://x.com", body="hello")
        assert result.success is True
        assert result.data["skill"] == "test_skill"
        assert result.data["action"] == "post"
        assert result.data["arguments"]["url"] == "https://x.com"

    @pytest.mark.asyncio
    async def test_execute_with_browser(self, sample_manifest: SkillManifest) -> None:
        fetched_urls: list[str] = []

        async def mock_browser(url: str) -> str:
            fetched_urls.append(url)
            return f"Content from {url}"

        tool = SkillPackTool(sample_manifest, browser_execute=mock_browser)
        result = await tool.execute(action="fetch", url="https://example.com")
        assert result.success is True
        assert result.data == "Content from https://example.com"
        assert fetched_urls == ["https://example.com"]

    @pytest.mark.asyncio
    async def test_dry_run(self, sample_manifest: SkillManifest) -> None:
        tool = SkillPackTool(sample_manifest)
        result = await tool.dry_run(action="fetch")
        assert result.success is True
        assert result.data["dry_run"] is True
        assert result.data["skill"] == "test_skill"

    @pytest.mark.asyncio
    async def test_dry_run_unknown_action(self, sample_manifest: SkillManifest) -> None:
        tool = SkillPackTool(sample_manifest)
        result = await tool.dry_run(action="bogus")
        assert result.success is False


# ---------------------------------------------------------------------------
# SkillPackRegistry
# ---------------------------------------------------------------------------


class TestSkillPackRegistry:
    def test_load_manifests(self, skills_dir: Path) -> None:
        reg = SkillPackRegistry(skills_dir)
        manifests = reg.load()
        assert len(manifests) == 2

    def test_register_all_skips_disabled(self, skills_dir: Path) -> None:
        from unittest.mock import MagicMock

        reg = SkillPackRegistry(skills_dir)
        reg.load()

        mock_registry = MagicMock()
        registered = reg.register_all(mock_registry)

        assert "test_skill" in registered
        assert "disabled_skill" not in registered
        assert mock_registry.register.call_count == 1

    def test_prompt_trigger_index(self, skills_dir: Path) -> None:
        reg = SkillPackRegistry(skills_dir)
        reg.load()
        index = reg.prompt_trigger_index()
        assert "test_skill" in index
        assert "agent wants to test" in index
        # Disabled skill should not appear in the trigger index
        assert "disabled_skill" not in index


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestSafeToolName:
    def test_lowercase(self) -> None:
        assert _safe_tool_name("MyTool") == "mytool"

    def test_spaces_to_underscores(self) -> None:
        assert _safe_tool_name("my cool tool") == "my_cool_tool"

    def test_special_chars(self) -> None:
        assert _safe_tool_name("tool-v2.0!") == "tool_v2_0"

    def test_strip_leading_trailing(self) -> None:
        assert _safe_tool_name("  _hello_ ") == "hello"


# ---------------------------------------------------------------------------
# Integration: real YAML manifests from config/skills/
# ---------------------------------------------------------------------------


class TestRealManifests:
    """Load the actual skill manifests shipped with AgentGolem."""

    @pytest.fixture()
    def real_skills_dir(self) -> Path:
        root = Path(__file__).resolve().parent.parent / "config" / "skills"
        if not root.is_dir():
            pytest.skip("config/skills/ not found relative to tests/")
        return root

    def test_all_manifests_parse(self, real_skills_dir: Path) -> None:
        manifests = load_skill_manifests(real_skills_dir)
        assert len(manifests) >= 4
        for m in manifests:
            assert m.name, f"Manifest missing name: {m}"
            assert m.description, f"Manifest {m.name} missing description"

    def test_all_manifests_produce_action_specs(self, real_skills_dir: Path) -> None:
        manifests = load_skill_manifests(real_skills_dir)
        for m in manifests:
            tool = SkillPackTool(m)
            specs = tool.action_specs()
            assert len(specs) >= 1, f"Skill {m.name} has no action specs"
            for spec in specs:
                assert spec.capability_name
                assert spec.description

    def test_alignment_research_has_three_browse_actions(self, real_skills_dir: Path) -> None:
        manifests = load_skill_manifests(real_skills_dir)
        ar = next((m for m in manifests if m.name == "alignment_research"), None)
        assert ar is not None
        action_names = {a.name for a in ar.actions}
        assert "browse_alignment_forum" in action_names
        assert "browse_lesswrong" in action_names
        assert "browse_stanford_encyclopedia" in action_names

    def test_self_evolution_write_actions_require_approval(self, real_skills_dir: Path) -> None:
        manifests = load_skill_manifests(real_skills_dir)
        se = next((m for m in manifests if m.name == "self_evolution"), None)
        assert se is not None
        for act in se.actions:
            if act.side_effect_class in ("local_write", "external_write"):
                assert act.requires_approval, (
                    f"Action {act.name} has write effects but no approval gate"
                )
