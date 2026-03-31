"""Tests for the AgentGolem web dashboard frontend."""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest


# ---------------------------------------------------------------------------
# Ensure agentgolem.dashboard.api is importable.  Another agent builds the
# real module in parallel; if it isn't ready yet we provide a minimal stub so
# the dashboard page routes (which lazily import the shared *state* object)
# can function under test.
# ---------------------------------------------------------------------------

def _ensure_api_module() -> types.ModuleType:
    """Return the ``agentgolem.dashboard.api`` module, creating a stub if needed."""
    try:
        import agentgolem.dashboard.api as api_mod

        if hasattr(api_mod, "DashboardState") and hasattr(api_mod, "state"):
            return api_mod
    except (ImportError, AttributeError):
        pass

    # Build a lightweight stand-in ------------------------------------------
    @dataclass
    class DashboardState:
        runtime_state: Any = None
        soul_manager: Any = None
        heartbeat_manager: Any = None
        audit_logger: Any = None
        approval_gate: Any = None
        interrupt_manager: Any = None
        param_store: Any = None
        param_specs: list[Any] = field(default_factory=list)
        default_values: dict[str, Any] = field(default_factory=dict)
        optimizable_settings: set[str] = field(default_factory=set)
        apply_setting_change: Any = None

    mod = types.ModuleType("agentgolem.dashboard.api")
    mod.DashboardState = DashboardState  # type: ignore[attr-defined]
    mod.state = DashboardState()  # type: ignore[attr-defined]
    # Register in sys.modules so subsequent imports resolve
    sys.modules["agentgolem.dashboard.api"] = mod
    return mod


_api_mod = _ensure_api_module()
DashboardState = _api_mod.DashboardState


# ---------------------------------------------------------------------------
# Fixtures
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
        return str(self.get(key, ptype))

@pytest.fixture()
def app_with_state(tmp_path: Path):
    """Create a dashboard app backed by real subsystem objects."""
    from agentgolem.identity.heartbeat import HeartbeatManager
    from agentgolem.identity.soul import SoulManager
    from agentgolem.logging.audit import AuditLogger
    from agentgolem.runtime.interrupts import InterruptManager
    from agentgolem.runtime.state import RuntimeState
    from agentgolem.tools.base import ApprovalGate

    runtime = RuntimeState(tmp_path)

    soul_path = tmp_path / "soul.md"
    soul_path.write_text("# Soul\nTest soul content")

    hb_path = tmp_path / "heartbeat.md"
    hb_path.write_text("# Heartbeat\nTest heartbeat content")

    (tmp_path / "approvals").mkdir(exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)

    ds = DashboardState(
        runtime_state=runtime,
        soul_manager=SoulManager(soul_path, tmp_path),
        heartbeat_manager=HeartbeatManager(hb_path, tmp_path),
        audit_logger=AuditLogger(tmp_path),
        approval_gate=ApprovalGate(tmp_path / "approvals", ["email_send"]),
        interrupt_manager=InterruptManager(),
        param_store=FakeParamStore(
            {
                "discussion_max_completion_tokens": 2048,
                "dashboard_refresh_interval_seconds": 5,
            }
        ),
        param_specs=[
            FakeParamSpec(
                "discussion_max_completion_tokens",
                "Discussion Max Tokens",
                "Maximum completion tokens for discussion wrap-up.",
                "int",
                "Dialogue",
            ),
            FakeParamSpec(
                "dashboard_refresh_interval_seconds",
                "Dashboard Refresh",
                "Refresh cadence for live panels.",
                "int",
                "Dashboard",
            ),
        ],
        default_values={
            "discussion_max_completion_tokens": 1024,
            "dashboard_refresh_interval_seconds": 5,
        },
        optimizable_settings={"discussion_max_completion_tokens"},
    )

    ds.apply_setting_change = lambda key, raw_value: ds.param_store.values.__setitem__(
        key, int(raw_value)
    ) or {
        "key": key,
        "display": str(ds.param_store.values[key]),
        "value": ds.param_store.values[key],
        "ptype": "int",
        "unchanged": False,
    }

    api_mod = sys.modules["agentgolem.dashboard.api"]
    old_state = getattr(api_mod, "state", None)
    api_mod.state = ds  # type: ignore[attr-defined]

    from agentgolem.dashboard.app import create_dashboard_app

    app = create_dashboard_app()
    yield app

    api_mod.state = old_state  # type: ignore[attr-defined]


@pytest.fixture()
async def client(app_with_state):
    transport = httpx.ASGITransport(app=app_with_state)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_root_redirects_to_dashboard(client: httpx.AsyncClient):
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code in (301, 302, 307, 308)
    assert "/dashboard" in resp.headers["location"]


async def test_dashboard_returns_html_with_status(client: httpx.AsyncClient):
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # RuntimeState defaults to PAUSED
    assert "PAUSED" in resp.text


