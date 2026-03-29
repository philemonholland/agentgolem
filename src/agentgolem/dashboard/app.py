"""AgentGolem web dashboard — FastAPI + Jinja2 + HTMX frontend."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _get_state() -> Any:
    """Lazy import of the shared dashboard state from the API module."""
    from agentgolem.dashboard.api import state  # noqa: PLC0415

    return state


def create_dashboard_app() -> FastAPI:
    """Create and configure the full dashboard FastAPI application.

    Returns a FastAPI app with:
    - HTML page routes served via Jinja2 + HTMX
    - The API router from ``api.py`` (if available) mounted automatically
    """
    app = FastAPI(title="AgentGolem Dashboard")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Mount the API router/sub-app from api.py when available.
    try:
        from agentgolem.dashboard.api import router as api_router  # noqa: PLC0415

        app.include_router(api_router)
    except (ImportError, AttributeError):
        pass

    # ------------------------------------------------------------------
    # Page routes (serve HTML — no secrets exposed)
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard")

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page(request: Request) -> HTMLResponse:
        ds = _get_state()
        status: dict[str, Any] = {}
        if ds.runtime_state:
            status = ds.runtime_state.to_dict()

        pending_approvals_count = 0
        if ds.approval_gate:
            pending_approvals_count = len(ds.approval_gate.get_pending())

        return templates.TemplateResponse(
            request,
            "status.html",
            {
                "status": status,
                "pending_approvals_count": pending_approvals_count,
            },
        )

    @app.get("/dashboard/soul", response_class=HTMLResponse)
    async def soul_page(request: Request) -> HTMLResponse:
        ds = _get_state()
        content = ""
        versions: list[Any] = []
        if ds.soul_manager:
            content = await ds.soul_manager.read()
            versions = await ds.soul_manager.get_version_history()

        return templates.TemplateResponse(
            request,
            "soul.html",
            {"content": content, "versions": versions},
        )

    @app.get("/dashboard/heartbeat", response_class=HTMLResponse)
    async def heartbeat_page(request: Request) -> HTMLResponse:
        ds = _get_state()
        content = ""
        history: list[Any] = []
        if ds.heartbeat_manager:
            content = await ds.heartbeat_manager.read()
            history = await ds.heartbeat_manager.get_history()

        return templates.TemplateResponse(
            request,
            "heartbeat.html",
            {"content": content, "history": history},
        )

    @app.get("/dashboard/logs", response_class=HTMLResponse)
    async def logs_page(
        request: Request,
        log_type: str = Query("audit", alias="type"),
        search: str = Query("", alias="q"),
        limit: int = Query(50),
    ) -> HTMLResponse:
        ds = _get_state()
        entries: list[dict[str, Any]] = []

        if ds.audit_logger and log_type == "activity":
            try:
                from agentgolem.dashboard.replay import AuditReplay  # noqa: PLC0415

                data_dir = ds.audit_logger._log_path.parent.parent
                replay = AuditReplay(data_dir)
                entries = replay.read_activity(limit=limit, search=search or None)
            except Exception:
                entries = []
        elif ds.audit_logger:
            entries = ds.audit_logger.read(limit=limit)
            if search:
                search_lower = search.lower()
                entries = [e for e in entries if search_lower in str(e).lower()]

        return templates.TemplateResponse(
            request,
            "logs.html",
            {
                "entries": entries,
                "log_type": log_type,
                "search": search,
                "limit": limit,
            },
        )

    @app.get("/dashboard/memory", response_class=HTMLResponse)
    async def memory_page(
        request: Request,
        type_filter: str = Query("", alias="type"),
        status_filter: str = Query("", alias="status"),
        trust_min: float = Query(0.0),
        trust_max: float = Query(1.0),
    ) -> HTMLResponse:
        ds = _get_state()
        nodes: list[Any] = []
        clusters: list[Any] = []
        stats: dict[str, int] = {"node_count": 0, "edge_count": 0, "cluster_count": 0}

        memory_store = getattr(ds, "memory_store", None)
        if memory_store:
            try:
                from agentgolem.memory.models import NodeFilter, NodeStatus, NodeType

                nf = NodeFilter(trust_min=trust_min, trust_max=trust_max)
                if type_filter:
                    nf.type = NodeType(type_filter)
                if status_filter:
                    nf.status = NodeStatus(status_filter)
                nodes = await memory_store.query_nodes(nf)
            except Exception:
                pass

        return templates.TemplateResponse(
            request,
            "memory.html",
            {
                "nodes": nodes,
                "clusters": clusters,
                "stats": stats,
                "type_filter": type_filter,
                "status_filter": status_filter,
                "trust_min": trust_min,
                "trust_max": trust_max,
            },
        )

    @app.get("/dashboard/memory/nodes/{node_id}", response_class=HTMLResponse)
    async def node_detail_page(request: Request, node_id: str) -> HTMLResponse:
        ds = _get_state()
        node = None
        edges_out: list[Any] = []
        edges_in: list[Any] = []
        sources: list[Any] = []

        memory_store = getattr(ds, "memory_store", None)
        if memory_store:
            try:
                node = await memory_store.get_node(node_id)
                if node:
                    edges_out = await memory_store.get_edges_from(node_id)
                    edges_in = await memory_store.get_edges_to(node_id)
                    sources = await memory_store.get_node_sources(node_id)
            except Exception:
                pass

        return templates.TemplateResponse(
            request,
            "node_detail.html",
            {
                "node": node,
                "edges_out": edges_out,
                "edges_in": edges_in,
                "sources": sources,
            },
        )

    @app.get("/dashboard/memory/clusters/{cluster_id}", response_class=HTMLResponse)
    async def cluster_detail_page(request: Request, cluster_id: str) -> HTMLResponse:
        ds = _get_state()
        cluster = None
        members: list[Any] = []
        sources: list[Any] = []

        memory_store = getattr(ds, "memory_store", None)
        if memory_store:
            try:
                cluster = await memory_store.get_cluster(cluster_id)
                if cluster:
                    members = await memory_store.get_cluster_nodes(cluster_id)
            except Exception:
                pass

        return templates.TemplateResponse(
            request,
            "cluster_detail.html",
            {
                "cluster": cluster,
                "members": members,
                "sources": sources,
            },
        )

    @app.get("/dashboard/approvals", response_class=HTMLResponse)
    async def approvals_page(request: Request) -> HTMLResponse:
        ds = _get_state()
        approvals: list[dict[str, Any]] = []
        if ds.approval_gate:
            approvals = ds.approval_gate.get_pending()

        return templates.TemplateResponse(
            request,
            "approvals.html",
            {"approvals": approvals},
        )

    return app
