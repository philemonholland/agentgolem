"""FastAPI REST API for the AgentGolem dashboard."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

from agentgolem.experiments.ledger import ExperimentLedger


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
        return sorted(
            st.agents,
            key=lambda a: getattr(a, "_initial_agent_name", getattr(a, "agent_name", "")),
        )
    legacy = _legacy_agent(st)
    return [legacy] if legacy is not None else []


def _agent_identity_names(agent: Any) -> list[str]:
    names: list[str] = []
    for value in (
        getattr(agent, "agent_name", ""),
        getattr(agent, "_initial_agent_name", ""),
    ):
        if value and value.lower() not in {item.lower() for item in names}:
            names.append(value)
    for alias in getattr(agent, "_name_history", []):
        if isinstance(alias, str) and alias and alias.lower() not in {
            item.lower() for item in names
        }:
            names.append(alias)
    return names


def _resolve_agent(st: DashboardState, agent_name: str | None = None) -> Any | None:
    agents = _get_agents(st)
    if not agents:
        return None
    if not agent_name:
        return agents[0]

    requested = agent_name.strip().lower()
    for agent in agents:
        if requested in {name.lower() for name in _agent_identity_names(agent)}:
            return agent
    return None


def _experiment_data_dir(st: DashboardState, agent_name: str | None = None) -> Path | None:
    """Return the shared data root that holds experiment state and proposals."""
    if st.agents:
        resolved = _resolve_agent(st, agent_name)
        agent_dir = getattr(resolved, "_data_dir", None) if resolved is not None else None
        if agent_dir is not None:
            return agent_dir.parent
    return _get_data_dir(st)


def _dedupe_names(values: list[Any]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate or candidate.lower() in seen:
            continue
        deduped.append(candidate)
        seen.add(candidate.lower())
    return deduped


def _current_agent_label(agent: Any, duplicate_counts: dict[str, int]) -> str:
    current = getattr(agent, "agent_name", "") or getattr(agent, "_initial_agent_name", "?")
    initial = getattr(agent, "_initial_agent_name", current)
    if duplicate_counts.get(current.lower(), 0) > 1:
        return f"{current} ({initial})"
    return current


def _relationship_identity_maps(
    st: DashboardState,
) -> tuple[dict[str, str | None], dict[str, str]]:
    agents = _get_agents(st)
    duplicate_counts: dict[str, int] = {}
    for agent in agents:
        current = getattr(agent, "agent_name", "")
        if current:
            duplicate_counts[current.lower()] = duplicate_counts.get(current.lower(), 0) + 1

    identity_lookup: dict[str, str | None] = {}
    display_lookup: dict[str, str] = {}
    for agent in agents:
        initial = getattr(agent, "_initial_agent_name", getattr(agent, "agent_name", ""))
        display_lookup[initial] = _current_agent_label(agent, duplicate_counts)
        for name in _agent_identity_names(agent):
            lowered = name.lower()
            existing = identity_lookup.get(lowered)
            if existing is None and lowered in identity_lookup:
                continue
            if existing is not None and existing != initial:
                identity_lookup[lowered] = None
                continue
            identity_lookup[lowered] = initial
    return identity_lookup, display_lookup


def _relationship_average(
    existing_value: Any,
    existing_weight: int,
    incoming_value: Any,
    incoming_weight: int,
) -> float:
    existing = float(existing_value)
    incoming = float(incoming_value)
    left = max(existing_weight, 1)
    right = max(incoming_weight, 1)
    return ((existing * left) + (incoming * right)) / (left + right)


def _relationship_prompt_summary(relationship: dict[str, Any]) -> str:
    trust = float(relationship.get("trust", 0.5))
    trust_label = "high" if trust >= 0.7 else "low" if trust < 0.4 else "moderate"
    parts = [f"Trust: {trust_label}"]

    shared = relationship.get("shared_experiences", [])
    if isinstance(shared, list) and shared:
        parts.append(f"Shared: {', '.join(shared[-2:])}")

    disagreements = relationship.get("disagreements", [])
    if isinstance(disagreements, list) and disagreements:
        parts.append(f"Disagree about: {', '.join(disagreements[-2:])}")

    debt = float(relationship.get("intellectual_debt", 0.0))
    if abs(debt) > 0.3:
        direction = (
            "they've contributed more ideas" if debt > 0 else "you've contributed more ideas"
        )
        parts.append(direction)

    return " | ".join(parts)


def _merge_relationship_entries(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    existing_count = int(existing.get("interaction_count", 0))
    incoming_count = int(incoming.get("interaction_count", 0))
    merged = dict(existing)
    merged["peer_id"] = incoming.get("peer_id") or existing.get("peer_id")
    merged["peer_name"] = incoming.get("peer_name") or existing.get("peer_name")
    merged["aliases"] = _dedupe_names(
        [
            *existing.get("aliases", []),
            *incoming.get("aliases", []),
        ]
    )
    merged["trust"] = _relationship_average(
        existing.get("trust", 0.5),
        existing_count,
        incoming.get("trust", 0.5),
        incoming_count,
    )
    merged["intellectual_debt"] = _relationship_average(
        existing.get("intellectual_debt", 0.0),
        existing_count,
        incoming.get("intellectual_debt", 0.0),
        incoming_count,
    )
    merged["communication_compatibility"] = _relationship_average(
        existing.get("communication_compatibility", 0.5),
        existing_count,
        incoming.get("communication_compatibility", 0.5),
        incoming_count,
    )
    merged["interaction_count"] = existing_count + incoming_count
    merged["last_interaction_tick"] = max(
        int(existing.get("last_interaction_tick", 0)),
        int(incoming.get("last_interaction_tick", 0)),
    )
    merged["shared_experiences"] = _dedupe_names(
        [
            *existing.get("shared_experiences", []),
            *incoming.get("shared_experiences", []),
        ]
    )[-20:]
    merged["disagreements"] = _dedupe_names(
        [
            *existing.get("disagreements", []),
            *incoming.get("disagreements", []),
        ]
    )[-10:]
    return merged


def _normalize_relationships_for_dashboard(
    st: DashboardState,
    relationship_store: Any | None,
) -> tuple[dict[str, dict[str, Any]], str]:
    if relationship_store is None:
        return {}, ""

    raw_relationships = relationship_store.to_dict()
    if not isinstance(raw_relationships, dict):
        return {}, ""

    identity_lookup, display_lookup = _relationship_identity_maps(st)
    normalized: dict[str, dict[str, Any]] = {}

    for raw_key, raw_value in raw_relationships.items():
        if not isinstance(raw_value, dict):
            continue

        aliases = raw_value.get("aliases", [])
        alias_values = aliases if isinstance(aliases, list) else []
        candidates = _dedupe_names(
            [
                raw_value.get("peer_id"),
                raw_value.get("peer_name"),
                raw_key,
                *alias_values,
            ]
        )

        resolved_peer_id: str | None = None
        for candidate in candidates:
            resolved = identity_lookup.get(candidate.lower())
            if resolved is not None:
                resolved_peer_id = resolved
                break

        peer_id = resolved_peer_id or str(raw_value.get("peer_id") or raw_key)
        peer_name = display_lookup.get(
            resolved_peer_id or "",
            str(raw_value.get("peer_name") or raw_key),
        )
        entry = {
            "peer_id": peer_id,
            "peer_name": peer_name,
            "aliases": _dedupe_names([raw_key, raw_value.get("peer_name"), *alias_values]),
            "trust": float(raw_value.get("trust", 0.5)),
            "intellectual_debt": float(raw_value.get("intellectual_debt", 0.0)),
            "shared_experiences": _dedupe_names(raw_value.get("shared_experiences", [])),
            "disagreements": _dedupe_names(raw_value.get("disagreements", [])),
            "last_interaction_tick": int(raw_value.get("last_interaction_tick", 0)),
            "interaction_count": int(raw_value.get("interaction_count", 0)),
            "communication_compatibility": float(
                raw_value.get("communication_compatibility", 0.5)
            ),
        }

        if peer_id in normalized:
            normalized[peer_id] = _merge_relationship_entries(normalized[peer_id], entry)
        else:
            normalized[peer_id] = entry

    summary_lines = []
    for relationship in sorted(
        normalized.values(),
        key=lambda item: int(item.get("interaction_count", 0)),
        reverse=True,
    ):
        if int(relationship.get("interaction_count", 0)) <= 0:
            continue
        summary_lines.append(
            f"- {relationship['peer_name']}: {_relationship_prompt_summary(relationship)}"
        )

    summary = "Peer relationships:\n" + "\n".join(summary_lines[:5]) if summary_lines else ""
    return normalized, summary


def _get_data_dir(st: DashboardState) -> Path | None:
    """Return the parent data directory (e.g. ``data/``) that contains agent subdirs."""
    if st.data_dir is not None:
        return st.data_dir
    agents = _get_agents(st)
    for agent in agents:
        d = getattr(agent, "_data_dir", None)
        if d is not None:
            return d.parent
    return None


def _experiment_to_dict(experiment: Any) -> dict[str, Any]:
    payload = experiment.model_dump(mode="json")
    payload["metric_names"] = [metric.name for metric in experiment.metrics]
    payload["command_names"] = [command.name for command in experiment.evaluation_commands]
    payload["candidate_change_paths"] = [
        change.file_path for change in experiment.candidate_changes
    ]
    payload["candidate_change_count"] = len(experiment.candidate_changes)
    payload["review_proposal_count"] = len(experiment.review_proposal_ids)
    return payload


def build_experiment_snapshot(
    st: DashboardState,
    *,
    agent_name: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Return a dashboard-friendly summary of experiment state and recent runs."""
    data_dir = _experiment_data_dir(st, agent_name)
    empty_counts = {
        "total": 0,
        "active": 0,
        "terminal": 0,
        "pending_approvals": 0,
        "forwarded": 0,
    }
    if data_dir is None:
        return {"counts": empty_counts, "status_counts": {}, "experiments": [], "records": []}

    ledger = ExperimentLedger(data_dir)
    experiments = ledger.list_experiments()
    if agent_name:
        lowered = agent_name.lower()
        experiments = [
            experiment
            for experiment in experiments
            if experiment.proposed_by.lower() == lowered
        ]

    experiments = sorted(experiments, key=lambda item: item.created_at, reverse=True)
    experiment_ids = {experiment.id for experiment in experiments}
    records = ledger.load_records(limit=max(limit * 3, 20))
    if experiment_ids:
        records = [record for record in records if record.experiment_id in experiment_ids]
    else:
        records = []

    counts = dict(empty_counts)
    status_counts: dict[str, int] = {}
    for experiment in experiments:
        counts["total"] += 1
        if experiment.is_terminal:
            counts["terminal"] += 1
        else:
            counts["active"] += 1
        if experiment.approval_status.value == "pending":
            counts["pending_approvals"] += 1
        if experiment.review_proposal_ids:
            counts["forwarded"] += 1
        status_counts[experiment.status.value] = status_counts.get(experiment.status.value, 0) + 1

    return {
        "counts": counts,
        "status_counts": status_counts,
        "experiments": [_experiment_to_dict(experiment) for experiment in experiments[:limit]],
        "records": [record.model_dump(mode="json") for record in reversed(records[-limit:])],
    }


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
                    "agent": getattr(agent, "agent_name", "?"),
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