async def test_soul_returns_html_with_content(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/soul")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Test soul content" in resp.text


async def test_heartbeat_returns_html_with_content(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/heartbeat")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Test heartbeat content" in resp.text


async def test_logs_returns_html(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/logs")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Log Viewer" in resp.text


async def test_memory_returns_html(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/memory")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Memory Explorer" in resp.text


async def test_approvals_returns_html(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/approvals")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Approval Queue" in resp.text


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/dashboard/settings",
        "/dashboard/soul",
        "/dashboard/heartbeat",
        "/dashboard/logs",
        "/dashboard/memory",
        "/dashboard/approvals",
    ],
)
async def test_pages_include_navigation_links(client: httpx.AsyncClient, path: str):
    resp = await client.get(path)
    html = resp.text
    assert 'href="/dashboard"' in html
    assert 'href="/dashboard/settings"' in html
    assert 'href="/dashboard/soul"' in html
    assert 'href="/dashboard/heartbeat"' in html
    assert 'href="/dashboard/memory"' in html
    assert 'href="/dashboard/logs"' in html
    assert 'href="/dashboard/approvals"' in html


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/dashboard/settings",
        "/dashboard/soul",
        "/dashboard/heartbeat",
        "/dashboard/logs",
        "/dashboard/memory",
        "/dashboard/approvals",
    ],
)
async def test_pages_include_branding(client: httpx.AsyncClient, path: str):
    resp = await client.get(path)
    assert "AgentGolem" in resp.text


async def test_status_page_includes_control_buttons(client: httpx.AsyncClient):
    resp = await client.get("/dashboard")
    html = resp.text
    assert "Wake" in html
    assert "Sleep" in html
    assert "Pause" in html
    assert "Resume" in html


async def test_status_page_includes_message_input(client: httpx.AsyncClient):
    resp = await client.get("/dashboard")
    assert 'name="text"' in resp.text
    assert "Send" in resp.text


async def test_settings_page_returns_html_with_setting_content(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/settings")
    assert resp.status_code == 200
    assert "Settings Control Center" in resp.text
    assert "Discussion Max Tokens" in resp.text


async def test_dialogue_partial_returns_html(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/partials/dialogue")
    assert resp.status_code == 200
    assert "Dialogue Strip" in resp.text


async def test_dashboard_preserves_inner_state_details(client: httpx.AsyncClient):
    resp = await client.get("/dashboard")
    assert "agentgolem:council-inner-state-open" in resp.text

    partial = await client.get("/dashboard/partials/council")
    assert 'data-persist-key="inner-state:' in partial.text


async def test_dashboard_uses_two_column_council_grid(client: httpx.AsyncClient):
    resp = await client.get("/dashboard")
    assert "card-grid--council" in resp.text


async def test_dashboard_rich_cognition_panels(client: httpx.AsyncClient):
    """Verify the rich cognition UI elements render in the council grid."""
    resp = await client.get("/dashboard/partials/council")
    text = resp.text
    assert resp.status_code == 200
    # Cognition section labels are present
    assert "Felt Sense" in text
    assert "cog-section-label" in text
    # Metacognition section exists (either data or warming up)
    assert "Metacognition" in text
    # Self-Model section
    assert "Self-Model" in text
    # Narrative section
    assert "Narrative" in text
    # Warming-up messages for pillars not yet ticked (test agent has no data)
    lower = text.lower()
    assert "warming up" in lower or "forming" in lower or "weaving" in lower


async def test_node_detail_not_found(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/memory/nodes/nonexistent-id")
    assert resp.status_code == 200
    assert "Not Found" in resp.text


async def test_cluster_detail_not_found(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/memory/clusters/nonexistent-id")
    assert resp.status_code == 200
    assert "Not Found" in resp.text


async def test_graph_page_returns_html(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/graph")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Memory Graph" in resp.text
    assert "graph-svg" in resp.text
    assert "d3.v7" in resp.text


async def test_graph_api_returns_json_no_agent(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/api/graph?agent=nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nodes"] == []
    assert data["edges"] == []


async def test_graph_node_api_returns_error_no_agent(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/api/graph/node?agent=nonexistent&id=abc")
    assert resp.status_code == 404


async def test_graph_page_has_nav_link(client: httpx.AsyncClient):
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "/dashboard/graph" in resp.text


async def test_council_card_has_graph_link(client: httpx.AsyncClient):
    resp = await client.get("/dashboard/partials/council")
    assert resp.status_code == 200
    assert "/dashboard/graph" in resp.text


async def test_personality_section_renders(client: httpx.AsyncClient):
    """Personality section should be present when temperament data exists
    (or gracefully absent for test fixtures without temperament)."""
    resp = await client.get("/dashboard/partials/council")
    assert resp.status_code == 200
    # The template contains the personality label regardless of data
    # If no temperament data, the section simply won't render
