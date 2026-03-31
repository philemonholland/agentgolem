"""Tests for Google Custom Search and shared quota handling."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import Response

from agentgolem.tools.google_search import (
    GoogleCustomSearchBackend,
    PersistentTokenBucket,
    SearchTool,
)


@pytest.mark.asyncio
@respx.mock
async def test_google_custom_search_backend_normalizes_results() -> None:
    route = respx.get("https://customsearch.googleapis.com/customsearch/v1").mock(
        return_value=Response(
            200,
            json={
                "items": [
                    {
                        "title": "Interruptibility",
                        "link": "https://www.lesswrong.com/posts/abc/interruptibility",
                        "snippet": "A post about interruptibility.",
                        "displayLink": "www.lesswrong.com",
                    },
                    {
                        "title": "Corrigibility",
                        "link": "https://www.lesswrong.com/posts/def/corrigibility",
                        "snippet": "A post about corrigibility.",
                        "displayLink": "www.lesswrong.com",
                    },
                ],
                "searchInformation": {
                    "totalResults": "42",
                    "searchTime": 0.31,
                },
                "queries": {"nextPage": [{"startIndex": 3}]},
            },
        )
    )
    backend = GoogleCustomSearchBackend(api_key="key-123", engine_id="engine-456")

    result = await backend.search(
        "interruptibility",
        num=2,
        site_search="www.lesswrong.com",
        safe="active",
    )

    assert route.called
    params = route.calls[0].request.url.params
    assert params["q"] == "interruptibility"
    assert params["cx"] == "engine-456"
    assert params["siteSearch"] == "www.lesswrong.com"
    assert params["safe"] == "active"
    assert result.backend == "google_custom_search"
    assert result.result_count == 2
    assert result.total_results == 42
    assert result.next_start == 3
    assert result.results[0].title == "Interruptibility"
    assert result.results[0].rank == 1


@pytest.mark.asyncio
async def test_search_tool_rejects_unconfigured_backend(tmp_path) -> None:
    tool = SearchTool(
        GoogleCustomSearchBackend(api_key="", engine_id=""),
        quota=PersistentTokenBucket(tmp_path / "quota.json", capacity=100, refill_rate_per_hour=4),
    )

    result = await tool.execute(action="query", query="interruptibility")

    assert result.success is False
    assert "not configured" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_persistent_token_bucket_refills_from_past_state(tmp_path) -> None:
    state_path = tmp_path / "quota.json"
    past = datetime.now(UTC) - timedelta(hours=2)
    state_path.write_text(
        json.dumps({"tokens": 0.0, "updated_at": past.isoformat()}),
        encoding="utf-8",
    )
    bucket = PersistentTokenBucket(state_path, capacity=100, refill_rate_per_hour=4)

    status = await bucket.status()

    assert 7.5 <= status["remaining_tokens"] <= 8.5
    assert status["capacity"] == 100
    assert status["refill_rate_per_hour"] == 4


@pytest.mark.asyncio
@respx.mock
async def test_search_tool_reports_quota_exhaustion(tmp_path) -> None:
    respx.get("https://customsearch.googleapis.com/customsearch/v1").mock(
        return_value=Response(
            200,
            json={
                "items": [],
                "searchInformation": {"totalResults": "0", "searchTime": 0.2},
            },
        )
    )
    quota = PersistentTokenBucket(tmp_path / "quota.json", capacity=1, refill_rate_per_hour=0)
    tool = SearchTool(
        GoogleCustomSearchBackend(api_key="key", engine_id="engine"),
        quota=quota,
    )

    first = await tool.execute(action="query", query="first query")
    second = await tool.execute(action="query", query="second query")

    assert first.success is True
    assert second.success is False
    assert "quota" in (second.error or "").lower()