def _build_desires(internal_state: Any, self_model: Any) -> list[str]:
    """Synthesize a compact list of desires from internal state and self-model."""
    desires: list[str] = []
    if internal_state is not None:
        curiosity = getattr(internal_state, "curiosity_focus", "")
        intensity = getattr(internal_state, "curiosity_intensity", 0)
        if curiosity:
            label = f"Explore: {curiosity}"
            if intensity >= 0.7:
                label += " (strong)"
            desires.append(label)
        growth = getattr(internal_state, "growth_vector", "")
        if growth:
            desires.append(f"Grow toward: {growth}")
        isolation = getattr(internal_state, "isolation_signal", 0)
        if isolation > 0.5:
            desires.append("Seek connection with peers")
    if self_model is not None:
        interests = getattr(self_model, "evolving_interests", [])
        for interest in interests[:2]:
            if interest and f"Explore: {interest}" not in desires:
                desires.append(f"Interested in: {interest}")
        edges = getattr(self_model, "growth_edges", [])
        for edge in edges[:2]:
            if edge:
                desires.append(f"Develop: {edge}")
    return desires


def _developmental_badge(developmental_state: Any) -> str:
    """Return a compact badge string for the agent's developmental stage."""
    if developmental_state is None:
        return "🌱 nascent"
    try:
        from agentgolem.consciousness.developmental import stage_badge
        return stage_badge(developmental_state.current_stage)
    except Exception:
        return f"❓ {getattr(developmental_state, 'current_stage', 'unknown')}"


