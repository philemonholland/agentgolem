"""AgentGolem web dashboard — FastAPI + Jinja2 + HTMX frontend."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agentgolem.dashboard import api as api_mod

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _get_state() -> Any:
    """Lazy access to the shared dashboard state."""
    return api_mod.state


def _common_context(
    request: Request,
    overview: dict[str, Any],
    *,
    selected_agent_name: str | None = None,
) -> dict[str, Any]:
    agent_names = [agent["name"] for agent in overview.get("agents", [])]
    if selected_agent_name is None and agent_names:
        selected_agent_name = agent_names[0]
    return {
        "request": request,
        "overview": overview,
        "agent_names": agent_names,
        "selected_agent_name": selected_agent_name,
        "refresh_interval": api_mod.dashboard_refresh_interval_seconds(_get_state()),
    }


def _selected_snapshot(
    overview: dict[str, Any], selected_agent_name: str | None
) -> dict[str, Any] | None:
    agents = overview.get("agents", [])
    if not agents:
        return None
    if selected_agent_name:
        for agent in agents:
            names = [agent.get("name", ""), agent.get("initial_name", "")]
            names.extend(agent.get("aliases", []))
            if selected_agent_name.lower() in {name.lower() for name in names if name}:
                return agent
    return agents[0]


def _selected_agent_name(
    request_agent: str | None,
    overview: dict[str, Any],
) -> str | None:
    agents = overview.get("agents", [])
    if request_agent:
        for agent in agents:
            names = [agent.get("name", ""), agent.get("initial_name", "")]
            names.extend(agent.get("aliases", []))
            if request_agent.lower() in {name.lower() for name in names if name}:
                return agent["name"]
    return agents[0]["name"] if agents else None


def _graph_agent_paths(
    ds: Any,
    agent_name: str,
) -> tuple[str, Path | None, Path | None, Path | None]:
    """Resolve a graph agent selector to graph, audit, and sleep-state paths."""
    if agent_name:
        resolved = api_mod._resolve_agent(ds, agent_name)
        if resolved is not None:
            agent_dir = getattr(resolved, "_data_dir", None)
            display_name = getattr(resolved, "agent_name", agent_name)
            if agent_dir is not None:
                return (
                    display_name,
                    agent_dir / "memory" / "graph.db",
                    agent_dir / "logs" / "audit.jsonl",
                    agent_dir / "state" / "sleep_state.json",
                )

    data_dir = api_mod._get_data_dir(ds)
    if data_dir is None or not agent_name:
        return agent_name, None, None, None
    return (
        agent_name,
        data_dir / agent_name / "memory" / "graph.db",
        data_dir / agent_name / "logs" / "audit.jsonl",
        data_dir / agent_name / "state" / "sleep_state.json",
    )


def _approval_items(ds: Any, agent_name: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if agent_name:
        resolved = api_mod._resolve_agent(ds, agent_name)
        gate = getattr(resolved, "_approval_gate", None) if resolved is not None else None
        if gate is None:
            return []
        for item in gate.get_pending():
            items.append({**item, "agent_name": getattr(resolved, "agent_name", "")})
        return items

    for agent in api_mod._get_agents(ds):
        gate = getattr(agent, "_approval_gate", None)
        if gate is None:
            continue
        for item in gate.get_pending():
            items.append({**item, "agent_name": getattr(agent, "agent_name", "")})
    return items

def create_dashboard_app() -> FastAPI:
    """Create the combined dashboard HTML + API app."""
    ds = _get_state()
    app = api_mod.create_app(ds)
    app.title = "AgentGolem Dashboard"
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/", response_class=HTMLResponse)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard")

    @app.get("/dashboard", response_class=HTMLResponse)
    @app.get("/dashboard/consciousness", response_class=HTMLResponse)
    async def dashboard_page(
        request: Request,
        agent: str | None = Query(None),
    ) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        selected_name = _selected_agent_name(agent, overview)
        dialogue = api_mod.build_dialogue_snapshot(ds)
        settings_history = api_mod._setting_history(ds, limit=5)
        return templates.TemplateResponse(
            request,
            "consciousness.html",
            {
                **_common_context(request, overview, selected_agent_name=selected_name),
                "selected_snapshot": _selected_snapshot(overview, selected_name),
                "dialogue": dialogue,
                "settings_history": settings_history,
            },
        )

    @app.get("/dashboard/partials/council", response_class=HTMLResponse)
    async def dashboard_council_partial(
        request: Request,
        agent: str | None = Query(None),
    ) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        selected_name = _selected_agent_name(agent, overview)
        return templates.TemplateResponse(
            request,
            "_council_grid.html",
            {
                **_common_context(request, overview, selected_agent_name=selected_name),
                "selected_snapshot": _selected_snapshot(overview, selected_name),
            },
        )

    @app.get("/dashboard/partials/dialogue", response_class=HTMLResponse)
    async def dashboard_dialogue_partial(request: Request) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        dialogue = api_mod.build_dialogue_snapshot(ds)
        return templates.TemplateResponse(
            request,
            "_dialogue_panel.html",
            {
                **_common_context(request, overview),
                "dialogue": dialogue,
            },
        )

    @app.post("/dashboard/actions/mode/{action}", response_class=HTMLResponse)
    async def dashboard_mode_action(
        request: Request,
        action: str,
    ) -> HTMLResponse:
        form = await request.form()
        target_agent = str(form.get("agent", "")).strip() or None
        mapping = {
            "wake": "awake",
            "sleep": "asleep",
            "pause": "paused",
            "resume": "awake",
        }
        if action not in mapping:
            return templates.TemplateResponse(
                request,
                "_flash.html",
                {"kind": "danger", "message": f"Unknown action: {action}"},
            )

        agents = await api_mod._transition_agents(ds, mapping[action], target_agent)
        return templates.TemplateResponse(
            request,
            "_flash.html",
            {
                "kind": "success",
                "message": f"{action.title()} queued for {', '.join(agents) or 'no agents'}.",
            },
        )

    @app.post("/dashboard/actions/message", response_class=HTMLResponse)
    async def dashboard_send_message(request: Request) -> HTMLResponse:
        form = await request.form()
        text = str(form.get("text", "")).strip()
        target_agent = str(form.get("agent", "")).strip() or None
        if not text:
            return templates.TemplateResponse(
                request,
                "_flash.html",
                {"kind": "danger", "message": "Message text is required."},
            )
        recipients = await api_mod._queue_message(ds, text, target_agent)
        return templates.TemplateResponse(
            request,
            "_flash.html",
            {
                "kind": "success",
                "message": f"Queued human message for {', '.join(recipients)}.",
            },
        )

    @app.get("/dashboard/settings", response_class=HTMLResponse)
    async def settings_page(
        request: Request,
        group: str | None = Query(None),
        q: str = Query(""),
    ) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        groups = api_mod.group_settings_entries(ds)
        if group:
            groups = [item for item in groups if item["name"].lower() == group.lower()]
        if q:
            search = q.lower()
            filtered_groups = []
            for item in groups:
                entries = [
                    entry
                    for entry in item["entries"]
                    if search in entry["display_name"].lower()
                    or search in entry["key"].lower()
                    or search in entry["description"].lower()
                ]
                if entries:
                    filtered_groups.append({"name": item["name"], "entries": entries})
            groups = filtered_groups

        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                **_common_context(request, overview),
                "groups": groups,
                "selected_group": group or "",
                "search": q,
                "settings_history": api_mod._setting_history(ds),
            },
        )

    @app.get("/dashboard/settings/history", response_class=HTMLResponse)
    async def settings_history_partial(request: Request) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        return templates.TemplateResponse(
            request,
            "_settings_history.html",
            {
                **_common_context(request, overview),
                "settings_history": api_mod._setting_history(ds),
            },
        )

    @app.post("/dashboard/settings/{key}", response_class=HTMLResponse)
    async def update_setting(request: Request, key: str) -> HTMLResponse:
        form = await request.form()
        raw_value = str(form.get("value", ""))
        try:
            if ds.apply_setting_change is None:
                raise ValueError("Live setting updates are not available.")
            ds.apply_setting_change(key, raw_value)
            entry = next(item for item in api_mod.build_settings_entries(ds) if item["key"] == key)
            return templates.TemplateResponse(
                request,
                "_setting_card.html",
                {"entry": entry},
            )
        except StopIteration:
            return templates.TemplateResponse(
                request,
                "_flash.html",
                {"kind": "danger", "message": f"Unknown setting: {key}"},
            )
        except Exception as exc:
            entry = next(
                (item for item in api_mod.build_settings_entries(ds) if item["key"] == key),
                None,
            )
            if entry is None:
                return templates.TemplateResponse(
                    request,
                    "_flash.html",
                    {"kind": "danger", "message": str(exc)},
                )
            return templates.TemplateResponse(
                request,
                "_setting_card.html",
                {"entry": entry, "error": str(exc)},
            )

    @app.get("/dashboard/soul", response_class=HTMLResponse)
    async def soul_page(
        request: Request,
        agent: str | None = Query(None),
    ) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        selected_name = _selected_agent_name(agent, overview)
        soul_manager = api_mod._selected_soul_manager(ds, selected_name)
        content = ""
        versions: list[Any] = []
        if soul_manager is not None:
            content = await api_mod._run_on_agent_loop(ds, soul_manager.read())
            versions = await api_mod._run_on_agent_loop(ds, soul_manager.get_version_history())

        return templates.TemplateResponse(
            request,
            "soul.html",
            {
                **_common_context(request, overview, selected_agent_name=selected_name),
                "content": content,
                "versions": versions,
            },
        )

    @app.get("/dashboard/heartbeat", response_class=HTMLResponse)
    async def heartbeat_page(
        request: Request,
        agent: str | None = Query(None),
    ) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        selected_name = _selected_agent_name(agent, overview)
        heartbeat_manager = api_mod._selected_heartbeat_manager(ds, selected_name)
        content = ""
        history: list[Any] = []
        if heartbeat_manager is not None:
            content = await api_mod._run_on_agent_loop(ds, heartbeat_manager.read())
            history = await api_mod._run_on_agent_loop(ds, heartbeat_manager.get_history())

        return templates.TemplateResponse(
            request,
            "heartbeat.html",
            {
                **_common_context(request, overview, selected_agent_name=selected_name),
                "content": content,
                "history": history,
            },
        )

    @app.get("/dashboard/logs", response_class=HTMLResponse)
    async def logs_page(
        request: Request,
        log_type: str = Query("audit", alias="type"),
        search: str = Query("", alias="q"),
        limit: int = Query(50),
        agent: str | None = Query(None),
    ) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        selected_name = _selected_agent_name(agent, overview)
        entries: list[dict[str, Any]] = []

        if log_type == "activity":
            selected_agent = api_mod._resolve_agent(ds, selected_name)
            log_path = (
                api_mod._find_activity_log_path_for_agent(selected_agent)
                if selected_agent
                else None
            )
            if log_path and log_path.exists():
                entries = api_mod._load_jsonl_entries(log_path, limit=limit, search=search)
        else:
            audit_logger = api_mod._selected_audit_logger(ds, selected_name)
            if audit_logger is not None:
                entries = audit_logger.read(limit=limit)
                if search:
                    lowered = search.lower()
                    entries = [entry for entry in entries if lowered in str(entry).lower()]

        return templates.TemplateResponse(
            request,
            "logs.html",
            {
                **_common_context(request, overview, selected_agent_name=selected_name),
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
        agent: str | None = Query(None),
    ) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        selected_name = _selected_agent_name(agent, overview)
        memory_store = api_mod._selected_memory_store(ds, selected_name)
        nodes: list[Any] = []
        clusters: list[Any] = []
        stats: dict[str, int] = {
            "total_nodes": 0,
            "total_edges": 0,
            "total_clusters": 0,
            "total_sources": 0,
        }

        if memory_store is not None:
            try:
                from agentgolem.memory.models import NodeFilter, NodeStatus, NodeType

                node_filter = NodeFilter(trust_min=trust_min, trust_max=trust_max, limit=100)
                if type_filter:
                    node_filter.type = NodeType(type_filter.lower())
                if status_filter:
                    node_filter.status = NodeStatus(status_filter.lower())
                nodes = await memory_store.query_nodes(node_filter)
                stats = await memory_store.get_statistics()

                async with memory_store._db.execute("SELECT id FROM clusters LIMIT 50") as cur:
                    rows = await cur.fetchall()
                for row in rows:
                    cluster = await memory_store.get_cluster(row["id"])
                    if cluster:
                        clusters.append(cluster)
            except Exception:
                nodes = []
                clusters = []

        return templates.TemplateResponse(
            request,
            "memory.html",
            {
                **_common_context(request, overview, selected_agent_name=selected_name),
                "nodes": nodes,
                "clusters": clusters,
                "stats": stats,
                "type_filter": type_filter.upper(),
                "status_filter": status_filter.upper(),
                "trust_min": trust_min,
                "trust_max": trust_max,
            },
        )

    @app.get("/dashboard/memory/nodes/{node_id}", response_class=HTMLResponse)
    async def node_detail_page(
        request: Request,
        node_id: str,
        agent: str | None = Query(None),
    ) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        selected_name = _selected_agent_name(agent, overview)
        memory_store = api_mod._selected_memory_store(ds, selected_name)
        node = None
        edges_out: list[Any] = []
        edges_in: list[Any] = []
        sources: list[Any] = []

        if memory_store is not None:
            try:
                node = await memory_store.get_node(node_id)
                if node:
                    edges_out = await memory_store.get_edges_from(node_id)
                    edges_in = await memory_store.get_edges_to(node_id)
                    sources = await memory_store.get_node_sources(node_id)
            except Exception:
                node = None

        return templates.TemplateResponse(
            request,
            "node_detail.html",
            {
                **_common_context(request, overview, selected_agent_name=selected_name),
                "node": node,
                "edges_out": edges_out,
                "edges_in": edges_in,
                "sources": sources,
            },
        )

    @app.get("/dashboard/memory/clusters/{cluster_id}", response_class=HTMLResponse)
    async def cluster_detail_page(
        request: Request,
        cluster_id: str,
        agent: str | None = Query(None),
    ) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        selected_name = _selected_agent_name(agent, overview)
        memory_store = api_mod._selected_memory_store(ds, selected_name)
        cluster = None
        members: list[Any] = []

        if memory_store is not None:
            try:
                cluster = await memory_store.get_cluster(cluster_id)
                if cluster:
                    members = await memory_store.get_cluster_nodes(cluster_id)
            except Exception:
                cluster = None

        return templates.TemplateResponse(
            request,
            "cluster_detail.html",
            {
                **_common_context(request, overview, selected_agent_name=selected_name),
                "cluster": cluster,
                "members": members,
                "sources": [],
            },
        )

    # ── Graph visualizer (embedded D3.js) ──

    @app.get("/dashboard/graph", response_class=HTMLResponse)
    async def graph_page(
        request: Request,
        agent: str | None = Query(None),
    ) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        selected_name = _selected_agent_name(agent, overview)
        agent_names = [a["name"] for a in overview.get("agents", [])]
        return templates.TemplateResponse(
            request,
            "graph.html",
            {
                **_common_context(request, overview, selected_agent_name=selected_name),
                "graph_agent_names": agent_names,
            },
        )

    @app.get("/dashboard/api/graph", response_class=JSONResponse)
    async def graph_api(
        agent: str = Query(""),
        type: str = Query(""),
        status: str = Query(""),
        search: str = Query(""),
        limit: int = Query(500),
    ) -> JSONResponse:
        """Return graph data (nodes, edges, clusters, stats) for D3 rendering."""
        import asyncio
        import hashlib
        import json
        import sqlite3
        from datetime import UTC, datetime, timedelta

        recent_window = api_mod.dashboard_recent_change_seconds(ds)
        resolved_agent_name, db_path, audit_path, sleep_state_path = _graph_agent_paths(ds, agent)
        empty_live = {
            "graph_hash": "0:0:",
            "recent_window_seconds": recent_window,
            "latest_activity_ts": "",
            "activity_count": 0,
            "recent_activity": [],
            "activated_node_ids": [],
            "activated_edge_ids": [],
        }
        empty_response = {
            "nodes": [],
            "edges": [],
            "clusters": [],
            "stats": {},
            "live": empty_live,
        }
        if db_path is None:
            return JSONResponse(empty_response)

        def _sync_graph() -> dict:
            if not db_path.is_file():
                return empty_response

            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA query_only = ON")
            conn.row_factory = sqlite3.Row

            def _q(sql: str, params: tuple = ()) -> list[dict]:
                return [dict(r) for r in conn.execute(sql, params).fetchall()]

            def _parse_iso_timestamp(value: str | None) -> datetime | None:
                if not value:
                    return None
                normalized = str(value).strip().replace("Z", "+00:00")
                try:
                    return datetime.fromisoformat(normalized)
                except ValueError:
                    return None

            def _latest_timestamp(*values: str | None) -> str:
                parsed = [
                    (ts, raw)
                    for raw in values
                    if (ts := _parse_iso_timestamp(raw)) is not None
                ]
                if not parsed:
                    return ""
                return max(parsed, key=lambda item: item[0])[1]

            try:
                clauses, params_list = [], []
                if type:
                    clauses.append("type = ?")
                    params_list.append(type)
                if status:
                    clauses.append("status = ?")
                    params_list.append(status)
                if search:
                    clauses.append("text LIKE ?")
                    params_list.append(f"%{search}%")
                where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
                safe_limit = min(limit, 2000)

                nodes = _q(
                    f"SELECT * FROM nodes{where} ORDER BY centrality DESC LIMIT ?",
                    (*params_list, safe_limit),
                )
                for n in nodes:
                    n["owner_agent"] = resolved_agent_name
                    n["node_id"] = n["id"]
                    n["is_peer_ghost"] = False
                    n["trust_useful"] = float(n.get("base_usefulness", 0.0)) * float(
                        n.get("trustworthiness", 0.0)
                    )

                node_ids = {n["id"] for n in nodes}
                edges = []
                if node_ids:
                    ph = ",".join("?" for _ in node_ids)
                    edges = _q(
                        f"SELECT * FROM edges WHERE source_id IN ({ph}) AND target_id IN ({ph})",
                        (*node_ids, *node_ids),
                    )

                clusters = []
                if node_ids:
                    ph = ",".join("?" for _ in node_ids)
                    cluster_rows = _q(
                        f"SELECT DISTINCT c.* FROM clusters c "
                        f"JOIN cluster_members cm ON c.id = cm.cluster_id "
                        f"WHERE cm.node_id IN ({ph})",
                        tuple(node_ids),
                    )
                    for c in cluster_rows:
                        members = _q(
                            "SELECT node_id FROM cluster_members WHERE cluster_id = ?",
                            (c["id"],),
                        )
                        c["node_ids"] = [m["node_id"] for m in members if m["node_id"] in node_ids]
                        clusters.append(c)

                stats: dict[str, Any] = {}
                for row in _q("SELECT type, COUNT(*) as cnt FROM nodes GROUP BY type"):
                    stats[row["type"]] = row["cnt"]
                total_edges = _q("SELECT COUNT(*) as cnt FROM edges")[0]["cnt"]
                stats["_total_edges"] = total_edges
                stats["_total_nodes"] = sum(v for k, v in stats.items() if not k.startswith("_"))
                latest_node = _q("SELECT MAX(created_at) as ts FROM nodes")
                latest_accessed = _q("SELECT MAX(last_accessed) as ts FROM nodes")
                latest_edge = _q(
                    "SELECT MAX("
                    "CASE WHEN modified_at != '' THEN modified_at ELSE created_at END"
                    ") as ts FROM edges"
                )

                audit_entries = (
                    api_mod._load_jsonl_entries(audit_path, limit=200)
                    if audit_path is not None and audit_path.exists()
                    else []
                )
                latest_activity_ts = ""
                recent_cutoff = datetime.now(UTC) - timedelta(seconds=recent_window)
                recent_activity: list[dict[str, Any]] = []
                activated_node_ids: set[str] = set()
                activated_edge_ids: set[str] = set()
                edge_ids = {str(edge.get("id", "")) for edge in edges if edge.get("id")}

                for node in nodes:
                    node_id = str(node.get("id", ""))
                    accessed_at = str(node.get("last_accessed", ""))
                    access_count = int(node.get("access_count", 0) or 0)
                    parsed = _parse_iso_timestamp(accessed_at)
                    if not node_id or access_count <= 0 or parsed is None or parsed < recent_cutoff:
                        continue
                    activated_node_ids.add(node_id)
                    latest_activity_ts = _latest_timestamp(latest_activity_ts, accessed_at)
                    recent_activity.append(
                        {
                            "timestamp": accessed_at,
                            "mutation_type": "node_access",
                            "target_id": node_id,
                            "target_kind": "node",
                        }
                    )

                for entry in audit_entries:
                    timestamp = str(entry.get("timestamp", ""))
                    parsed = _parse_iso_timestamp(timestamp)
                    if parsed is None:
                        continue
                    if parsed < recent_cutoff:
                        break
                    latest_activity_ts = _latest_timestamp(latest_activity_ts, timestamp)

                    target_id = str(entry.get("target_id", ""))
                    target_kind = "other"
                    if target_id in node_ids:
                        activated_node_ids.add(target_id)
                        target_kind = "node"
                    elif target_id in edge_ids:
                        activated_edge_ids.add(target_id)
                        target_kind = "edge"

                    recent_activity.append(
                        {
                            "timestamp": timestamp,
                            "mutation_type": str(entry.get("mutation_type", "")),
                            "target_id": target_id,
                            "target_kind": target_kind,
                        }
                    )

                if sleep_state_path is not None and sleep_state_path.exists():
                    try:
                        sleep_state = json.loads(sleep_state_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError, TypeError, ValueError):
                        sleep_state = {}
                    last_cycle_activity = dict(sleep_state.get("last_cycle_activity", {}))
                    sleep_timestamp = str(
                        last_cycle_activity.get(
                            "timestamp",
                            sleep_state.get("last_cycle_time", ""),
                        )
                    )
                    parsed_sleep_ts = _parse_iso_timestamp(sleep_timestamp)
                    if parsed_sleep_ts is not None and parsed_sleep_ts >= recent_cutoff:
                        sleep_phase = str(
                            last_cycle_activity.get(
                                "phase",
                                sleep_state.get("current_phase", "consolidation"),
                            )
                        )
                        sleep_node_ids = {
                            str(node_id)
                            for node_id in last_cycle_activity.get("node_ids", [])
                            if str(node_id) in node_ids
                        }
                        sleep_edge_ids = {
                            str(edge_id)
                            for edge_id in last_cycle_activity.get("edge_ids", [])
                            if str(edge_id) in edge_ids
                        }
                        activated_node_ids.update(sleep_node_ids)
                        activated_edge_ids.update(sleep_edge_ids)
                        latest_activity_ts = _latest_timestamp(latest_activity_ts, sleep_timestamp)
                        recent_activity.append(
                            {
                                "timestamp": sleep_timestamp,
                                "mutation_type": f"sleep_{sleep_phase}_walk",
                                "target_id": "",
                                "target_kind": "sleep_cycle",
                                "node_count": len(sleep_node_ids),
                                "edge_count": len(sleep_edge_ids),
                            }
                        )

                if activated_node_ids:
                    for edge in edges:
                        source_id = str(edge.get("source_id", ""))
                        target_id = str(edge.get("target_id", ""))
                        edge_id = str(edge.get("id", ""))
                        if edge_id and (
                            source_id in activated_node_ids or target_id in activated_node_ids
                        ):
                            activated_edge_ids.add(edge_id)

                recent_activity.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
                activity_fingerprint = hashlib.sha1(
                    json.dumps(
                        {
                            "latest_activity_ts": latest_activity_ts,
                            "activated_node_ids": sorted(activated_node_ids),
                            "activated_edge_ids": sorted(activated_edge_ids),
                            "recent_activity": recent_activity[:24],
                        },
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()[:16]
                stats["_latest_ts"] = _latest_timestamp(
                    latest_node[0]["ts"] if latest_node else None,
                    latest_accessed[0]["ts"] if latest_accessed else None,
                    latest_edge[0]["ts"] if latest_edge else None,
                    latest_activity_ts,
                )

                live = {
                    "graph_hash": (
                        f"{len(nodes)}:{len(edges)}:{stats['_latest_ts']}:{activity_fingerprint}"
                    ),
                    "recent_window_seconds": recent_window,
                    "latest_activity_ts": latest_activity_ts,
                    "activity_count": len(recent_activity),
                    "recent_activity": recent_activity[:48],
                    "activated_node_ids": sorted(activated_node_ids),
                    "activated_edge_ids": sorted(activated_edge_ids),
                }
                return {
                    "nodes": nodes,
                    "edges": edges,
                    "clusters": clusters,
                    "stats": stats,
                    "live": live,
                }
            finally:
                conn.close()

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _sync_graph)
        return JSONResponse(result)

    @app.get("/dashboard/api/graph/node", response_class=JSONResponse)
    async def graph_node_api(
        agent: str = Query(""),
        id: str = Query(""),
    ) -> JSONResponse:
        """Return detailed info for a single node (edges, sources, clusters)."""
        import asyncio
        import sqlite3

        resolved_agent_name, db_path, _audit_path, _sleep_state_path = _graph_agent_paths(ds, agent)
        if db_path is None:
            return JSONResponse({"error": "no data dir"}, status_code=404)

        def _sync_node() -> dict:
            if not db_path.is_file():
                return {"error": "no graph.db"}

            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA query_only = ON")
            conn.row_factory = sqlite3.Row

            def _q(sql: str, params: tuple = ()) -> list[dict]:
                return [dict(r) for r in conn.execute(sql, params).fetchall()]

            try:
                rows = _q("SELECT * FROM nodes WHERE id = ?", (id,))
                if not rows:
                    return {"error": "node not found"}
                node = rows[0]
                node["owner_agent"] = resolved_agent_name

                edges_out_raw = _q("SELECT * FROM edges WHERE source_id = ?", (id,))
                edges_in_raw = _q("SELECT * FROM edges WHERE target_id = ?", (id,))

                for e in edges_out_raw:
                    target = _q("SELECT text FROM nodes WHERE id = ?", (e["target_id"],))
                    e["target_text"] = target[0]["text"] if target else ""
                for e in edges_in_raw:
                    source = _q("SELECT text FROM nodes WHERE id = ?", (e["source_id"],))
                    e["source_text"] = source[0]["text"] if source else ""

                sources = _q(
                    "SELECT s.* FROM sources s "
                    "JOIN node_sources ns ON s.id = ns.source_id "
                    "WHERE ns.node_id = ?",
                    (id,),
                )

                clusters = _q(
                    "SELECT c.* FROM clusters c "
                    "JOIN cluster_members cm ON c.id = cm.cluster_id "
                    "WHERE cm.node_id = ?",
                    (id,),
                )

                return {
                    "node": node,
                    "edges_out": edges_out_raw,
                    "edges_in": edges_in_raw,
                    "sources": sources,
                    "clusters": clusters,
                }
            finally:
                conn.close()

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _sync_node)
        return JSONResponse(result, status_code=200 if "error" not in result else 404)

    @app.get("/dashboard/approvals", response_class=HTMLResponse)
    async def approvals_page(
        request: Request,
        agent: str | None = Query(None),
    ) -> HTMLResponse:
        overview = api_mod.build_council_overview(ds)
        selected_name = _selected_agent_name(agent, overview)
        approvals = _approval_items(ds, selected_name)
        return templates.TemplateResponse(
            request,
            "approvals.html",
            {
                **_common_context(request, overview, selected_agent_name=selected_name),
                "approvals": approvals,
            },
        )

    @app.post("/dashboard/approvals/{request_id}/{decision}", response_class=HTMLResponse)
    async def approval_action(
        request: Request,
        request_id: str,
        decision: str,
    ) -> HTMLResponse:
        form = await request.form()
        agent_name = str(form.get("agent", "")).strip() or None
        reason = str(form.get("reason", "")).strip()
        resolved = api_mod._resolve_agent(ds, agent_name)
        gate = (
            getattr(resolved, "_approval_gate", None)
            if resolved is not None
            else ds.approval_gate
        )
        if gate is None:
            return templates.TemplateResponse(
                request,
                "_flash.html",
                {"kind": "danger", "message": "Approval gate not available."},
            )
        try:
            if decision == "approve":
                gate.approve(request_id, reason)
            elif decision == "deny":
                gate.deny(request_id, reason)
            else:
                raise ValueError(f"Unknown decision: {decision}")
            return templates.TemplateResponse(
                request,
                "_flash.html",
                {
                    "kind": "success",
                    "message": (
                        f"Request {request_id} marked as "
                        f"{'approved' if decision == 'approve' else 'denied'}."
                    ),
                },
            )
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "_flash.html",
                {"kind": "danger", "message": str(exc)},
            )

    return app
