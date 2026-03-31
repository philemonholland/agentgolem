"""Tests for the AgentGolem Dashboard API."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agentgolem.dashboard.api import DashboardState, create_app
from agentgolem.identity.heartbeat import HeartbeatManager
from agentgolem.identity.soul import SoulManager
from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import ConceptualNode, MemoryCluster, NodeType
from agentgolem.memory.schema import init_db
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.runtime.interrupts import InterruptManager
from agentgolem.runtime.state import RuntimeState
from agentgolem.tools.base import ApprovalGate


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------


@dataclass
class FakeParamSpec:
    key: str
    display_name: str
    description: str
    ptype: str
    group: str
    aliases: tuple[str, ...] = ()


class FakeParamStore:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = dict(values)
        self.settings = self.values
        self.launcher: dict[str, Any] = {}
        self.env: dict[str, str] = {}
        self._runtime_overrides: dict[str, Any] = {}

    def get(self, key: str, ptype: str) -> Any:
        return self.values[key]

    def get_display(self, key: str, ptype: str) -> str:
        value = self.get(key, ptype)
        if ptype == "bool":
            return str(bool(value)).lower()
        return str(value)


class FakeBus:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.floor_holder = "Council-1"
        self._messages = [
            SimpleNamespace(
                from_agent="Council-1",
                to_agent="Council-6",
                text="I think the dialogue should stay open.",
                timestamp=now,
            )
        ]

    def floor_locked(self) -> bool:
        return True

    def get_waiting_speakers(self) -> list[str]:
        return ["Council-6"]

    def get_transcript(self, limit: int = 10) -> list[Any]:
        return self._messages[:limit]

    def pending_count(self, agent_name: str) -> int:
        return 1 if agent_name == "Council-7" else 0

    def get_priority(self, agent_name: str) -> float:
        return 0.9 if agent_name == "Council-7" else 0.4


def _make_council_agent(
    root: Path,
    name: str,
    mode: str,
    task: str,
    *,
    current_name: str | None = None,
    name_history: list[str] | None = None,
) -> Any:
    agent_dir = root / name.lower().replace("-", "_")
    (agent_dir / "logs").mkdir(parents=True, exist_ok=True)
    (agent_dir / "approvals").mkdir(parents=True, exist_ok=True)
    display_name = current_name or name
    history = name_history or [name]
    (agent_dir / "soul.md").write_text(f"# Soul\n{display_name}", encoding="utf-8")
    (agent_dir / "heartbeat.md").write_text(f"# Heartbeat\n{display_name}", encoding="utf-8")
    (agent_dir / "internal_state.json").write_text("{}", encoding="utf-8")

    runtime_state = SimpleNamespace(
        mode=SimpleNamespace(value=mode),
        current_task=task,
        pending_tasks=[],
        started_at=datetime.now(timezone.utc),
    )
    audit_logger = AuditLogger(agent_dir)
    interrupt_manager = InterruptManager()

    return SimpleNamespace(
        agent_name=display_name,
        _initial_agent_name=name,
        _name_history=history,
        ethical_vector="holistic" if name == "Council-6" else "integrity",
        runtime_state=runtime_state,
        soul_manager=None,
        heartbeat_manager=None,
        audit_logger=audit_logger,
        _approval_gate=ApprovalGate(agent_dir / "approvals", ["email_send"]),
        interrupt_manager=interrupt_manager,
        _data_dir=agent_dir,
        _recent_thoughts=[f"{name} is tracking the conversation."],
        _conversation_paused=(mode == "paused"),
        _wake_cycle_count=1,
        _name_discovered=True,
        _memory_store=None,
        _internal_state=None,
        _metacognitive_monitor=SimpleNamespace(last_observation=None),
        _attention_director=None,
        _self_model=None,
        _narrative_synthesizer=SimpleNamespace(latest_chapter=None),
        _settings=None,
        _peer_msg_limit=3000,
        _discussion_max_completion_tokens=2048,
    )


def _make_base_state(tmp_path: Path) -> DashboardState:
    """Build a DashboardState rooted in *tmp_path*."""
    runtime = RuntimeState(tmp_path)

    soul_path = tmp_path / "soul.md"
    soul_path.write_text("# AgentGolem Soul\nTest soul content", encoding="utf-8")
    soul_mgr = SoulManager(soul_path, tmp_path)

    hb_path = tmp_path / "heartbeat.md"
    hb_path.write_text("# Heartbeat\nTest heartbeat content", encoding="utf-8")
    hb_mgr = HeartbeatManager(hb_path, tmp_path)

    audit = AuditLogger(tmp_path)

    approvals_dir = tmp_path / "approvals"
    gate = ApprovalGate(approvals_dir, ["email_send"])

    interrupt_mgr = InterruptManager()

    # Seed an activity log
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    activity_log = logs_dir / "activity.jsonl"
    entries = [
        {"timestamp": "2024-01-01T00:00:00Z", "action": "test_action", "detail": "first"},
        {"timestamp": "2024-01-01T00:01:00Z", "action": "another_action", "detail": "second"},
    ]
    with open(activity_log, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")

    return DashboardState(
        runtime_state=runtime,
        soul_manager=soul_mgr,
        heartbeat_manager=hb_mgr,
        audit_logger=audit,
        approval_gate=gate,
        interrupt_manager=interrupt_mgr,
        data_dir=tmp_path,
    )


@pytest.fixture()
def dashboard_state(tmp_path: Path) -> DashboardState:
    return _make_base_state(tmp_path)


def _make_council_state(tmp_path: Path) -> DashboardState:
    store = FakeParamStore(
        {
            "discussion_max_completion_tokens": 2048,
            "dashboard_refresh_interval_seconds": 5,
        }
    )
    specs = [
        FakeParamSpec(
            key="discussion_max_completion_tokens",
            display_name="Discussion Max Tokens",
            description="Maximum completion tokens for discussion wrap-up.",
            ptype="int",
            group="Dialogue",
        ),
        FakeParamSpec(
            key="dashboard_refresh_interval_seconds",
            display_name="Dashboard Refresh",
            description="Refresh cadence for live panels.",
            ptype="int",
            group="Dashboard",
        ),
    ]

    state = DashboardState(
        agents=[
            _make_council_agent(tmp_path, "Council-1", "awake", "Listening"),
            _make_council_agent(tmp_path, "Council-6", "paused", "Integrating"),
        ],
        peer_bus=FakeBus(),
        param_store=store,
        param_specs=specs,
        default_values={
            "discussion_max_completion_tokens": 1024,
            "dashboard_refresh_interval_seconds": 5,
        },
        locked_settings={"repo_root"},
        optimizable_settings={"discussion_max_completion_tokens"},
    )

    state.agents[0].audit_logger.log(
        "setting_optimized",
        "Council-1",
        {
            "key": "discussion_max_completion_tokens",
            "old_value": "1024",
            "new_value": "2048",
            "reason": "More room for council synthesis",
        },
    )

    def apply_setting_change(key: str, raw_value: str) -> dict[str, Any]:
        value = int(raw_value)
        store.values[key] = value
        return {
            "key": key,
            "display": str(value),
            "value": value,
            "ptype": "int",
            "unchanged": False,
        }

    state.apply_setting_change = apply_setting_change
    return state


@pytest.fixture()
def council_state(tmp_path: Path) -> DashboardState:
    return _make_council_state(tmp_path)


@pytest.fixture()
def renamed_council_state(tmp_path: Path) -> DashboardState:
    state = _make_council_state(tmp_path)
    state.agents = [
        _make_council_agent(
            tmp_path,
            "Council-1",
            "awake",
            "Listening",
            current_name="Aurora",
            name_history=["Council-1", "Dawn", "Aurora"],
        ),
        _make_council_agent(tmp_path, "Council-6", "paused", "Integrating"),
    ]
    return state


@pytest.fixture()
async def client(dashboard_state: DashboardState) -> Any:
    app = create_app(dashboard_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
async def council_client(council_state: DashboardState) -> Any:
    app = create_app(council_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
async def renamed_council_client(renamed_council_state: DashboardState) -> Any:
    app = create_app(renamed_council_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
async def memory_client(tmp_path: Path) -> Any:
    """Client backed by a real SQLite memory store with seed data."""
    state = _make_base_state(tmp_path)

    db = await init_db(tmp_path / "test_memory.db")
    store = SQLiteMemoryStore(db)
    state.memory_store = store

    # Seed nodes
    await store.add_node(ConceptualNode(text="Test fact node", type=NodeType.FACT, id="node-1"))
    await store.add_node(ConceptualNode(text="Test goal node", type=NodeType.GOAL, id="node-2"))

    # Seed cluster referencing node-1
    await store.add_cluster(
        MemoryCluster(label="Test cluster", id="cluster-1", node_ids=["node-1"])
    )

    app = create_app(state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# ---------------------------------------------------------------------------
# 1. GET /api/status
# ---------------------------------------------------------------------------


async def test_get_status(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "paused"
    assert data["current_task"] is None
    assert isinstance(data["pending_count"], int)
    assert data["pending_count"] == 0
    assert isinstance(data["uptime"], (int, float))
    assert data["uptime"] >= 0
    assert "last_heartbeat" in data


async def test_get_council_agents(council_client: httpx.AsyncClient) -> None:
    resp = await council_client.get("/api/council/agents")
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) == 2
    assert agents[0]["name"] == "Council-1"
    assert agents[0]["is_speaking"] is True
    assert agents[1]["is_waiting_to_speak"] is True


async def test_get_dialogue(council_client: httpx.AsyncClient) -> None:
    resp = await council_client.get("/api/dialogue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["floor_holder"] == "Council-1"
    assert data["waiting_speakers"] == ["Council-6"]
    assert data["transcript"][0]["from_agent"] == "Council-1"


async def test_get_council_agent_resolves_initial_name_after_rename(
    renamed_council_client: httpx.AsyncClient,
) -> None:
    resp = await renamed_council_client.get("/api/council/agents/Council-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Aurora"
    assert data["initial_name"] == "Council-1"
    assert data["aliases"] == ["Dawn"]


async def test_get_council_agent_resolves_alias_after_rename(
    renamed_council_client: httpx.AsyncClient,
) -> None:
    resp = await renamed_council_client.get("/api/council/agents/Dawn")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Aurora"
    assert data["initial_name"] == "Council-1"


async def test_get_setting_with_history(council_client: httpx.AsyncClient) -> None:
    resp = await council_client.get("/api/settings/discussion_max_completion_tokens")
    assert resp.status_code == 200
    data = resp.json()
    assert data["display_name"] == "Discussion Max Tokens"
    assert data["current_display"] == "2048"
    assert data["history"][0]["mutation_type"] == "setting_optimized"


async def test_update_setting_via_api(
    council_client: httpx.AsyncClient, council_state: DashboardState
) -> None:
    resp = await council_client.post(
        "/api/settings/discussion_max_completion_tokens",
        data={"value": "3072"},
    )
    assert resp.status_code == 200
    assert council_state.param_store.get("discussion_max_completion_tokens", "int") == 3072
    assert resp.json()["setting"]["current_display"] == "3072"


# ---------------------------------------------------------------------------
# 2-5. Agent control transitions
# ---------------------------------------------------------------------------


async def test_agent_wake(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/agent/wake")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "awake"


async def test_agent_sleep(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/agent/sleep")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "asleep"


async def test_agent_pause(client: httpx.AsyncClient) -> None:
    # Initial state is PAUSED; wake first, then pause
    await client.post("/api/agent/wake")
    resp = await client.post("/api/agent/pause")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "paused"


async def test_agent_resume(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/agent/resume")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "awake"


# ---------------------------------------------------------------------------
# 6. POST /api/agent/message
# ---------------------------------------------------------------------------


async def test_agent_message(
    client: httpx.AsyncClient, dashboard_state: DashboardState
) -> None:
    resp = await client.post("/api/agent/message", json={"text": "Hello agent"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["message"] == "queued"
    assert dashboard_state.interrupt_manager.has_messages()


# ---------------------------------------------------------------------------
# 7-8. Identity — soul
# ---------------------------------------------------------------------------


async def test_get_soul(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/soul")
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data
    assert "AgentGolem Soul" in data["content"]


async def test_get_soul_history(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/soul/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# 9. Identity — heartbeat
# ---------------------------------------------------------------------------


async def test_get_heartbeat(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/heartbeat")
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data
    assert "Heartbeat" in data["content"]
    assert "is_due" in data
    assert "next_heartbeat" in data
    assert isinstance(data["recent_history"], list)


# ---------------------------------------------------------------------------
# 10. Logs
# ---------------------------------------------------------------------------


async def test_get_activity_logs(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/logs", params={"type": "activity"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "activity"
    assert len(data["entries"]) == 2
    # Most-recent-first ordering
    assert data["entries"][0]["action"] == "another_action"


async def test_get_audit_logs(
    client: httpx.AsyncClient, dashboard_state: DashboardState
) -> None:
    dashboard_state.audit_logger.log("test_mutation", "target1", {"key": "val"})
    resp = await client.get("/api/logs", params={"type": "audit"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "audit"
    assert len(data["entries"]) >= 1
    assert data["entries"][0]["mutation_type"] == "test_mutation"


# ---------------------------------------------------------------------------
# 11. Memory nodes (with store)
# ---------------------------------------------------------------------------


async def test_get_memory_nodes(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.get("/api/memory/nodes")
    assert resp.status_code == 200
    nodes = resp.json()
    assert len(nodes) >= 2
    ids = {n["id"] for n in nodes}
    assert "node-1" in ids
    assert "node-2" in ids


# ---------------------------------------------------------------------------
# 12. Memory node detail
# ---------------------------------------------------------------------------


async def test_get_memory_node_detail(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.get("/api/memory/nodes/node-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node"]["id"] == "node-1"
    assert data["node"]["text"] == "Test fact node"
    assert isinstance(data["edges_from"], list)
    assert isinstance(data["edges_to"], list)
    assert isinstance(data["sources"], list)


# ---------------------------------------------------------------------------
# 13. Memory clusters
# ---------------------------------------------------------------------------


async def test_get_memory_clusters(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.get("/api/memory/clusters")
    assert resp.status_code == 200
    clusters = resp.json()
    assert len(clusters) >= 1
    assert clusters[0]["id"] == "cluster-1"
    assert clusters[0]["label"] == "Test cluster"


# ---------------------------------------------------------------------------
# 14. Memory stats
# ---------------------------------------------------------------------------


async def test_get_memory_stats(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.get("/api/memory/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_nodes"] >= 2
    assert data["total_clusters"] >= 1


async def test_get_memory_stats_no_store(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/memory/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_nodes"] == 0


# ---------------------------------------------------------------------------
# 15. Approvals — list
# ---------------------------------------------------------------------------


async def test_get_approvals_empty(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/approvals")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# 16. Approve
# ---------------------------------------------------------------------------


async def test_approve_request(
    client: httpx.AsyncClient, dashboard_state: DashboardState
) -> None:
    request_id = dashboard_state.approval_gate.request_approval(
        "email_send", {"to": "test@example.com"}
    )

    # Pending list should contain the new request
    resp = await client.get("/api/approvals")
    assert len(resp.json()) == 1

    resp = await client.post(
        f"/api/approvals/{request_id}/approve", json={"reason": "Looks good"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"

    # No longer pending
    resp = await client.get("/api/approvals")
    assert resp.json() == []


# ---------------------------------------------------------------------------
# 17. Deny
# ---------------------------------------------------------------------------


async def test_deny_request(
    client: httpx.AsyncClient, dashboard_state: DashboardState
) -> None:
    request_id = dashboard_state.approval_gate.request_approval(
        "email_send", {"to": "spam@example.com"}
    )

    resp = await client.post(
        f"/api/approvals/{request_id}/deny", json={"reason": "Spam"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "denied"

    # No longer pending
    resp = await client.get("/api/approvals")
    assert resp.json() == []