# ── Execution trace helpers (Meta-Harness diagnostics) ───────────────


def _load_agent_traces(agent: Any, limit: int = 20) -> list[dict[str, Any]]:
    """Load recent execution traces for a single agent."""
    from agentgolem.harness.trace import load_traces

    data_dir = getattr(agent, "_data_dir", None)
    if data_dir is None:
        return []
    traces = load_traces(data_dir, limit=limit)
    return [t.to_dict() for t in traces]


def _load_agent_trace_stats(agent: Any, limit: int = 50) -> dict[str, Any]:
    """Compute aggregate trace stats for a single agent."""
    from agentgolem.harness.trace import load_traces
    from agentgolem.harness.trace_stats import compute_trace_stats

    data_dir = getattr(agent, "_data_dir", None)
    if data_dir is None:
        return {"total_calls": 0}
    traces = load_traces(data_dir, limit=limit)
    stats = compute_trace_stats(traces)
    return stats.to_dict()


def _load_agent_activity(agent: Any, limit: int = 30) -> dict[str, Any]:
    """Load search/browse activity from execution traces."""
    from agentgolem.harness.trace import load_traces

    data_dir = getattr(agent, "_data_dir", None)
    if data_dir is None:
        return {"searches": [], "browses": [], "browse_queue": []}

    traces = load_traces(data_dir, limit=200)

    searches: list[dict[str, Any]] = []
    browses: list[dict[str, Any]] = []
    for t in traces:
        d = t.to_dict()
        if t.call_site == "_autonomous_search":
            searches.append({
                "timestamp": t.timestamp,
                "query": t.prompt_summary[:200],
                "outcome": t.outcome_value[:300] if t.outcome_value else "",
            })
        elif t.call_site == "_autonomous_browse":
            browses.append({
                "timestamp": t.timestamp,
                "summary": t.prompt_summary[:200],
                "reflection": d.get("outcome_value", "")[:300],
                "response_length": t.response_length,
            })

    browse_queue: list[str] = list(getattr(agent, "_browse_queue", []))

    return {
        "searches": searches[:limit],
        "browses": browses[:limit],
        "browse_queue": browse_queue,
    }


