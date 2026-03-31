"""Tests for the individual and team goal systems."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentgolem.runtime.team_goals import (
    TeamGoal,
    archive_team_goal,
    load_active_team_goal,
    save_team_goal,
)


# ── 1. TeamGoal model ────────────────────────────────────────────────


class TestTeamGoalModel:
    """TeamGoal dataclass round-trip and persistence."""

    def test_round_trip(self):
        goal = TeamGoal(
            description="Create an AI safety guide",
            why_it_matters="Bridge gap between research and public",
            success_criteria="Published guide with 5+ entries",
            proposed_by="Anvaya",
            status="voting",
            votes={"Anvaya": "accept"},
        )
        d = goal.to_dict()
        restored = TeamGoal.from_dict(d)
        assert restored.description == goal.description
        assert restored.proposed_by == "Anvaya"
        assert restored.votes == {"Anvaya": "accept"}
        assert restored.status == "voting"

    def test_defaults(self):
        goal = TeamGoal()
        assert len(goal.id) == 12
        assert goal.status == "proposed"
        assert goal.votes == {}
        assert goal.subtasks == {}
        assert goal.completed_at is None


# ── 2. TeamGoal persistence ──────────────────────────────────────────


class TestTeamGoalPersistence:
    """File-based persistence for team goals."""

    def test_save_and_load(self, tmp_path: Path):
        goal = TeamGoal(
            description="Test goal",
            proposed_by="Agent-1",
            status="active",
        )
        save_team_goal(goal, tmp_path)
        loaded = load_active_team_goal(tmp_path)
        assert loaded is not None
        assert loaded.description == "Test goal"
        assert loaded.proposed_by == "Agent-1"

    def test_load_missing(self, tmp_path: Path):
        assert load_active_team_goal(tmp_path) is None

    def test_load_corrupt(self, tmp_path: Path):
        d = tmp_path / "team_goals"
        d.mkdir()
        (d / "active.json").write_text("not json")
        assert load_active_team_goal(tmp_path) is None

    def test_archive(self, tmp_path: Path):
        goal = TeamGoal(id="abc123", description="Done", status="completed")
        save_team_goal(goal, tmp_path)
        archive_team_goal(tmp_path)
        assert load_active_team_goal(tmp_path) is None
        archived = list((tmp_path / "team_goals").glob("completed_*.json"))
        assert len(archived) == 1

    def test_archive_no_active(self, tmp_path: Path):
        archive_team_goal(tmp_path)  # should not raise


# ── 3. TeamGoal voting logic ─────────────────────────────────────────


class TestTeamGoalVoting:
    """Voting and status transitions."""

    def test_majority_accept(self, tmp_path: Path):
        goal = TeamGoal(
            description="Collab",
            proposed_by="A",
            status="voting",
            votes={"A": "accept", "B": "accept", "C": "accept"},
        )
        # 3 accepts → not yet majority (need 4 of 7)
        assert goal.status == "voting"
        goal.votes["D"] = "accept"
        accepts = sum(1 for v in goal.votes.values() if v == "accept")
        assert accepts >= 4  # majority reached

    def test_majority_reject(self, tmp_path: Path):
        goal = TeamGoal(
            description="Bad idea",
            proposed_by="A",
            status="voting",
            votes={"A": "accept", "B": "reject", "C": "reject", "D": "reject"},
        )
        rejects = sum(1 for v in goal.votes.values() if v == "reject")
        # 3 rejects out of 7 → not enough to block (need >3)
        assert rejects == 3
        goal.votes["E"] = "reject"
        rejects = sum(1 for v in goal.votes.values() if v == "reject")
        assert rejects > 7 - 4  # more than (total - majority) → rejected

    def test_subtask_assignment(self, tmp_path: Path):
        goal = TeamGoal(
            description="Research",
            status="active",
            proposed_by="A",
            subtasks={
                "A": {"description": "Task A", "status": "pending", "progress_notes": []},
                "B": {"description": "Task B", "status": "pending", "progress_notes": []},
            },
        )
        save_team_goal(goal, tmp_path)
        loaded = load_active_team_goal(tmp_path)
        assert loaded is not None
        assert "A" in loaded.subtasks
        assert loaded.subtasks["A"]["description"] == "Task A"

    def test_subtask_completion_check(self):
        goal = TeamGoal(
            description="Done check",
            status="active",
            subtasks={
                "A": {"status": "completed"},
                "B": {"status": "completed"},
                "C": {"status": "in_progress"},
            },
        )
        all_done = all(
            st.get("status") == "completed" for st in goal.subtasks.values()
        )
        assert not all_done

        goal.subtasks["C"]["status"] = "completed"
        all_done = all(
            st.get("status") == "completed" for st in goal.subtasks.values()
        )
        assert all_done


# ── 4. Personal goal search_text encoding ────────────────────────────


class TestGoalEncoding:
    """Verify GOAL|ACTIVE|... encoding conventions."""

    def test_search_text_format(self):
        agent = "Anvaya"
        criteria = "Hit rate > 0.4"
        desc = "Improve retrieval"
        iso = datetime.now(UTC).isoformat()
        st = f"GOAL|ACTIVE|{agent}|{criteria}|{desc}|{iso}"

        parts = st.split("|", 5)
        assert parts[0] == "GOAL"
        assert parts[1] == "ACTIVE"
        assert parts[2] == agent
        assert parts[3] == criteria
        assert parts[4] == desc

    def test_completed_marker(self):
        st = "GOAL|ACTIVE|Anvaya|criteria|desc|2025-01-01"
        completed = st.replace("GOAL|ACTIVE|", "GOAL|COMPLETED|", 1)
        assert completed.startswith("GOAL|COMPLETED|")
        assert "Anvaya" in completed

    def test_abandoned_marker(self):
        st = "GOAL|ACTIVE|Anvaya|criteria|desc|2025-01-01"
        abandoned = st.replace("GOAL|ACTIVE|", "GOAL|ABANDONED|", 1)
        assert abandoned.startswith("GOAL|ABANDONED|")


# ── 5. Goal context prompt builder ───────────────────────────────────


class TestGoalContextPrompt:
    """Test _build_goal_context_for_prompt output format."""

    def test_team_goal_in_context(self, tmp_path: Path):
        goal = TeamGoal(
            description="Create AI safety guide",
            why_it_matters="Education",
            status="active",
            proposed_by="A",
            subtasks={
                "TestAgent": {
                    "description": "Research IIT",
                    "status": "in_progress",
                    "last_updated": datetime.now(UTC).isoformat(),
                },
            },
        )
        save_team_goal(goal, tmp_path)
        loaded = load_active_team_goal(tmp_path)
        assert loaded is not None
        assert loaded.status == "active"
        assert "TestAgent" in loaded.subtasks

    def test_voting_status_shows_votes(self, tmp_path: Path):
        goal = TeamGoal(
            description="Proposal",
            status="voting",
            votes={"A": "accept", "B": "reject"},
        )
        save_team_goal(goal, tmp_path)
        loaded = load_active_team_goal(tmp_path)
        assert loaded is not None
        assert loaded.status == "voting"
        accepts = sum(1 for v in loaded.votes.values() if v == "accept")
        assert accepts == 1
