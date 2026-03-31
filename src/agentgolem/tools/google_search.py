"""Web search tool backed by Google Custom Search with shared quota controls."""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
from pydantic import BaseModel

from agentgolem.tools.base import Tool, ToolArgument, ToolResult

if TYPE_CHECKING:
    from pathlib import Path


class SearchResultItem(BaseModel):
    """Normalized single search hit."""

    title: str
    link: str
    snippet: str
    display_link: str
    rank: int


class SearchResponse(BaseModel):
    """Normalized search response independent of the backing provider."""

    backend: str
    query: str
    results: list[SearchResultItem]
    result_count: int
    total_results: int | None = None
    search_time_seconds: float | None = None
    next_start: int | None = None


@dataclass(slots=True)
class TokenBucketState:
    """In-memory representation of persisted quota state."""

    tokens: float
    updated_at: datetime


class SearchBackend(ABC):
    """Provider-agnostic search backend interface."""

    name: str

    @abstractmethod
    def is_configured(self) -> bool:
        """Return whether the backend has the credentials it needs."""

    @abstractmethod
    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        """Perform a web search and return normalized results."""


class PersistentTokenBucket:
    """Shared on-disk token bucket for API budgeting with rollover."""

    _locks: ClassVar[dict[Path, asyncio.Lock]] = {}

    def __init__(
        self,
        state_path: Path,
        *,
        capacity: int,
        refill_rate_per_hour: float,
    ) -> None:
        self._state_path = state_path
        self._capacity = float(max(1, capacity))
        self._refill_rate_per_hour = max(0.0, refill_rate_per_hour)

    async def consume(self, amount: float = 1.0) -> tuple[bool, float]:
        """Consume tokens if available and return ``(allowed, remaining)``."""
        async with self._lock():
            state = self._load_state()
            state = self._refill(state)
            if state.tokens < amount:
                self._write_state(state)
                return False, round(state.tokens, 3)
            state.tokens = max(0.0, state.tokens - amount)
            self._write_state(state)
            return True, round(state.tokens, 3)

    async def status(self) -> dict[str, float]:
        """Return current bucket status after applying refill."""
        async with self._lock():
            state = self._refill(self._load_state())
            self._write_state(state)
            return {
                "remaining_tokens": round(state.tokens, 3),
                "capacity": self._capacity,
                "refill_rate_per_hour": self._refill_rate_per_hour,
            }

    def _lock(self) -> asyncio.Lock:
        lock = self._locks.get(self._state_path)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[self._state_path] = lock
        return lock

    def _load_state(self) -> TokenBucketState:
        now = datetime.now(UTC)
        if not self._state_path.exists():
            return TokenBucketState(tokens=self._capacity, updated_at=now)

        data = json.loads(self._state_path.read_text(encoding="utf-8"))
        updated_at_raw = data.get("updated_at")
        updated_at = datetime.fromisoformat(updated_at_raw) if updated_at_raw else now
        tokens = float(data.get("tokens", self._capacity))
        return TokenBucketState(
            tokens=max(0.0, min(tokens, self._capacity)),
            updated_at=updated_at,
        )

    def _refill(self, state: TokenBucketState) -> TokenBucketState:
        now = datetime.now(UTC)
        elapsed_hours = max(0.0, (now - state.updated_at).total_seconds() / 3600.0)
        refilled = min(
            self._capacity,
            state.tokens + elapsed_hours * self._refill_rate_per_hour,
        )
        return TokenBucketState(tokens=refilled, updated_at=now)

    def _write_state(self, state: TokenBucketState) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tokens": round(state.tokens, 6),
            "updated_at": state.updated_at.isoformat(),
        }
        self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class GoogleCustomSearchBackend(SearchBackend):
    """Google Programmable Search JSON API backend."""

    name = "google_custom_search"

    def __init__(
        self,
        *,
        api_key: str,
        engine_id: str,
        timeout_seconds: int = 20,
        base_url: str = "https://customsearch.googleapis.com/customsearch/v1",
    ) -> None:
        self._api_key = api_key.strip()
        self._engine_id = engine_id.strip()
        self._timeout_seconds = timeout_seconds
        self._base_url = base_url

    def is_configured(self) -> bool:
        return bool(self._api_key and self._engine_id)

    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        if not self.is_configured():
            raise ValueError(
                "Google Custom Search is not configured. Set the API key and engine ID (cx)."
            )

        num = max(1, min(int(kwargs.get("num", 5)), 10))
        start = max(1, int(kwargs.get("start", 1)))
        safe = str(kwargs.get("safe", "active")).strip().lower() or "active"
        site_search = str(kwargs.get("site_search", "")).strip()
        gl = str(kwargs.get("gl", "")).strip()
        hl = str(kwargs.get("hl", "")).strip()
        lr = str(kwargs.get("lr", "")).strip()

        params: dict[str, Any] = {
            "key": self._api_key,
            "cx": self._engine_id,
            "q": query,
            "num": num,
            "start": start,
            "safe": "off" if safe == "off" else "active",
        }
        if site_search:
            params["siteSearch"] = site_search
            params["siteSearchFilter"] = "i"
        if gl:
            params["gl"] = gl
        if hl:
            params["hl"] = hl
        if lr:
            params["lr"] = lr

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.get(self._base_url, params=params)
            response.raise_for_status()

        payload = response.json()
        items = payload.get("items", [])
        results = [
            SearchResultItem(
                title=str(item.get("title", "")).strip(),
                link=str(item.get("link", "")).strip(),
                snippet=str(item.get("snippet", "")).strip(),
                display_link=str(item.get("displayLink", "")).strip(),
                rank=index,
            )
            for index, item in enumerate(items, start=start)
            if item.get("link")
        ]

        search_info = payload.get("searchInformation", {})
        total_results_raw = search_info.get("totalResults")
        next_pages = payload.get("queries", {}).get("nextPage") or []
        next_start = None
        if next_pages:
            next_start = int(next_pages[0].get("startIndex", 0)) or None

        return SearchResponse(
            backend=self.name,
            query=query,
            results=results,
            result_count=len(results),
            total_results=int(total_results_raw) if total_results_raw not in (None, "") else None,
            search_time_seconds=float(search_info["searchTime"])
            if search_info.get("searchTime") not in (None, "")
            else None,
            next_start=next_start,
        )


