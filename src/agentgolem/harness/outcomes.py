"""Outcome statistics aggregator.

Computes actionable metrics from execution traces to answer
"what did the agents actually accomplish?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class OutcomeStats:
    """Aggregate outcome metrics over a window of execution traces."""

    total_actions: int = 0
    actions_with_outcome: int = 0
    actions_toward_goals: int = 0
    search_count: int = 0
    browse_count: int = 0
    search_to_browse_rate: float = 0.0
    browse_to_share_rate: float = 0.0
    settings_modified: int = 0
    goals_progressed: int = 0
    goals_completed: int = 0
    goals_set: int = 0
    tool_failures: int = 0
    tool_failure_rate: float = 0.0
    idle_count: int = 0
    idle_rate: float = 0.0
    productive_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_actions": self.total_actions,
            "actions_with_outcome": self.actions_with_outcome,
            "actions_toward_goals": self.actions_toward_goals,
            "search_count": self.search_count,
            "browse_count": self.browse_count,
            "search_to_browse_rate": round(self.search_to_browse_rate, 3),
            "browse_to_share_rate": round(self.browse_to_share_rate, 3),
            "settings_modified": self.settings_modified,
            "goals_progressed": self.goals_progressed,
            "goals_completed": self.goals_completed,
            "goals_set": self.goals_set,
            "tool_failures": self.tool_failures,
            "tool_failure_rate": round(self.tool_failure_rate, 3),
            "idle_count": self.idle_count,
            "idle_rate": round(self.idle_rate, 3),
            "productive_rate": round(self.productive_rate, 3),
        }

    def format_diagnostic(self) -> str:
        """Format a concise diagnostic block for calibration prompts."""
        lines = [
            f"--- OUTCOME DIAGNOSTICS (last {self.total_actions} actions) ---",
            f"Actions with meaningful outcome: "
            f"{self.actions_with_outcome}/{self.total_actions} "
            f"({self.productive_rate:.0%})",
        ]
        if self.actions_toward_goals:
            lines.append(
                f"Actions toward active goals: "
                f"{self.actions_toward_goals}/{self.total_actions} "
                f"({self.actions_toward_goals / max(self.total_actions, 1):.0%})"
            )
        if self.search_count:
            lines.append(
                f"Search → browse conversion: "
                f"{self.search_to_browse_rate:.0%} "
                f"({self.browse_count}/{self.search_count} searches led to browsing)"
            )
        if self.settings_modified:
            lines.append(f"Settings modified: {self.settings_modified}")
        if self.goals_set or self.goals_progressed or self.goals_completed:
            lines.append(
                f"Goals: {self.goals_set} set, "
                f"{self.goals_progressed} progressed, "
                f"{self.goals_completed} completed"
            )
        if self.tool_failures:
            lines.append(
                f"Tool failures: {self.tool_failures} "
                f"({self.tool_failure_rate:.0%})"
            )
        if self.idle_count:
            lines.append(f"Idle: {self.idle_count} ({self.idle_rate:.0%})")
        lines.append("---")
        return "\n".join(lines)


def compute_outcome_stats(traces: list) -> OutcomeStats:
    """Compute outcome stats from a list of ExecutionTrace objects.

    Accepts any object with the trace fields (dataclass or dict).
    """
    stats = OutcomeStats()
    if not traces:
        return stats

    browse_after_search = 0
    share_after_browse = 0
    saw_search = False
    saw_browse = False

    for t in traces:
        action = _get(t, "action_taken", "").lower()
        outcome_type = _get(t, "outcome_type", "")
        goal_id = _get(t, "goal_id", "")

        stats.total_actions += 1

        if outcome_type:
            stats.actions_with_outcome += 1

        if goal_id:
            stats.actions_toward_goals += 1

        if "search" in action:
            stats.search_count += 1
            saw_search = True
        elif "browse" in action and saw_search:
            stats.browse_count += 1
            browse_after_search += 1
            saw_search = False
            saw_browse = True
        elif "browse" in action:
            stats.browse_count += 1
            saw_browse = True

        if "share" in action and saw_browse:
            share_after_browse += 1
            saw_browse = False

        if outcome_type == "setting_changed":
            stats.settings_modified += 1
        elif outcome_type == "goal_progress":
            stats.goals_progressed += 1
        elif outcome_type == "goal_completed":
            stats.goals_completed += 1
        elif outcome_type == "goal_set":
            stats.goals_set += 1
        elif outcome_type == "tool_failure":
            stats.tool_failures += 1

        if action == "idle":
            stats.idle_count += 1

    total = max(stats.total_actions, 1)
    stats.search_to_browse_rate = (
        browse_after_search / max(stats.search_count, 1)
    )
    stats.browse_to_share_rate = (
        share_after_browse / max(stats.browse_count, 1)
    )
    stats.tool_failure_rate = stats.tool_failures / total
    stats.idle_rate = stats.idle_count / total
    stats.productive_rate = stats.actions_with_outcome / total

    return stats


def _get(obj: Any, attr: str, default: str = "") -> str:
    """Get attribute from dataclass or dict."""
    if isinstance(obj, dict):
        return str(obj.get(attr, default))
    return str(getattr(obj, attr, default))
