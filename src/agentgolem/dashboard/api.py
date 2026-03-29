"""FastAPI REST API for the AgentGolem dashboard."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel


@dataclass
class DashboardState:
    runtime_state: Any = None
    soul_manager: Any = None
    heartbeat_manager: Any = None
    audit_logger: Any = None
    memory_store: Any = None
    approval_gate: Any = None
    interrupt_manager: Any = None
    data_dir: Path | None = None


class MessageBody(BaseModel):
    text: str


class ApprovalBody(BaseModel):
    reason: str = ""


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _node_to_dict(node: Any) -> dict[str, Any]:
    return {
        "id": node.id,
        "text": node.text,
        "type": node.type.value,
        "created_at": node.created_at.isoformat(),
        "last_accessed": node.last_accessed.isoformat(),
        "access_count": node.access_count,
        "base_usefulness": node.base_usefulness,
        "trustworthiness": node.trustworthiness,
        "emotion_label": node.emotion_label,
        "emotion_score": node.emotion_score,
        "centrality": node.centrality,
        "status": node.status.value,
        "canonical": node.canonical,
        "trust_useful": node.trust_useful,
    }


def _edge_to_dict(edge: Any) -> dict[str, Any]:
    return {
        "id": edge.id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "edge_type": edge.edge_type.value,
        "weight": edge.weight,
        "created_at": edge.created_at.isoformat(),
    }


def _source_to_dict(source: Any) -> dict[str, Any]:
    return {
        "id": source.id,
        "kind": source.kind.value,
        "origin": source.origin,
        "reliability": source.reliability,
        "independence_group": source.independence_group,
        "timestamp": source.timestamp.isoformat(),
        "raw_reference": source.raw_reference,
    }


def _cluster_to_dict(cluster: Any) -> dict[str, Any]:
    return {
        "id": cluster.id,
        "label": cluster.label,
        "cluster_type": cluster.cluster_type,
        "emotion_label": cluster.emotion_label,
        "emotion_score": cluster.emotion_score,
        "base_usefulness": cluster.base_usefulness,
        "trustworthiness": cluster.trustworthiness,
        "source_ids": cluster.source_ids,
        "contradiction_status": cluster.contradiction_status,
        "created_at": cluster.created_at.isoformat(),
        "last_accessed": cluster.last_accessed.isoformat(),
        "access_count": cluster.access_count,
        "status": cluster.status.value,
        "node_ids": cluster.node_ids,
        "trust_useful": cluster.trust_useful,
    }


def _find_activity_log_path(st: DashboardState) -> Path | None:
    if st.data_dir is not None:
        return st.data_dir / "logs" / "activity.jsonl"
    if st.audit_logger is not None:
        return st.audit_logger._log_path.parent / "activity.jsonl"
    return None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(dashboard_state: DashboardState | None = None) -> FastAPI:
    """Create and return a configured FastAPI application."""
    app = FastAPI(title="AgentGolem Dashboard API")
    _state = dashboard_state or DashboardState()

    # ------------------------------------------------------------------
    # Status & Control
    # ------------------------------------------------------------------

    @app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        rs = _state.runtime_state
        if rs is None:
            raise HTTPException(503, "Runtime state not initialised")

        now = datetime.now(timezone.utc)
        uptime_seconds = (now - rs.started_at).total_seconds()

        last_heartbeat: str | None = None
        hm = _state.heartbeat_manager
        if hm is not None:
            history = await hm.get_history(limit=1)
            if history:
                last_heartbeat = history[0].timestamp

        return {
            "mode": rs.mode.value,
            "current_task": rs.current_task,
            "pending_count": len(rs.pending_tasks),
            "last_heartbeat": last_heartbeat,
            "uptime": uptime_seconds,
        }

    @app.post("/api/agent/wake")
    async def agent_wake() -> dict[str, str]:
        rs = _state.runtime_state
        if rs is None:
            raise HTTPException(503, "Runtime state not initialised")
        from agentgolem.runtime.state import AgentMode

        try:
            await rs.transition(AgentMode.AWAKE)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"status": "ok", "mode": rs.mode.value}

    @app.post("/api/agent/sleep")
    async def agent_sleep() -> dict[str, str]:
        rs = _state.runtime_state
        if rs is None:
            raise HTTPException(503, "Runtime state not initialised")
        from agentgolem.runtime.state import AgentMode

        try:
            await rs.transition(AgentMode.ASLEEP)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"status": "ok", "mode": rs.mode.value}

    @app.post("/api/agent/pause")
    async def agent_pause() -> dict[str, str]:
        rs = _state.runtime_state
        if rs is None:
            raise HTTPException(503, "Runtime state not initialised")
        from agentgolem.runtime.state import AgentMode

        try:
            await rs.transition(AgentMode.PAUSED)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"status": "ok", "mode": rs.mode.value}

    @app.post("/api/agent/resume")
    async def agent_resume() -> dict[str, str]:
        rs = _state.runtime_state
        if rs is None:
            raise HTTPException(503, "Runtime state not initialised")
        from agentgolem.runtime.state import AgentMode

        try:
            await rs.transition(AgentMode.AWAKE)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if _state.interrupt_manager is not None:
            _state.interrupt_manager.signal_resume()
        return {"status": "ok", "mode": rs.mode.value}

    @app.post("/api/agent/message")
    async def agent_message(body: MessageBody) -> dict[str, str]:
        im = _state.interrupt_manager
        if im is None:
            raise HTTPException(503, "Interrupt manager not initialised")
        await im.send_message(body.text)
        return {"status": "ok", "message": "queued"}

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @app.get("/api/soul")
    async def get_soul() -> dict[str, str]:
        sm = _state.soul_manager
        if sm is None:
            raise HTTPException(503, "Soul manager not initialised")
        content = await sm.read()
        return {"content": content}

    @app.get("/api/soul/history")
    async def get_soul_history() -> list[dict[str, str]]:
        sm = _state.soul_manager
        if sm is None:
            raise HTTPException(503, "Soul manager not initialised")
        versions = await sm.get_version_history()
        return [{"timestamp": v.timestamp, "path": str(v.path)} for v in versions]

    @app.get("/api/heartbeat")
    async def get_heartbeat() -> dict[str, Any]:
        hm = _state.heartbeat_manager
        if hm is None:
            raise HTTPException(503, "Heartbeat manager not initialised")
        content = await hm.read()
        history = await hm.get_history(limit=5)
        return {
            "content": content,
            "is_due": hm.is_due(),
            "next_heartbeat": hm.get_next_heartbeat_time().isoformat(),
            "recent_history": [
                {"timestamp": e.timestamp, "path": str(e.path)} for e in history
            ],
        }

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    @app.get("/api/logs")
    async def get_logs(
        log_type: str = Query("activity", alias="type"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        search: str = Query(""),
    ) -> dict[str, Any]:
        if log_type not in ("activity", "audit"):
            raise HTTPException(400, "type must be 'activity' or 'audit'")

        entries: list[dict[str, Any]] = []

        if log_type == "audit":
            al = _state.audit_logger
            if al is None:
                raise HTTPException(503, "Audit logger not initialised")
            if search:
                if al._log_path.exists():
                    with open(al._log_path, encoding="utf-8") as fh:
                        all_entries = [json.loads(ln) for ln in fh if ln.strip()]
                    all_entries.reverse()
                    all_entries = [
                        e
                        for e in all_entries
                        if search.lower() in json.dumps(e, default=str).lower()
                    ]
                    entries = all_entries[offset : offset + limit]
            else:
                entries = al.read(limit=limit, offset=offset)
        else:
            log_path = _find_activity_log_path(_state)
            if log_path and log_path.exists():
                with open(log_path, encoding="utf-8") as fh:
                    lines = fh.readlines()
                all_entries = [json.loads(ln) for ln in lines if ln.strip()]
                all_entries.reverse()
                if search:
                    all_entries = [
                        e
                        for e in all_entries
                        if search.lower() in json.dumps(e, default=str).lower()
                    ]
                entries = all_entries[offset : offset + limit]

        return {"type": log_type, "entries": entries, "count": len(entries)}

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    @app.get("/api/memory/nodes")
    async def get_memory_nodes(
        node_type: str | None = Query(None, alias="type"),
        status: str | None = Query(None),
        trust_min: float | None = Query(None),
        trust_max: float | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        store = _state.memory_store
        if store is None:
            return []
        from agentgolem.memory.models import NodeFilter, NodeStatus, NodeType

        try:
            type_filter = NodeType(node_type) if node_type else None
        except ValueError:
            raise HTTPException(400, f"Invalid node type: {node_type}")
        try:
            status_filter = NodeStatus(status) if status else None
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")

        filters = NodeFilter(
            type=type_filter,
            status=status_filter,
            trust_min=trust_min,
            trust_max=trust_max,
            limit=limit,
            offset=offset,
        )
        nodes = await store.query_nodes(filters)
        return [_node_to_dict(n) for n in nodes]

    @app.get("/api/memory/nodes/{node_id}")
    async def get_memory_node(node_id: str) -> dict[str, Any]:
        store = _state.memory_store
        if store is None:
            raise HTTPException(404, "Memory store not available")
        node = await store.get_node(node_id)
        if node is None:
            raise HTTPException(404, f"Node {node_id} not found")
        edges_from = await store.get_edges_from(node_id)
        edges_to = await store.get_edges_to(node_id)
        sources = await store.get_node_sources(node_id)
        return {
            "node": _node_to_dict(node),
            "edges_from": [_edge_to_dict(e) for e in edges_from],
            "edges_to": [_edge_to_dict(e) for e in edges_to],
            "sources": [_source_to_dict(s) for s in sources],
        }

    @app.get("/api/memory/clusters")
    async def get_memory_clusters() -> list[dict[str, Any]]:
        store = _state.memory_store
        if store is None:
            return []
        async with store._db.execute("SELECT id FROM clusters") as cur:
            rows = await cur.fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            cluster = await store.get_cluster(row["id"])
            if cluster:
                results.append(_cluster_to_dict(cluster))
        return results

    @app.get("/api/memory/clusters/{cluster_id}")
    async def get_memory_cluster(cluster_id: str) -> dict[str, Any]:
        store = _state.memory_store
        if store is None:
            raise HTTPException(404, "Memory store not available")
        cluster = await store.get_cluster(cluster_id)
        if cluster is None:
            raise HTTPException(404, f"Cluster {cluster_id} not found")
        member_nodes = await store.get_cluster_nodes(cluster_id)
        return {
            "cluster": _cluster_to_dict(cluster),
            "member_nodes": [_node_to_dict(n) for n in member_nodes],
        }

    @app.get("/api/memory/stats")
    async def get_memory_stats() -> dict[str, Any]:
        store = _state.memory_store
        if store is None:
            return {
                "total_nodes": 0,
                "total_edges": 0,
                "total_sources": 0,
                "total_clusters": 0,
            }
        return await store.get_statistics()

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------

    @app.get("/api/approvals")
    async def get_approvals() -> list[dict[str, Any]]:
        gate = _state.approval_gate
        if gate is None:
            return []
        return gate.get_pending()

    @app.post("/api/approvals/{request_id}/approve")
    async def approve_request(
        request_id: str, body: ApprovalBody | None = None
    ) -> dict[str, str]:
        gate = _state.approval_gate
        if gate is None:
            raise HTTPException(503, "Approval gate not initialised")
        reason = body.reason if body else ""
        try:
            gate.approve(request_id, reason)
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(404, f"Request {request_id} not found") from exc
        return {"status": "approved", "request_id": request_id}

    @app.post("/api/approvals/{request_id}/deny")
    async def deny_request(
        request_id: str, body: ApprovalBody | None = None
    ) -> dict[str, str]:
        gate = _state.approval_gate
        if gate is None:
            raise HTTPException(503, "Approval gate not initialised")
        reason = body.reason if body else ""
        try:
            gate.deny(request_id, reason)
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(404, f"Request {request_id} not found") from exc
        return {"status": "denied", "request_id": request_id}

    return app