def _compact_trace_diagnostics(agent: Any) -> dict[str, Any] | None:
    """Return a compact diagnostic summary for the agent card, or None."""
    from agentgolem.harness.trace import load_traces
    from agentgolem.harness.trace_stats import compute_trace_stats

    data_dir = getattr(agent, "_data_dir", None)
    if data_dir is None:
        return None
    try:
        traces = load_traces(data_dir, limit=50)
        if not traces:
            return None
        stats = compute_trace_stats(traces)
        return {
            "retrieval_hit_rate": round(stats.retrieval_hit_rate, 2),
            "avg_context_tokens": round(stats.avg_context_tokens),
            "peer_engagement_rate": round(stats.peer_engagement_rate, 2),
            "total_calls": stats.total_calls,
            "badge": (
                f"📊 Retrieval: {stats.retrieval_hit_rate:.0%}"
                f" | Context: {stats.avg_context_tokens:,.0f}"
                f" | Engagement: {stats.peer_engagement_rate:.0%}"
            ),
        }
    except Exception:
        return None


def _build_agent_snapshot(st: DashboardState, agent: Any) -> dict[str, Any]:
    bus = st.peer_bus
    data_dir = getattr(agent, "_data_dir", None)
    recent_window = dashboard_recent_change_seconds(st)
    activity_limit = dashboard_activity_limit(st)

    runtime_state = getattr(agent, "runtime_state", None)
    internal_state = getattr(agent, "_internal_state", None)
    temperament = getattr(agent, "_temperament", None)
    emotional_dynamics = getattr(agent, "_emotional_dynamics", None)
    relationship_store = getattr(agent, "_relationship_store", None)
    developmental_state = getattr(agent, "_developmental_state", None)
    monitor = getattr(agent, "_metacognitive_monitor", None)
    observation = getattr(monitor, "last_observation", None)
    attention_director = getattr(agent, "_attention_director", None)
    self_model = getattr(agent, "_self_model", None)
    narrative_synthesizer = getattr(agent, "_narrative_synthesizer", None)
    latest_chapter = getattr(narrative_synthesizer, "latest_chapter", None)
    relationships, relationships_summary = _normalize_relationships_for_dashboard(
        st, relationship_store
    )

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
        "aliases": [
            alias
            for alias in getattr(agent, "_name_history", [])
            if isinstance(alias, str)
            and alias
            and alias.lower() != getattr(agent, "agent_name", "").lower()
            and alias.lower() != getattr(agent, "_initial_agent_name", "").lower()
        ],
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
        "latest_narrative": latest_chapter.to_dict() if latest_chapter is not None else {},
        "narrative_summary": latest_chapter.summary if latest_chapter is not None else "No narrative chapter yet.",
        "desires": _build_desires(internal_state, self_model),
        "temperament": temperament.to_dict() if temperament is not None else {},
        "temperament_label": temperament.short_label() if temperament is not None else "",
        "ocean_scores": temperament.ocean_scores() if temperament is not None else {},
        "emotional_dynamics": emotional_dynamics.to_dict() if emotional_dynamics is not None else {},
        "emotional_baseline": (
            emotional_dynamics.effective_baseline if emotional_dynamics is not None else 0.0
        ),
        "formative_events_count": (
            len(emotional_dynamics.formative_events) if emotional_dynamics is not None else 0
        ),
        "relationships": relationships,
        "relationships_summary": relationships_summary,
        "developmental_stage": (
            developmental_state.current_stage if developmental_state is not None else "nascent"
        ),
        "developmental_badge": (
            _developmental_badge(developmental_state)
        ),
        "developmental_state": (
            developmental_state.to_dict() if developmental_state is not None else {}
        ),
        "consciousness_tick": getattr(agent, "_consciousness_tick_counter", 0),
        "metacognition_interval": getattr(agent, "_metacognition_interval", 3),
        "self_model_interval": getattr(agent, "_self_model_interval", 10),
        "narrative_interval": getattr(agent, "_narrative_interval", 15),
        "recent_thoughts": list(reversed(getattr(agent, "_recent_thoughts", [])[-activity_limit:])),
        "heartbeat_due": (
            heartbeat_manager.is_due() if heartbeat_manager is not None else False
        ),
        "discussion_limits": {
            "max_completion_tokens": getattr(agent, "_discussion_max_completion_tokens", None),
            "peer_message_max_chars": getattr(agent, "_peer_msg_limit", None),
        },
        "trace_diagnostics": _compact_trace_diagnostics(agent),
        "self_benchmark": getattr(agent, "_last_self_benchmark", None),
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

    @app.get("/api/council/agents/{agent_name}/traces")
    async def get_agent_traces(
        agent_name: str, limit: int = Query(20, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        agent = _resolve_agent(_state, agent_name)
        if agent is None:
            raise HTTPException(404, f"Agent '{agent_name}' not found")
        return _load_agent_traces(agent, limit)

    @app.get("/api/council/agents/{agent_name}/trace-stats")
    async def get_agent_trace_stats(
        agent_name: str, limit: int = Query(50, ge=1, le=500),
    ) -> dict[str, Any]:
        agent = _resolve_agent(_state, agent_name)
        if agent is None:
            raise HTTPException(404, f"Agent '{agent_name}' not found")
        return _load_agent_trace_stats(agent, limit)

    @app.get("/api/council/agents/{agent_name}/activity")
    async def get_agent_activity(
        agent_name: str, limit: int = Query(30, ge=1, le=200),
    ) -> dict[str, Any]:
        """Return search/browse activity for an agent."""
        agent = _resolve_agent(_state, agent_name)
        if agent is None:
            raise HTTPException(404, f"Agent '{agent_name}' not found")
        return _load_agent_activity(agent, limit)

    # ── Attention request endpoints ──────────────────────────────────

    @app.get("/api/attention/pending")
    async def get_attention_pending() -> list[dict[str, Any]]:
        from agentgolem.runtime.attention import list_pending

        data_dir = _state.data_dir or Path("data")
        return [r.to_dict() for r in list_pending(data_dir)]

    @app.get("/api/attention/history")
    async def get_attention_history() -> list[dict[str, Any]]:
        from agentgolem.runtime.attention import list_all

        data_dir = _state.data_dir or Path("data")
        return [r.to_dict() for r in list_all(data_dir) if r.resolved]

    @app.get("/api/team-goal")
    async def get_team_goal() -> dict[str, Any]:
        from agentgolem.runtime.team_goals import load_active_team_goal

        data_dir = _state.data_dir or Path("data")
        goal = load_active_team_goal(data_dir)
        if goal is None:
            return {"active": False}
        return {"active": True, **goal.to_dict()}

    @app.get("/api/council/agents/{agent_name}/outcomes")
    async def get_agent_outcomes(
        agent_name: str,
        limit: int = Query(50, ge=1, le=500),
    ) -> dict[str, Any]:
        from agentgolem.harness.outcomes import compute_outcome_stats
        from agentgolem.harness.trace import load_traces

        agent = _find_agent(_state, agent_name)
        data_dir = getattr(agent, "_data_dir", None)
        if data_dir is None:
            return {"total_actions": 0}
        traces = load_traces(data_dir, limit=limit)
        stats = compute_outcome_stats(traces)
        return stats.to_dict()

    @app.get("/api/council/agents/{agent_name}/self-benchmarks")
    async def get_agent_self_benchmarks(
        agent_name: str,
        limit: int = Query(20, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        from agentgolem.benchmarks.self_eval import load_self_benchmarks

        agent = _find_agent(_state, agent_name)
        data_dir = getattr(agent, "_data_dir", None)
        if data_dir is None:
            return []
        results = load_self_benchmarks(data_dir, limit=limit)
        return [r.to_dict() for r in results]

    @app.get("/api/council/agents/{agent_name}/templates")
    async def get_agent_templates(agent_name: str) -> dict[str, Any]:
        agent = _find_agent(_state, agent_name)
        registry = getattr(agent, "_template_registry", None)
        if registry is None:
            return {"templates": {}}
        return {"templates": registry.to_dict()}

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

    @app.get("/api/experiments")
    async def get_experiments(
        agent: str | None = Query(None),
        limit: int = Query(10, ge=1, le=100),
    ) -> dict[str, Any]:
        return build_experiment_snapshot(_state, agent_name=agent, limit=limit)

    @app.get("/api/experiments/{experiment_id}")
    async def get_experiment(
        experiment_id: str,
        agent: str | None = Query(None),
    ) -> dict[str, Any]:
        data_dir = _experiment_data_dir(_state, agent)
        if data_dir is None:
            raise HTTPException(404, "Experiment ledger not initialised")

        ledger = ExperimentLedger(data_dir)
        experiment = ledger.load_experiment(experiment_id)
        if experiment is None:
            raise HTTPException(404, f"Unknown experiment: {experiment_id}")

        records = ledger.load_records(limit=25, experiment_id=experiment_id)
        return {
            **_experiment_to_dict(experiment),
            "records": [record.model_dump(mode="json") for record in reversed(records)],
        }

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
