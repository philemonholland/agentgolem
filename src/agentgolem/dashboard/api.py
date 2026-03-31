"""FastAPI REST API for the AgentGolem dashboard."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import parse_qs

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
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
    agents: list[Any] = field(default_factory=list)
    peer_bus: Any = None
    param_store: Any = None
    param_specs: list[Any] = field(default_factory=list)
    default_values: dict[str, Any] = field(default_factory=dict)
    launcher_defaults: dict[str, Any] = field(default_factory=dict)
    env_key_map: dict[str, str] = field(default_factory=dict)
    settings_path: Path | None = None
    env_path: Path | None = None
    async_loop: asyncio.AbstractEventLoop | None = None
    apply_setting_change: Callable[[str, str], dict[str, Any]] | None = None
    locked_settings: set[str] = field(default_factory=set)
    optimizable_settings: set[str] = field(default_factory=set)
    human_speaking_event: Any = None
    transient_pause_event: Any = None


class ApprovalBody(BaseModel):
    reason: str = ""


state = DashboardState()


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _mask_secret(value: str) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


def _format_param_value(value: Any, ptype: str) -> str:
    if value is None:
        return "—"
    if ptype == "secret":
        return _mask_secret(str(value))
    if ptype == "list[str]":
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)
    if ptype == "dict":
        if isinstance(value, dict):
            return json.dumps(value, indent=2, sort_keys=True)
        return str(value)
    if ptype == "bool":
        return str(bool(value)).lower()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _editable_param_value(value: Any, ptype: str) -> str:
    if ptype == "secret":
        return ""
    if value is None:
        return ""
    if ptype == "list[str]":
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)
    if ptype == "dict":
        if isinstance(value, dict):
            return json.dumps(value, indent=2, sort_keys=True)
        return str(value)
    if ptype == "bool":
        return str(bool(value)).lower()
    if isinstance(value, Path):
        return str(value)
    return str(value)


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
    payload = {
        "id": edge.id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "edge_type": edge.edge_type.value,
        "weight": edge.weight,
        "created_at": edge.created_at.isoformat(),
    }
    for field_name in (
        "confidence",
        "temporal_valid_from",
        "temporal_valid_to",
        "direction",
        "constraint",
        "probability",
        "behavior",
        "modified_at",
    ):
        if hasattr(edge, field_name):
            value = getattr(edge, field_name)
            payload[field_name] = value.isoformat() if hasattr(value, "isoformat") else value
    return payload


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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _mtime(path: Path | None) -> datetime | None:
    if path is None or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _is_recent(path: Path | None, window_seconds: int) -> bool:
    updated = _mtime(path)
    if updated is None:
        return False
    return (_now() - updated).total_seconds() <= window_seconds


async def _read_request_data(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            return await request.json()
        except json.JSONDecodeError:
            return {}

    body = (await request.body()).decode("utf-8")
    if not body:
        return {}
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


async def _run_on_agent_loop(
    st: DashboardState,
    awaitable: Any,
    *,
    timeout: float = 10.0,
) -> Any:
    """Run a coroutine on the agent loop when the dashboard is threaded."""
    if st.async_loop is None:
        return await awaitable

    current = asyncio.get_running_loop()
    if current is st.async_loop:
        return await awaitable

    future = asyncio.run_coroutine_threadsafe(awaitable, st.async_loop)
    return await asyncio.to_thread(future.result, timeout)


def _legacy_agent(st: DashboardState) -> Any | None:
    if (
        st.runtime_state is None
        and st.soul_manager is None
        and st.heartbeat_manager is None
        and st.audit_logger is None
        and st.interrupt_manager is None
        and st.memory_store is None
    ):
        return None

    return SimpleNamespace(
        agent_name="AgentGolem",
        _initial_agent_name="AgentGolem",
        ethical_vector="",
        runtime_state=st.runtime_state,
        soul_manager=st.soul_manager,
        heartbeat_manager=st.heartbeat_manager,
        audit_logger=st.audit_logger,
        _approval_gate=st.approval_gate,
        interrupt_manager=st.interrupt_manager,
        _data_dir=st.data_dir or Path("."),
        _recent_thoughts=[],
        _conversation_paused=False,
        _wake_cycle_count=0,
        _name_discovered=True,
        _memory_store=st.memory_store,
        _internal_state=None,
        _metacognitive_monitor=SimpleNamespace(last_observation=None),
        _attention_director=None,
        _self_model=None,
        _narrative_synthesizer=SimpleNamespace(latest_chapter=None),
        _settings=None,
        _peer_msg_limit=None,
        _discussion_max_completion_tokens=None,
    )


def _get_agents(st: DashboardState) -> list[Any]:
    if st.agents:
        return list(st.agents)
    legacy = _legacy_agent(st)
    return [legacy] if legacy is not None else []


def _resolve_agent(st: DashboardState, agent_name: str | None = None) -> Any | None:
    agents = _get_agents(st)
    if not agents:
        return None
    if not agent_name:
        return agents[0]

    requested = agent_name.strip().lower()
    for agent in agents:
        name = getattr(agent, "agent_name", "").lower()
        if name == requested or name.startswith(requested):
            return agent
    return None


def _find_activity_log_path_for_agent(agent: Any) -> Path | None:
    data_dir = getattr(agent, "_data_dir", None)
    if data_dir is None:
        return None
    return data_dir / "logs" / "activity.jsonl"


def _load_jsonl_entries(path: Path, *, limit: int = 100, search: str = "") -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    entries.reverse()
    if search:
        search_lower = search.lower()
        entries = [
            entry
            for entry in entries
            if search_lower in json.dumps(entry, default=str).lower()
        ]
    return entries[:limit]


def _dashboard_param(st: DashboardState, key: str, ptype: str, default: Any) -> Any:
    store = st.param_store
    if store is None:
        return default
    try:
        return store.get(key, ptype)
    except Exception:
        return default


def dashboard_refresh_interval_seconds(st: DashboardState) -> int:
    return int(_dashboard_param(st, "dashboard_refresh_interval_seconds", "int", 5))


def dashboard_recent_change_seconds(st: DashboardState) -> int:
    return int(_dashboard_param(st, "dashboard_recent_change_seconds", "int", 60))


def dashboard_dialogue_limit(st: DashboardState) -> int:
    return int(_dashboard_param(st, "dashboard_dialogue_limit", "int", 10))


def dashboard_activity_limit(st: DashboardState) -> int:
    return int(_dashboard_param(st, "dashboard_activity_limit", "int", 6))


def dashboard_settings_history_limit(st: DashboardState) -> int:
    return int(_dashboard_param(st, "dashboard_settings_history_limit", "int", 25))


def _setting_source(st: DashboardState, key: str) -> tuple[str, Any]:
    store = st.param_store
    default_value = st.default_values.get(key, "")
    if store is None:
        return "default", default_value

    if key in st.launcher_defaults:
        if key in store.launcher:
            return "launcher", store.launcher[key]
        return "default", default_value

    env_key = st.env_key_map.get(key)
    if env_key:
        if env_key in store.env:
            return "env", store.env[env_key]
        return "default", default_value

    if key in store.settings:
        return "settings", store.settings[key]
    return "default", default_value


def _spec_map(st: DashboardState) -> dict[str, Any]:
    return {spec.key: spec for spec in st.param_specs}


def _load_agent_overrides(agent: Any) -> dict[str, Any]:
    data_dir = getattr(agent, "_data_dir", None)
    if data_dir is None:
        return {}
    overrides_path = data_dir / "settings_overrides.yaml"
    if not overrides_path.exists():
        return {}
    try:
        with open(overrides_path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _setting_history(st: DashboardState, key: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    spec_lookup = _spec_map(st)
    entries: list[dict[str, Any]] = []
    max_items = limit or dashboard_settings_history_limit(st)

    for agent in _get_agents(st):
        logger = getattr(agent, "audit_logger", None)
        if logger is None:
            continue
        for entry in logger.read(limit=max_items * 4):
            mutation = entry.get("mutation_type", "")
            if mutation not in {"setting_optimized", "setting_change_blocked"}:
                continue
            evidence = entry.get("evidence", {})
            setting_key = evidence.get("key", "")
            if key and setting_key != key:
                continue
            spec = spec_lookup.get(setting_key)
            ptype = getattr(spec, "ptype", "str")
            attempted = evidence.get("attempted_value")
            old_value = evidence.get("old_value")
            new_value = evidence.get("new_value")
            entries.append(
                {
                    "timestamp": entry.get("timestamp"),
                    "agent": entry.get("target_id", getattr(agent, "agent_name", "?")),
                    "mutation_type": mutation,
                    "key": setting_key,
                    "reason": evidence.get("reason", ""),
                    "old_value": _format_param_value(old_value, ptype) if old_value is not None else "—",
                    "new_value": _format_param_value(new_value, ptype) if new_value is not None else "—",
                    "attempted_value": (
                        _format_param_value(attempted, ptype) if attempted is not None else "—"
                    ),
                }
            )

    entries.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return entries[:max_items]


def build_settings_entries(st: DashboardState) -> list[dict[str, Any]]:
    store = st.param_store
    entries: list[dict[str, Any]] = []
    agents = _get_agents(st)

    for spec in st.param_specs:
        key = spec.key
        ptype = spec.ptype
        default_value = st.default_values.get(key, "")
        source_kind, operator_value = _setting_source(st, key)
        current_value = store.get(key, ptype) if store is not None else operator_value
        current_display = (
            store.get_display(key, ptype)
            if store is not None
            else _format_param_value(current_value, ptype)
        )

        overrides = []
        for agent in agents:
            agent_overrides = _load_agent_overrides(agent)
            if key in agent_overrides:
                overrides.append(
                    {
                        "agent": getattr(agent, "agent_name", "?"),
                        "value": _format_param_value(agent_overrides[key], ptype),
                    }
                )

        storage = "settings.yaml"
        if key in st.launcher_defaults:
            storage = "launcher_state.json"
        elif key in st.env_key_map:
            storage = ".env"

        entries.append(
            {
                "key": key,
                "display_name": spec.display_name,
                "description": spec.description,
                "ptype": ptype,
                "group": spec.group,
                "aliases": list(spec.aliases),
                "default_display": _format_param_value(default_value, ptype),
                "operator_display": _format_param_value(operator_value, ptype),
                "current_display": current_display,
                "editable_value": _editable_param_value(current_value, ptype),
                "source_kind": source_kind,
                "storage": storage,
                "runtime_override": bool(
                    store is not None and key in getattr(store, "_runtime_overrides", {})
                ),
                "agent_overrides": overrides,
                "is_secret": ptype == "secret",
                "is_env": key in st.env_key_map,
                "is_launcher": key in st.launcher_defaults,
                "is_locked": key in st.locked_settings,
                "is_optimizable": key in st.optimizable_settings,
            }
        )

    return entries


def group_settings_entries(st: DashboardState) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in build_settings_entries(st):
        grouped.setdefault(entry["group"], []).append(entry)
    return [
        {"name": group_name, "entries": entries}
        for group_name, entries in grouped.items()
    ]


def build_dialogue_snapshot(st: DashboardState) -> dict[str, Any]:
    bus = st.peer_bus
    limit = dashboard_dialogue_limit(st)
    agents = _get_agents(st)
    if bus is None:
        return {
            "floor_locked": False,
            "floor_holder": None,
            "waiting_speakers": [],
            "transcript": [],
            "human_messages_pending": 0,
        }

    transcript = [
        {
            "from_agent": msg.from_agent,
            "to_agent": msg.to_agent,
            "text": msg.text,
            "timestamp": msg.timestamp.isoformat(),
        }
        for msg in bus.get_transcript(limit=limit)
    ]
    human_pending = 0
    for agent in agents:
        interrupt_manager = getattr(agent, "interrupt_manager", None)
        queue = getattr(interrupt_manager, "_message_queue", None)
        if queue is not None:
            human_pending += queue.qsize()

    return {
        "floor_locked": bus.floor_locked(),
        "floor_holder": bus.floor_holder,
        "waiting_speakers": bus.get_waiting_speakers(),
        "transcript": transcript,
        "human_messages_pending": human_pending,
    }


def _build_agent_snapshot(st: DashboardState, agent: Any) -> dict[str, Any]:
    bus = st.peer_bus
    data_dir = getattr(agent, "_data_dir", None)
    recent_window = dashboard_recent_change_seconds(st)
    activity_limit = dashboard_activity_limit(st)

    runtime_state = getattr(agent, "runtime_state", None)
    internal_state = getattr(agent, "_internal_state", None)
    monitor = getattr(agent, "_metacognitive_monitor", None)
    observation = getattr(monitor, "last_observation", None)
    attention_director = getattr(agent, "_attention_director", None)
    self_model = getattr(agent, "_self_model", None)
    narrative_synthesizer = getattr(agent, "_narrative_synthesizer", None)
    latest_chapter = getattr(narrative_synthesizer, "latest_chapter", None)

    attention_directive = None
    if attention_director is not None and internal_state is not None:
        try:
            attention_directive = attention_director.compute(internal_state, observation)
        except Exception:
            attention_directive = None

    interrupt_manager = getattr(agent, "interrupt_manager", None)
    human_queue = getattr(interrupt_manager, "_message_queue", None)
    human_messages_pending = human_queue.qsize() if human_queue is not None else 0
    peer_messages_pending = (
        bus.pending_count(agent.agent_name) if bus is not None else 0
    )

    heartbeat_path = data_dir / "heartbeat.md" if data_dir is not None else None
    soul_path = data_dir / "soul.md" if data_dir is not None else None
    internal_state_path = data_dir / "internal_state.json" if data_dir is not None else None
    self_model_path = data_dir / "self_model.json" if data_dir is not None else None
    narrative_path = data_dir / "narrative_chapters.json" if data_dir is not None else None

    changed = {
        "heartbeat": _is_recent(heartbeat_path, recent_window),
        "soul": _is_recent(soul_path, recent_window),
        "internal_state": _is_recent(internal_state_path, recent_window),
        "self_model": _is_recent(self_model_path, recent_window),
        "narrative": _is_recent(narrative_path, recent_window),
    }
    heartbeat_manager = getattr(agent, "heartbeat_manager", None)

    return {
        "name": getattr(agent, "agent_name", "?"),
        "initial_name": getattr(agent, "_initial_agent_name", getattr(agent, "agent_name", "?")),
        "ethical_vector": getattr(agent, "ethical_vector", ""),
        "mode": getattr(getattr(runtime_state, "mode", None), "value", "unknown"),
        "current_task": getattr(runtime_state, "current_task", None),
        "pending_tasks": len(getattr(runtime_state, "pending_tasks", [])),
        "started_at": _iso(getattr(runtime_state, "started_at", None)),
        "wake_cycle_count": getattr(agent, "_wake_cycle_count", 0),
        "name_discovered": getattr(agent, "_name_discovered", False),
        "conversation_paused": getattr(agent, "_conversation_paused", False),
        "human_messages_pending": human_messages_pending,
        "peer_messages_pending": peer_messages_pending,
        "discussion_priority": bus.get_priority(agent.agent_name) if bus is not None else None,
        "is_speaking": bus.floor_holder == agent.agent_name if bus is not None else False,
        "is_waiting_to_speak": (
            agent.agent_name in bus.get_waiting_speakers() if bus is not None else False
        ),
        "internal_state": internal_state.to_dict() if internal_state is not None else {},
        "internal_state_summary": (
            internal_state.summary() if internal_state is not None else "No internal state yet."
        ),
        "metacognition": observation.to_dict() if observation is not None else {},
        "metacognition_summary": (
            observation.summary() if observation is not None else "No metacognitive signals."
        ),
        "attention_directive": (
            attention_directive.to_dict() if attention_directive is not None else {}
        ),
        "attention_directive_summary": (
            attention_directive.to_prompt_preamble() if attention_directive is not None else ""
        ),
        "self_model": self_model.to_dict() if self_model is not None else {},
        "self_model_summary": (
            self_model.summary() if self_model is not None else "Self-model not yet formed."
        ),
        "latest_narrative": latest_chapter.to_dict() if latest_chapter is not None else None,
        "narrative_summary": latest_chapter.summary if latest_chapter is not None else "No narrative chapter yet.",
        "recent_thoughts": list(reversed(getattr(agent, "_recent_thoughts", [])[-activity_limit:])),
        "heartbeat_due": (
            heartbeat_manager.is_due() if heartbeat_manager is not None else False
        ),
        "discussion_limits": {
            "max_completion_tokens": getattr(agent, "_discussion_max_completion_tokens", None),
            "peer_message_max_chars": getattr(agent, "_peer_msg_limit", None),
        },
        "changed_recently": changed,
        "has_recent_changes": any(changed.values()),
        "last_updated": {
            "heartbeat": _iso(_mtime(heartbeat_path)),
            "soul": _iso(_mtime(soul_path)),
            "internal_state": _iso(_mtime(internal_state_path)),
            "self_model": _iso(_mtime(self_model_path)),
            "narrative": _iso(_mtime(narrative_path)),
        },
    }


def build_council_overview(st: DashboardState) -> dict[str, Any]:
    agents = _get_agents(st)
    snapshots = [_build_agent_snapshot(st, agent) for agent in agents]
    mode_counts = {"awake": 0, "asleep": 0, "paused": 0, "unknown": 0}
    for snapshot in snapshots:
        mode = snapshot["mode"]
        mode_counts[mode if mode in mode_counts else "unknown"] += 1

    dialogue = build_dialogue_snapshot(st)
    approvals = 0
    for agent in agents:
        gate = getattr(agent, "_approval_gate", None)
        if gate is not None:
            approvals += len(gate.get_pending())

    return {
        "generated_at": _now().isoformat(),
        "agent_count": len(snapshots),
        "awake_count": mode_counts["awake"],
        "asleep_count": mode_counts["asleep"],
        "paused_count": mode_counts["paused"],
        "mode_counts": mode_counts,
        "pending_approvals": approvals,
        "floor_holder": dialogue["floor_holder"],
        "waiting_speakers": dialogue["waiting_speakers"],
        "human_messages_pending": dialogue["human_messages_pending"],
        "agents": snapshots,
    }


async def _queue_message(st: DashboardState, text: str, target_agent: str | None = None) -> list[str]:
    agents = _get_agents(st)
    if not agents:
        raise HTTPException(503, "No agents are available")

    recipients = agents
    if target_agent:
        resolved = _resolve_agent(st, target_agent)
        if resolved is None:
            raise HTTPException(404, f"Agent '{target_agent}' not found")
        recipients = [resolved]
    else:
        responder = None
        if st.peer_bus is not None:
            holder = getattr(st.peer_bus, "floor_holder", None)
            if holder:
                responder = _resolve_agent(st, holder)
            elif hasattr(st.peer_bus, "recommend_responder"):
                responder_name = st.peer_bus.recommend_responder()
                if responder_name:
                    responder = _resolve_agent(st, responder_name)
        recipients = [responder or agents[0]]

    manual_pause_active = bool(
        st.human_speaking_event is not None and st.human_speaking_event.is_set()
    )
    if not manual_pause_active and st.transient_pause_event is not None:
        st.transient_pause_event.set()
        for agent in agents:
            setattr(agent, "_conversation_paused", True)

    delivered_to: list[str] = []
    for agent in recipients:
        interrupt_manager = getattr(agent, "interrupt_manager", None)
        if interrupt_manager is None:
            continue
        await _run_on_agent_loop(st, interrupt_manager.send_message(text))
        delivered_to.append(getattr(agent, "agent_name", "?"))

    return delivered_to


async def _transition_agents(st: DashboardState, target_mode: str, target_agent: str | None = None) -> list[str]:
    from agentgolem.runtime.state import AgentMode

    mode_map = {
        "awake": AgentMode.AWAKE,
        "asleep": AgentMode.ASLEEP,
        "paused": AgentMode.PAUSED,
    }
    mode = mode_map[target_mode]

    agents = _get_agents(st)
    if not agents:
        raise HTTPException(503, "No agents are available")

    recipients = agents
    if target_agent:
        resolved = _resolve_agent(st, target_agent)
        if resolved is None:
            raise HTTPException(404, f"Agent '{target_agent}' not found")
        recipients = [resolved]

    transitioned: list[str] = []
    for agent in recipients:
        runtime_state = getattr(agent, "runtime_state", None)
        if runtime_state is None:
            continue
        await _run_on_agent_loop(st, runtime_state.transition(mode))
        if target_mode == "awake":
            interrupt_manager = getattr(agent, "interrupt_manager", None)
            if interrupt_manager is not None and hasattr(interrupt_manager, "signal_resume"):
                interrupt_manager.signal_resume()
            setattr(agent, "_conversation_paused", False)
        elif target_mode == "paused":
            setattr(agent, "_conversation_paused", True)
        transitioned.append(getattr(agent, "agent_name", "?"))
    return transitioned


def _selected_memory_store(st: DashboardState, agent_name: str | None = None) -> Any:
    agent = _resolve_agent(st, agent_name)
    if agent is not None and getattr(agent, "_memory_store", None) is not None:
        return agent._memory_store
    return st.memory_store


def _selected_audit_logger(st: DashboardState, agent_name: str | None = None) -> Any:
    agent = _resolve_agent(st, agent_name)
    if agent is not None and getattr(agent, "audit_logger", None) is not None:
        return agent.audit_logger
    return st.audit_logger


def _selected_soul_manager(st: DashboardState, agent_name: str | None = None) -> Any:
    agent = _resolve_agent(st, agent_name)
    if agent is not None and getattr(agent, "soul_manager", None) is not None:
        return agent.soul_manager
    return st.soul_manager


def _selected_heartbeat_manager(st: DashboardState, agent_name: str | None = None) -> Any:
    agent = _resolve_agent(st, agent_name)
    if agent is not None and getattr(agent, "heartbeat_manager", None) is not None:
        return agent.heartbeat_manager
    return st.heartbeat_manager


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(dashboard_state: DashboardState | None = None) -> FastAPI:
    """Create and return a configured FastAPI application."""
    global state

    if dashboard_state is not None:
        state = dashboard_state
    _state = dashboard_state or state

    app = FastAPI(title="AgentGolem Dashboard API")

    # ------------------------------------------------------------------
    # Status & Control
    # ------------------------------------------------------------------

    @app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        agents = _get_agents(_state)
        if not agents:
            raise HTTPException(503, "Runtime state not initialised")

        primary = agents[0]
        primary_runtime = getattr(primary, "runtime_state", None)
        started_at = getattr(primary_runtime, "started_at", None)
        uptime_seconds = (_now() - started_at).total_seconds() if started_at else 0.0

        heartbeat_manager = _selected_heartbeat_manager(_state)
        last_heartbeat = None
        if heartbeat_manager is not None:
            heartbeat_path = getattr(heartbeat_manager, "_heartbeat_path", None)
            last_heartbeat = _iso(_mtime(heartbeat_path))

        overview = build_council_overview(_state)
        return {
            "mode": getattr(getattr(primary_runtime, "mode", None), "value", "unknown"),
            "current_task": getattr(primary_runtime, "current_task", None),
            "pending_count": sum(len(getattr(agent.runtime_state, "pending_tasks", [])) for agent in agents),
            "last_heartbeat": last_heartbeat,
            "uptime": uptime_seconds,
            "agent_count": overview["agent_count"],
            "mode_counts": overview["mode_counts"],
            "floor_holder": overview["floor_holder"],
            "waiting_speakers": overview["waiting_speakers"],
        }

    @app.get("/api/council/agents")
    async def get_council_agents() -> list[dict[str, Any]]:
        return build_council_overview(_state)["agents"]

    @app.get("/api/council/agents/{agent_name}")
    async def get_council_agent(agent_name: str) -> dict[str, Any]:
        agent = _resolve_agent(_state, agent_name)
        if agent is None:
            raise HTTPException(404, f"Agent '{agent_name}' not found")
        return _build_agent_snapshot(_state, agent)

    @app.get("/api/dialogue")
    async def get_dialogue() -> dict[str, Any]:
        return build_dialogue_snapshot(_state)

    @app.post("/api/agent/wake")
    async def agent_wake(agent: str | None = Query(None)) -> dict[str, Any]:
        transitioned = await _transition_agents(_state, "awake", agent)
        return {"status": "ok", "mode": "awake", "agents": transitioned}

    @app.post("/api/agent/sleep")
    async def agent_sleep(agent: str | None = Query(None)) -> dict[str, Any]:
        transitioned = await _transition_agents(_state, "asleep", agent)
        return {"status": "ok", "mode": "asleep", "agents": transitioned}

    @app.post("/api/agent/pause")
    async def agent_pause(agent: str | None = Query(None)) -> dict[str, Any]:
        transitioned = await _transition_agents(_state, "paused", agent)
        return {"status": "ok", "mode": "paused", "agents": transitioned}

    @app.post("/api/agent/resume")
    async def agent_resume(agent: str | None = Query(None)) -> dict[str, Any]:
        transitioned = await _transition_agents(_state, "awake", agent)
        return {"status": "ok", "mode": "awake", "agents": transitioned}

    @app.post("/api/agent/message")
    async def agent_message(request: Request) -> dict[str, Any]:
        payload = await _read_request_data(request)
        text = str(payload.get("text", "")).strip()
        target_agent = str(payload.get("agent", "")).strip() or None
        if not text:
            raise HTTPException(400, "text is required")
        recipients = await _queue_message(_state, text, target_agent)
        return {"status": "ok", "message": "queued", "agents": recipients}

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    @app.get("/api/settings")
    async def get_settings(group: str | None = Query(None)) -> list[dict[str, Any]]:
        entries = build_settings_entries(_state)
        if group:
            entries = [entry for entry in entries if entry["group"].lower() == group.lower()]
        return entries

    @app.get("/api/settings/history")
    async def get_settings_history(
        key: str | None = Query(None),
        limit: int = Query(25, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        return _setting_history(_state, key=key, limit=limit)

    @app.get("/api/settings/{key}")
    async def get_setting(key: str) -> dict[str, Any]:
        for entry in build_settings_entries(_state):
            if entry["key"] == key:
                history = _setting_history(_state, key=key)
                return {**entry, "history": history}
        raise HTTPException(404, f"Unknown setting: {key}")

    @app.post("/api/settings/{key}")
    async def update_setting(key: str, request: Request) -> dict[str, Any]:
        if _state.apply_setting_change is None:
            raise HTTPException(503, "Live setting updates are not available")
        payload = await _read_request_data(request)
        raw_value = str(payload.get("value", ""))
        try:
            result = _state.apply_setting_change(key, raw_value)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

        entry = next((item for item in build_settings_entries(_state) if item["key"] == key), None)
        return {
            "status": "ok",
            "result": result,
            "setting": entry,
        }

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @app.get("/api/soul")
    async def get_soul(agent: str | None = Query(None)) -> dict[str, str]:
        soul_manager = _selected_soul_manager(_state, agent)
        if soul_manager is None:
            raise HTTPException(503, "Soul manager not initialised")
        content = await _run_on_agent_loop(_state, soul_manager.read())
        return {"content": content}

    @app.get("/api/soul/history")
    async def get_soul_history(agent: str | None = Query(None)) -> list[dict[str, str]]:
        soul_manager = _selected_soul_manager(_state, agent)
        if soul_manager is None:
            raise HTTPException(503, "Soul manager not initialised")
        versions = await _run_on_agent_loop(_state, soul_manager.get_version_history())
        return [{"timestamp": v.timestamp, "path": str(v.path)} for v in versions]

    @app.get("/api/heartbeat")
    async def get_heartbeat(agent: str | None = Query(None)) -> dict[str, Any]:
        heartbeat_manager = _selected_heartbeat_manager(_state, agent)
        if heartbeat_manager is None:
            raise HTTPException(503, "Heartbeat manager not initialised")
        content = await _run_on_agent_loop(_state, heartbeat_manager.read())
        history = await _run_on_agent_loop(_state, heartbeat_manager.get_history(limit=5))
        return {
            "content": content,
            "is_due": heartbeat_manager.is_due(),
            "next_heartbeat": heartbeat_manager.get_next_heartbeat_time().isoformat(),
            "recent_history": [
                {"timestamp": entry.timestamp, "path": str(entry.path)} for entry in history
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
        agent: str | None = Query(None),
    ) -> dict[str, Any]:
        if log_type not in ("activity", "audit"):
            raise HTTPException(400, "type must be 'activity' or 'audit'")

        entries: list[dict[str, Any]] = []

        if log_type == "audit":
            logger = _selected_audit_logger(_state, agent)
            if logger is None:
                raise HTTPException(503, "Audit logger not initialised")
            if search:
                if logger._log_path.exists():
                    entries = _load_jsonl_entries(logger._log_path, limit=limit + offset, search=search)
                    entries = entries[offset : offset + limit]
            else:
                entries = logger.read(limit=limit, offset=offset)
        else:
            selected_agent = _resolve_agent(_state, agent)
            log_path = (
                _find_activity_log_path_for_agent(selected_agent)
                if selected_agent is not None
                else None
            )
            if log_path and log_path.exists():
                entries = _load_jsonl_entries(log_path, limit=limit + offset, search=search)
                entries = entries[offset : offset + limit]

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
        agent: str | None = Query(None),
    ) -> list[dict[str, Any]]:
        store = _selected_memory_store(_state, agent)
        if store is None:
            return []
        from agentgolem.memory.models import NodeFilter, NodeStatus, NodeType

        try:
            type_filter = NodeType(node_type) if node_type else None
        except ValueError as exc:
            raise HTTPException(400, f"Invalid node type: {node_type}") from exc
        try:
            status_filter = NodeStatus(status) if status else None
        except ValueError as exc:
            raise HTTPException(400, f"Invalid status: {status}") from exc

        filters = NodeFilter(
            type=type_filter,
            status=status_filter,
            trust_min=trust_min,
            trust_max=trust_max,
            limit=limit,
            offset=offset,
        )
        nodes = await store.query_nodes(filters)
        return [_node_to_dict(node) for node in nodes]

    @app.get("/api/memory/nodes/{node_id}")
    async def get_memory_node(node_id: str, agent: str | None = Query(None)) -> dict[str, Any]:
        store = _selected_memory_store(_state, agent)
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
            "edges_from": [_edge_to_dict(edge) for edge in edges_from],
            "edges_to": [_edge_to_dict(edge) for edge in edges_to],
            "sources": [_source_to_dict(source) for source in sources],
        }

    @app.get("/api/memory/clusters")
    async def get_memory_clusters(agent: str | None = Query(None)) -> list[dict[str, Any]]:
        store = _selected_memory_store(_state, agent)
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
    async def get_memory_cluster(cluster_id: str, agent: str | None = Query(None)) -> dict[str, Any]:
        store = _selected_memory_store(_state, agent)
        if store is None:
            raise HTTPException(404, "Memory store not available")
        cluster = await store.get_cluster(cluster_id)
        if cluster is None:
            raise HTTPException(404, f"Cluster {cluster_id} not found")
        member_nodes = await store.get_cluster_nodes(cluster_id)
        return {
            "cluster": _cluster_to_dict(cluster),
            "member_nodes": [_node_to_dict(node) for node in member_nodes],
        }

    @app.get("/api/memory/stats")
    async def get_memory_stats(agent: str | None = Query(None)) -> dict[str, Any]:
        store = _selected_memory_store(_state, agent)
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
    async def get_approvals(agent: str | None = Query(None)) -> list[dict[str, Any]]:
        resolved = _resolve_agent(_state, agent)
        gate = getattr(resolved, "_approval_gate", None) if resolved is not None else _state.approval_gate
        if gate is None:
            return []
        return gate.get_pending()

    @app.post("/api/approvals/{request_id}/approve")
    async def approve_request(
        request_id: str,
        body: ApprovalBody | None = None,
        agent: str | None = Query(None),
    ) -> dict[str, str]:
        resolved = _resolve_agent(_state, agent)
        gate = getattr(resolved, "_approval_gate", None) if resolved is not None else _state.approval_gate
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
        request_id: str,
        body: ApprovalBody | None = None,
        agent: str | None = Query(None),
    ) -> dict[str, str]:
        resolved = _resolve_agent(_state, agent)
        gate = getattr(resolved, "_approval_gate", None) if resolved is not None else _state.approval_gate
        if gate is None:
            raise HTTPException(503, "Approval gate not initialised")
        reason = body.reason if body else ""
        try:
            gate.deny(request_id, reason)
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(404, f"Request {request_id} not found") from exc
        return {"status": "denied", "request_id": request_id}

    return app