class SearchTool(Tool):
    """Generic search capability surface backed by a concrete search backend."""

    name = "search"
    description = "Search the web and return normalized results"
    domains = ("web", "research")
    safety_class = "external_read"
    side_effect_class = "network_read"
    supported_actions = ("query",)
    action_descriptions = {
        "query": "Search the web using the configured backend and return ranked results",
    }
    action_arguments = {
        "query": (
            ToolArgument("query", "Search query string"),
            ToolArgument("num", "Result count (1-10)", kind="int", required=False),
            ToolArgument("start", "Starting result index (1-based)", kind="int", required=False),
            ToolArgument(
                "site_search",
                "Optional site/domain restriction, e.g. www.lesswrong.com",
                required=False,
            ),
            ToolArgument("safe", "Safe search mode: active or off", required=False),
            ToolArgument("gl", "Optional geolocation country code", required=False),
            ToolArgument("hl", "Optional UI language code", required=False),
            ToolArgument("lr", "Optional language restrict code", required=False),
        ),
    }
    usage_hint = "search.query(query=interruptibility, site_search=www.lesswrong.com, num=5)"

    def __init__(
        self,
        backend: SearchBackend,
        *,
        quota: PersistentTokenBucket | None = None,
        default_num_results: int = 5,
        safe_mode: str = "active",
    ) -> None:
        self._backend = backend
        self._quota = quota
        self._default_num_results = default_num_results
        self._safe_mode = safe_mode

    def is_available(self) -> bool:
        return self._backend.is_configured()

    async def execute(self, action: str = "query", **kwargs: Any) -> ToolResult:
        """Search the web and return normalized provider-agnostic results."""
        if action != "query":
            return ToolResult(success=False, error=f"Unknown action: {action}")

        query = str(kwargs.get("query", "")).strip()
        if not query:
            return ToolResult(success=False, error="SearchTool requires a non-empty query")
        if not self._backend.is_configured():
            return ToolResult(
                success=False,
                error=(
                    "Search is not configured. Set GOOGLE_CUSTOM_SEARCH_API_KEY and "
                    "GOOGLE_CUSTOM_SEARCH_ENGINE_ID."
                ),
            )

        if self._quota is not None:
            allowed, remaining_before = await self._quota.consume()
            if not allowed:
                return ToolResult(
                    success=False,
                    error=(
                        "Google Custom Search quota is empty. Wait for refill or raise the "
                        "local budget."
                    ),
                    data={"quota_remaining": remaining_before},
                )

        num = kwargs.get("num", self._default_num_results)
        safe = kwargs.get("safe", self._safe_mode)
        try:
            response = await self._backend.search(
                query,
                num=num,
                start=kwargs.get("start", 1),
                safe=safe,
                site_search=kwargs.get("site_search", ""),
                gl=kwargs.get("gl", ""),
                hl=kwargs.get("hl", ""),
                lr=kwargs.get("lr", ""),
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"Search failed: {exc}")

        quota_status = await self._quota.status() if self._quota is not None else None
        payload = response.model_dump()
        payload["links"] = [item["link"] for item in payload["results"]]
        if quota_status is not None:
            payload["quota_remaining"] = quota_status["remaining_tokens"]
            payload["quota_capacity"] = quota_status["capacity"]

        return ToolResult(success=True, data=payload)
