"""Attention-request system: agents escalate to the human operator."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class AttentionRequest:
    """A request from an agent for human attention."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent_name: str = ""
    reason: str = ""  # tool_failure, need_input, discovery, blocked_goal, etc.
    context: str = ""  # Detailed explanation
    urgency: str = "blocking"  # "blocking" | "informational"
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    resolved: bool = False
    resolution: str = ""
    resolved_at: str | None = None

    # ── Persistence helpers ──────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> AttentionRequest:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _requests_dir(data_dir: Path) -> Path:
    d = data_dir / "attention_requests"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_request(req: AttentionRequest, data_dir: Path) -> Path:
    """Persist a request to disk and return the file path."""
    path = _requests_dir(data_dir) / f"{req.id}.json"
    path.write_text(json.dumps(req.to_dict(), indent=2), encoding="utf-8")
    return path


def load_request(request_id: str, data_dir: Path) -> AttentionRequest | None:
    """Load a single request by ID."""
    path = _requests_dir(data_dir) / f"{request_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return AttentionRequest.from_dict(data)


def list_pending(data_dir: Path) -> list[AttentionRequest]:
    """Return all unresolved requests, oldest first."""
    reqs: list[AttentionRequest] = []
    d = _requests_dir(data_dir)
    for p in d.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            req = AttentionRequest.from_dict(data)
            if not req.resolved:
                reqs.append(req)
        except Exception:
            continue
    reqs.sort(key=lambda r: (r.timestamp, r.id))
    return reqs


def list_all(data_dir: Path) -> list[AttentionRequest]:
    """Return all requests (pending + resolved), oldest first."""
    reqs: list[AttentionRequest] = []
    d = _requests_dir(data_dir)
    for p in d.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            reqs.append(AttentionRequest.from_dict(data))
        except Exception:
            continue
    reqs.sort(key=lambda r: (r.timestamp, r.id))
    return reqs


def resolve_request(
    request_id: str,
    data_dir: Path,
    resolution: str = "",
) -> AttentionRequest | None:
    """Mark a request as resolved and persist the update."""
    req = load_request(request_id, data_dir)
    if req is None:
        return None
    req.resolved = True
    req.resolution = resolution
    req.resolved_at = datetime.now(UTC).isoformat()
    save_request(req, data_dir)
    return req


def resolve_oldest_blocking(
    data_dir: Path,
    resolution: str = "",
) -> AttentionRequest | None:
    """Resolve the oldest unresolved blocking request."""
    for req in list_pending(data_dir):
        if req.urgency == "blocking":
            return resolve_request(req.id, data_dir, resolution)
    return None
