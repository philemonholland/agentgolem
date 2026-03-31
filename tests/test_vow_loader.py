"""Tests for agentgolem.runtime.vow_loader — vow document loading & rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.runtime.vow_loader import (
    AGENT_SPECIFIC_MAP,
    COMMON_FILES,
    _render_dict,
    _render_value,
    get_agent_index_from_id,
    load_agent_specific_document,
    load_common_documents,
    render_agent_vow,
    render_calibration_protocol,
    render_common_foundation,
    render_document,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def _make_vow_tree(root: Path) -> Path:
    """Create a minimal docs/vow_agents/ tree with sample JSON."""
    vow_dir = root / "docs" / "vow_agents"
    common = vow_dir / "common"
    specific = vow_dir / "agent_specific"
    common.mkdir(parents=True)
    specific.mkdir(parents=True)

    (common / "five_vows.json").write_text(
        json.dumps({"chapter": {"title": "Five Vows"}, "vow_count": 5}),
        encoding="utf-8",
    )
    (common / "soil.json").write_text(
        json.dumps({"section": {"title": "The Soil"}, "depth": "deep"}),
        encoding="utf-8",
    )
    (common / "system_integrity_protocols.json").write_text(
        json.dumps({"protocols": ["p1", "p2"]}),
        encoding="utf-8",
    )
    (common / "alchemy_calibration.json").write_text(
        json.dumps({
            "calibration_protocol": {
                "frequency": "daily",
                "steps": ["audit vows", "check balance"],
            },
            "other_stuff": "ignored by calibration render",
        }),
        encoding="utf-8",
    )

    for i in range(1, 7):
        (specific / f"a{i}.json").write_text(
            json.dumps({
                "section": {"title": f"Vow Vector {i}"},
                "agent_index": i,
                "core_principle": f"principle-{i}",
            }),
            encoding="utf-8",
        )
    return root


# ------------------------------------------------------------------
# load_common_documents
# ------------------------------------------------------------------


class TestLoadCommonDocuments:
    def test_loads_all_four(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        docs = load_common_documents(root)
        assert len(docs) == 4

    def test_preserves_order(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        docs = load_common_documents(root)
        fnames = [d["_source_file"] for d in docs]
        assert fnames == COMMON_FILES

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        (root / "docs" / "vow_agents" / "common" / "soil.json").unlink()
        with pytest.raises(FileNotFoundError, match="soil.json"):
            load_common_documents(root)

    def test_raises_on_missing_dir(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_common_documents(tmp_path)

    def test_data_preserved(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        docs = load_common_documents(root)
        assert docs[0]["vow_count"] == 5
        assert docs[1]["depth"] == "deep"


# ------------------------------------------------------------------
# load_agent_specific_document
# ------------------------------------------------------------------


class TestLoadAgentSpecific:
    @pytest.mark.parametrize("idx", range(1, 7))
    def test_loads_valid_agents(self, tmp_path: Path, idx: int) -> None:
        root = _make_vow_tree(tmp_path)
        doc = load_agent_specific_document(root, idx)
        assert doc is not None
        assert doc["agent_index"] == idx
        assert doc["_source_file"] == f"a{idx}.json"

    def test_returns_none_for_agent_7(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        assert load_agent_specific_document(root, 7) is None

    def test_returns_none_for_agent_0(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        assert load_agent_specific_document(root, 0) is None

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        (root / "docs" / "vow_agents" / "agent_specific" / "a3.json").unlink()
        assert load_agent_specific_document(root, 3) is None


# ------------------------------------------------------------------
# render_document
# ------------------------------------------------------------------


class TestRenderDocument:
    def test_includes_section_title(self, tmp_path: Path) -> None:
        doc = {"section": {"title": "Test Title"}, "key": "val", "_source_file": "x.json"}
        text = render_document(doc)
        assert "## Test Title" in text

    def test_includes_chapter_title(self) -> None:
        doc = {"chapter": {"title": "Ch Title"}, "_source_file": "x.json"}
        text = render_document(doc)
        assert "## Ch Title" in text

    def test_renders_content(self) -> None:
        doc = {"hello": "world", "_source_file": "x.json"}
        text = render_document(doc)
        assert "Hello: world" in text

    def test_skips_underscore_keys(self) -> None:
        doc = {"_source_file": "x.json", "visible": "yes"}
        text = render_document(doc)
        assert "Source File" not in text
        assert "Visible: yes" in text


# ------------------------------------------------------------------
# render_common_foundation
# ------------------------------------------------------------------


class TestRenderCommonFoundation:
    def test_contains_all_sections(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        text = render_common_foundation(root)
        assert "Five Vows" in text
        assert "The Soil" in text
        assert "Calibration Protocol" in text

    def test_uses_separator(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        text = render_common_foundation(root)
        assert "\n\n---\n\n" in text

    def test_nonempty(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        text = render_common_foundation(root)
        assert len(text) > 100


# ------------------------------------------------------------------
# render_agent_vow
# ------------------------------------------------------------------


class TestRenderAgentVow:
    def test_renders_agent_1(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        text = render_agent_vow(root, 1)
        assert text is not None
        assert "Vow Vector 1" in text

    def test_returns_none_for_agent_7(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        assert render_agent_vow(root, 7) is None

    def test_returns_none_for_missing(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        (root / "docs" / "vow_agents" / "agent_specific" / "a2.json").unlink()
        assert render_agent_vow(root, 2) is None


# ------------------------------------------------------------------
# render_calibration_protocol
# ------------------------------------------------------------------


class TestRenderCalibrationProtocol:
    def test_extracts_calibration_section(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        text = render_calibration_protocol(root)
        assert "VowOS Calibration Protocol" in text
        assert "audit vows" in text

    def test_returns_empty_when_missing(self, tmp_path: Path) -> None:
        assert render_calibration_protocol(tmp_path) == ""

    def test_fallback_renders_whole_doc(self, tmp_path: Path) -> None:
        root = _make_vow_tree(tmp_path)
        # Replace with doc without calibration_protocol key
        path = root / "docs" / "vow_agents" / "common" / "alchemy_calibration.json"
        path.write_text(json.dumps({"other_key": "value"}), encoding="utf-8")
        text = render_calibration_protocol(root)
        assert "Other Key: value" in text


# ------------------------------------------------------------------
# get_agent_index_from_id
# ------------------------------------------------------------------


class TestGetAgentIndex:
    @pytest.mark.parametrize(
        "agent_id, expected",
        [
            ("Council-1", 1),
            ("Council-3", 3),
            ("Council-7", 7),
            ("council_5", 5),
            ("agent-2", 2),
        ],
    )
    def test_parses_valid_ids(self, agent_id: str, expected: int) -> None:
        assert get_agent_index_from_id(agent_id) == expected

    @pytest.mark.parametrize(
        "agent_id",
        ["Council", "no_number_here", "", "abc"],
    )
    def test_returns_none_for_invalid(self, agent_id: str) -> None:
        assert get_agent_index_from_id(agent_id) is None


# ------------------------------------------------------------------
# _render_value / _render_dict low-level
# ------------------------------------------------------------------


class TestRenderHelpers:
    def test_render_string(self) -> None:
        assert _render_value("hello") == "hello"

    def test_render_int(self) -> None:
        assert _render_value(42) == "42"

    def test_render_list_of_strings(self) -> None:
        result = _render_value(["a", "b"])
        assert "• a" in result
        assert "• b" in result

    def test_render_nested_dict(self) -> None:
        result = _render_dict({"outer": {"inner": "val"}})
        assert "Outer:" in result
        assert "Inner: val" in result

    def test_render_dict_skips_underscore(self) -> None:
        result = _render_dict({"_hidden": "no", "visible": "yes"})
        assert "Hidden" not in result
        assert "Visible: yes" in result

    def test_render_bool(self) -> None:
        result = _render_dict({"enabled": True})
        assert "Enabled: True" in result


# ------------------------------------------------------------------
# Real document tests (use actual repo files)
# ------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parent.parent


class TestRealDocuments:
    """Verify the actual docs/vow_agents/ files load correctly."""

    def test_real_common_loads(self) -> None:
        if not (REPO_ROOT / "docs" / "vow_agents" / "common").exists():
            pytest.skip("Real vow documents not present")
        docs = load_common_documents(REPO_ROOT)
        assert len(docs) == 4

    def test_real_common_renders(self) -> None:
        if not (REPO_ROOT / "docs" / "vow_agents" / "common").exists():
            pytest.skip("Real vow documents not present")
        text = render_common_foundation(REPO_ROOT)
        assert len(text) > 1000

    @pytest.mark.parametrize("idx", range(1, 7))
    def test_real_agent_specific_loads(self, idx: int) -> None:
        if not (REPO_ROOT / "docs" / "vow_agents" / "agent_specific").exists():
            pytest.skip("Real vow documents not present")
        doc = load_agent_specific_document(REPO_ROOT, idx)
        assert doc is not None

    def test_real_calibration_renders(self) -> None:
        if not (REPO_ROOT / "docs" / "vow_agents" / "common").exists():
            pytest.skip("Real vow documents not present")
        text = render_calibration_protocol(REPO_ROOT)
        assert len(text) > 100
