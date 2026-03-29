"""Tests for codebase inspection and self-evolution system."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.config.secrets import Secrets
from agentgolem.config.settings import Settings
from agentgolem.runtime.loop import (
    EDITABLE_EXTENSIONS,
    INSPECTABLE_EXTENSIONS,
    NISCALAJYOTI_CHAPTERS,
    PROTECTED_PATHS,
    REPO_ROOT,
    MainLoop,
)


def _make_loop(tmp_path: Path, *, reading_complete: bool = False) -> MainLoop:
    """Build a minimal MainLoop for evolution tests."""
    data_dir = tmp_path / "data" / "council_1"
    data_dir.mkdir(parents=True, exist_ok=True)
    for d in (
        "soul_versions", "heartbeat_history", "logs",
        "memory", "memory/snapshots", "approvals",
        "inbox", "outbox", "state",
    ):
        (data_dir / d).mkdir(parents=True, exist_ok=True)
    (data_dir / "soul.md").write_text("# Test Soul\n", encoding="utf-8")
    (data_dir / "heartbeat.md").write_text("# Heartbeat\n", encoding="utf-8")

    settings = Settings(data_dir=data_dir)
    secrets = Secrets(openai_api_key="", openai_base_url="")
    loop = MainLoop(
        settings=settings,
        secrets=secrets,
        agent_name="TestAgent",
        ethical_vector="kindness",
    )
    if reading_complete:
        loop._niscalajyoti_reading_complete = True
    return loop


# ------------------------------------------------------------------
# REPO_ROOT constant
# ------------------------------------------------------------------

class TestRepoRoot:
    """REPO_ROOT correctly points to the repository root."""

    def test_repo_root_exists(self) -> None:
        assert REPO_ROOT.exists()

    def test_repo_root_contains_run_golem(self) -> None:
        assert (REPO_ROOT / "run_golem.py").exists()

    def test_repo_root_contains_src(self) -> None:
        assert (REPO_ROOT / "src").is_dir()


# ------------------------------------------------------------------
# Path validation
# ------------------------------------------------------------------

class TestPathValidation:
    """_validate_repo_path prevents escaping the repo and accessing secrets."""

    def test_valid_path(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        result = loop._validate_repo_path("src/agentgolem/runtime/loop.py")
        assert result is not None
        assert result.is_absolute()

    def test_blocked_env_file(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._validate_repo_path(".env") is None

    def test_blocked_git_dir(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._validate_repo_path(".git") is None
        assert loop._validate_repo_path(".git/config") is None

    def test_blocked_secrets_yaml(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._validate_repo_path("config/secrets.yaml") is None

    def test_blocked_pycache(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._validate_repo_path("__pycache__/foo.pyc") is None

    def test_path_traversal_blocked(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._validate_repo_path("../../etc/passwd") is None
        assert loop._validate_repo_path("src/../../outside") is None

    def test_root_path(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        result = loop._validate_repo_path(".")
        assert result is not None

    def test_backslash_normalized(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        result = loop._validate_repo_path("src\\agentgolem\\runtime")
        assert result is not None


# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

class TestConstants:
    """Extension and protection sets are well-configured."""

    def test_py_is_inspectable(self) -> None:
        assert ".py" in INSPECTABLE_EXTENSIONS

    def test_py_is_editable(self) -> None:
        assert ".py" in EDITABLE_EXTENSIONS

    def test_yaml_is_editable(self) -> None:
        assert ".yaml" in EDITABLE_EXTENSIONS

    def test_env_is_protected(self) -> None:
        assert ".env" in PROTECTED_PATHS

    def test_git_is_protected(self) -> None:
        assert ".git" in PROTECTED_PATHS

    def test_editable_subset_of_inspectable(self) -> None:
        assert EDITABLE_EXTENSIONS.issubset(INSPECTABLE_EXTENSIONS)


# ------------------------------------------------------------------
# Codebase inspection
# ------------------------------------------------------------------

class TestInspectCodebase:
    """INSPECT action is only available after NJ reading complete."""

    @pytest.mark.asyncio
    async def test_inspect_blocked_before_reading(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path, reading_complete=False)
        emitted: list[tuple[str, str]] = []
        loop._activity_callback = lambda icon, text: emitted.append((icon, text))
        await loop._inspect_codebase("run_golem.py")
        assert any("only available" in t for _, t in emitted)

    @pytest.mark.asyncio
    async def test_inspect_protected_path_denied(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path, reading_complete=True)
        emitted: list[tuple[str, str]] = []
        loop._activity_callback = lambda icon, text: emitted.append((icon, text))
        await loop._inspect_codebase(".env")
        assert any("Access denied" in t for _, t in emitted)

    @pytest.mark.asyncio
    async def test_inspect_nonexistent_file(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path, reading_complete=True)
        emitted: list[tuple[str, str]] = []
        loop._activity_callback = lambda icon, text: emitted.append((icon, text))
        await loop._inspect_codebase("this_file_does_not_exist_xyz.py")
        assert any("not found" in t for _, t in emitted)


# ------------------------------------------------------------------
# Evolution proposals
# ------------------------------------------------------------------

class TestEvolutionProposals:
    """Proposal creation, storage, and validation."""

    def test_proposals_dir_created(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._proposals_dir.exists()
        assert loop._proposals_dir.name == "evolution_proposals"

    @pytest.mark.asyncio
    async def test_propose_blocked_before_reading(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path, reading_complete=False)
        emitted: list[tuple[str, str]] = []
        loop._activity_callback = lambda icon, text: emitted.append((icon, text))
        await loop._propose_evolution("test.py", "test", "a", "b")
        assert any("only available" in t for _, t in emitted)

    @pytest.mark.asyncio
    async def test_propose_blocked_for_protected_path(
        self, tmp_path: Path
    ) -> None:
        loop = _make_loop(tmp_path, reading_complete=True)
        emitted: list[tuple[str, str]] = []
        loop._activity_callback = lambda icon, text: emitted.append((icon, text))
        await loop._propose_evolution(".env", "test", "a", "b")
        assert any("protected" in t.lower() for _, t in emitted)

    @pytest.mark.asyncio
    async def test_propose_blocked_git_push(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path, reading_complete=True)
        emitted: list[tuple[str, str]] = []
        loop._activity_callback = lambda icon, text: emitted.append((icon, text))
        # Create a test file the agent can edit
        test_file = REPO_ROOT / "test_evolution_temp.py"
        test_file.write_text("# temp\n", encoding="utf-8")
        try:
            await loop._propose_evolution(
                "test_evolution_temp.py",
                "add git push",
                "# temp",
                "import os; os.system('git push')",
            )
            assert any("git push" in t.lower() for _, t in emitted)
        finally:
            test_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_propose_creates_json(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path, reading_complete=True)
        emitted: list[tuple[str, str]] = []
        loop._activity_callback = lambda icon, text: emitted.append((icon, text))

        # Create a test file in the repo
        test_file = REPO_ROOT / "test_evolution_temp.py"
        test_file.write_text("# old content\npass\n", encoding="utf-8")
        try:
            await loop._propose_evolution(
                "test_evolution_temp.py",
                "Add docstring",
                "# old content",
                '"""Module docstring."""',
            )
            # Check proposal file was created
            proposals = list(loop._proposals_dir.glob("evo_*.json"))
            assert len(proposals) >= 1

            data = json.loads(proposals[-1].read_text(encoding="utf-8"))
            assert data["status"] == "pending"
            assert data["proposer"] == "TestAgent"
            assert data["file_path"] == "test_evolution_temp.py"
            assert "TestAgent" in data["votes"]  # auto-approves own
        finally:
            test_file.unlink(missing_ok=True)
            for p in loop._proposals_dir.glob("evo_*.json"):
                p.unlink(missing_ok=True)


# ------------------------------------------------------------------
# Proposal voting and consensus
# ------------------------------------------------------------------

class TestProposalVoting:
    """Voting logic and consensus checking."""

    def test_load_proposals_empty(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._load_proposals("pending") == []

    def test_load_proposals_with_data(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        proposal = {
            "id": "evo_test1",
            "status": "pending",
            "proposer": "Agent1",
            "votes": {},
        }
        (loop._proposals_dir / "evo_test1.json").write_text(
            json.dumps(proposal), encoding="utf-8"
        )
        results = loop._load_proposals("pending")
        assert len(results) == 1
        assert results[0]["id"] == "evo_test1"

    def test_load_proposals_filters_status(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        for status in ("pending", "applied", "rejected"):
            prop = {"id": f"evo_{status}", "status": status, "votes": {}}
            (loop._proposals_dir / f"evo_{status}.json").write_text(
                json.dumps(prop), encoding="utf-8"
            )
        assert len(loop._load_proposals("pending")) == 1
        assert len(loop._load_proposals("applied")) == 1
        assert len(loop._load_proposals("rejected")) == 1

    def test_get_required_voters_without_bus(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop._peer_bus = None
        voters = loop._get_required_voters()
        assert voters == ["TestAgent"]


# ------------------------------------------------------------------
# Apply approved proposals
# ------------------------------------------------------------------

class TestApplyProposals:
    """Applying unanimously approved code changes."""

    @pytest.mark.asyncio
    async def test_apply_with_unanimous_approval(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path, reading_complete=True)
        loop._peer_bus = None  # solo agent — only needs own vote
        emitted: list[tuple[str, str]] = []
        loop._activity_callback = lambda icon, text: emitted.append((icon, text))

        # Create target file
        test_file = REPO_ROOT / "test_apply_temp.py"
        test_file.write_text("# old line\npass\n", encoding="utf-8")

        proposal = {
            "id": "evo_apply1",
            "proposer": "TestAgent",
            "timestamp": "2026-01-01T00:00:00Z",
            "file_path": "test_apply_temp.py",
            "description": "Update comment",
            "old_content": "# old line",
            "new_content": "# new line",
            "votes": {
                "TestAgent": {"approve": True, "reason": "Vow-aligned"},
            },
            "status": "pending",
        }
        (loop._proposals_dir / "evo_apply1.json").write_text(
            json.dumps(proposal), encoding="utf-8"
        )

        try:
            result = await loop._apply_approved_proposals()
            assert result is True
            assert loop._evolution_restart_requested is True

            # Verify file was changed
            content = test_file.read_text(encoding="utf-8")
            assert "# new line" in content
            assert "# old line" not in content

            # Verify proposal status updated
            updated = json.loads(
                (loop._proposals_dir / "evo_apply1.json").read_text(
                    encoding="utf-8"
                )
            )
            assert updated["status"] == "applied"
        finally:
            test_file.unlink(missing_ok=True)
            for p in loop._proposals_dir.glob("evo_*.json"):
                p.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_reject_without_consensus(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path, reading_complete=True)
        loop._peer_bus = None
        emitted: list[tuple[str, str]] = []
        loop._activity_callback = lambda icon, text: emitted.append((icon, text))

        proposal = {
            "id": "evo_reject1",
            "proposer": "TestAgent",
            "timestamp": "2026-01-01T00:00:00Z",
            "file_path": "test.py",
            "description": "Bad change",
            "old_content": "old",
            "new_content": "new",
            "votes": {
                "TestAgent": {"approve": False, "reason": "Not Vow-aligned"},
            },
            "status": "pending",
        }
        (loop._proposals_dir / "evo_reject1.json").write_text(
            json.dumps(proposal), encoding="utf-8"
        )

        try:
            result = await loop._apply_approved_proposals()
            assert result is False  # rejection doesn't count as "applied"

            updated = json.loads(
                (loop._proposals_dir / "evo_reject1.json").read_text(
                    encoding="utf-8"
                )
            )
            assert updated["status"] == "rejected"
        finally:
            for p in loop._proposals_dir.glob("evo_*.json"):
                p.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_skip_incomplete_votes(self, tmp_path: Path) -> None:
        """Proposals with missing votes are not applied or rejected."""
        loop = _make_loop(tmp_path, reading_complete=True)

        from agentgolem.runtime.bus import InterAgentBus
        bus = InterAgentBus()
        bus.register("TestAgent")
        bus.register("OtherAgent")
        loop._peer_bus = bus
        loop.agent_name = "TestAgent"

        proposal = {
            "id": "evo_wait1",
            "proposer": "TestAgent",
            "timestamp": "2026-01-01T00:00:00Z",
            "file_path": "test.py",
            "description": "Good change",
            "old_content": "old",
            "new_content": "new",
            "votes": {
                "TestAgent": {"approve": True, "reason": "Good"},
                # OtherAgent hasn't voted yet
            },
            "status": "pending",
        }
        (loop._proposals_dir / "evo_wait1.json").write_text(
            json.dumps(proposal), encoding="utf-8"
        )

        try:
            result = await loop._apply_approved_proposals()
            assert result is False  # not all votes in

            updated = json.loads(
                (loop._proposals_dir / "evo_wait1.json").read_text(
                    encoding="utf-8"
                )
            )
            assert updated["status"] == "pending"  # still waiting
        finally:
            for p in loop._proposals_dir.glob("evo_*.json"):
                p.unlink(missing_ok=True)


# ------------------------------------------------------------------
# Parse helpers
# ------------------------------------------------------------------

class TestParseAndEvolve:
    """EVOLVE action parsing."""

    @pytest.mark.asyncio
    async def test_invalid_format_rejected(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path, reading_complete=True)
        emitted: list[tuple[str, str]] = []
        loop._activity_callback = lambda icon, text: emitted.append((icon, text))
        await loop._parse_and_evolve("just some text")
        assert any("Invalid EVOLVE format" in t for _, t in emitted)


# ------------------------------------------------------------------
# Evolution restart flag
# ------------------------------------------------------------------

class TestEvolutionRestart:
    """Evolution restart propagation."""

    def test_initial_flag_is_false(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._evolution_restart_requested is False

    def test_evolution_shutdown_event_wirable(self, tmp_path: Path) -> None:
        import asyncio
        loop = _make_loop(tmp_path)
        event = asyncio.Event()
        loop._evolution_shutdown_event = event
        assert loop._evolution_shutdown_event is event


# ------------------------------------------------------------------
# Synchronized wake (offset = 0)
# ------------------------------------------------------------------

class TestSynchronizedWake:
    """With offset 0, all agents start at the same time."""

    def test_zero_offset_no_delay(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop._start_delay_seconds = 0.0
        assert loop._start_delay_seconds == 0.0
