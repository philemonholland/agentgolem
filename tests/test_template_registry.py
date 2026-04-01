"""Tests for the prompt template registry (Meta-Harness Phase 2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentgolem.harness.template_registry import (
    TemplateOutcome,
    TemplateRegistry,
    TemplateVersion,
)


# ── 1. TemplateVersion round-trip ─────────────────────────────────────

def test_template_version_round_trip():
    v = TemplateVersion(
        name="identity_preamble",
        version=0,
        content="Hello {agent_name}",
        proposed_by="system",
        active=True,
    )
    d = v.to_dict()
    v2 = TemplateVersion.from_dict(d)
    assert v2.name == "identity_preamble"
    assert v2.version == 0
    assert v2.content == "Hello {agent_name}"
    assert v2.active is True


# ── 2. Registry: register default ────────────────────────────────────

def test_register_default(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    reg.register_default("test_template", "Hello {name}")
    active = reg.get_active("test_template")
    assert active is not None
    assert active.version == 0
    assert active.content == "Hello {name}"


def test_register_default_idempotent(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    reg.register_default("t", "v1")
    reg.register_default("t", "v2")  # should not overwrite
    assert reg.get_active("t").content == "v1"


# ── 3. Registry: propose edit ────────────────────────────────────────

def test_propose_edit(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    reg.register_default("t", "original")
    v1 = reg.propose_edit("t", "modified", proposed_by="agent")
    assert v1.version == 1
    assert v1.content == "modified"
    assert v1.active is True
    # Old version should be inactive
    assert reg.version_count("t") == 2
    active = reg.get_active("t")
    assert active.version == 1


def test_propose_edit_new_template(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    v0 = reg.propose_edit("new", "brand new")
    assert v0.version == 0
    assert v0.active is True


# ── 4. Registry: revert ──────────────────────────────────────────────

def test_revert_to(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    reg.register_default("t", "v0")
    reg.propose_edit("t", "v1")
    reg.propose_edit("t", "v2")
    assert reg.get_active("t").version == 2
    assert reg.revert_to("t", 0)
    assert reg.get_active("t").version == 0
    assert reg.get_active("t").content == "v0"


def test_revert_nonexistent(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    assert not reg.revert_to("missing", 0)


# ── 5. Registry: persistence ─────────────────────────────────────────

def test_persistence_round_trip(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    reg.register_default("a", "content-a")
    reg.propose_edit("a", "content-a-v1")
    reg.register_default("b", "content-b")

    # Load fresh from disk
    reg2 = TemplateRegistry(tmp_path)
    assert reg2.get_active("a").version == 1
    assert reg2.get_active("b").version == 0
    assert reg2.version_count("a") == 2


# ── 6. Outcomes ───────────────────────────────────────────────────────

def test_record_outcome(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    outcome = TemplateOutcome(
        name="t",
        version=0,
        trace_count=50,
        avg_response_length=500.0,
        retrieval_hit_rate=0.45,
        peer_engagement_rate=0.6,
    )
    reg.record_outcome(outcome)
    results = reg.outcomes_for("t")
    assert len(results) == 1
    assert results[0].trace_count == 50


def test_outcome_persistence(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    reg.record_outcome(TemplateOutcome(name="t", version=0, trace_count=10))
    # Reload
    reg2 = TemplateRegistry(tmp_path)
    assert len(reg2.outcomes_for("t")) == 1


# ── 7. List templates ────────────────────────────────────────────────

def test_list_templates(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    reg.register_default("a", "1")
    reg.register_default("b", "2")
    listing = reg.list_templates()
    assert set(listing.keys()) == {"a", "b"}
    assert listing["a"].version == 0


# ── 8. get_active_content ────────────────────────────────────────────

def test_get_active_content(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    reg.register_default("t", "Hello {name}")
    assert reg.get_active_content("t") == "Hello {name}"
    assert reg.get_active_content("missing") is None


# ── 9. to_dict ────────────────────────────────────────────────────────

def test_to_dict(tmp_path: Path):
    reg = TemplateRegistry(tmp_path)
    reg.register_default("t", "x")
    d = reg.to_dict()
    assert "t" in d
    assert len(d["t"]) == 1
    assert d["t"][0]["name"] == "t"
