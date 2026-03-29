"""Tests for the web browsing tool."""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
import respx

from agentgolem.logging.audit import AuditLogger
from agentgolem.tools.browser import WebBrowser, WebPage


SIMPLE_HTML = "<html><body><p>Hello World</p></body></html>"


@respx.mock
async def test_fetch_success():
    """Fetch a 200 page and verify all WebPage fields."""
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(200, text=SIMPLE_HTML)
    )
    browser = WebBrowser()
    page = await browser.fetch("https://example.com/page")

    assert page.status_code == 200
    assert page.url == "https://example.com/page"
    assert "Hello World" in page.content
    assert page.fetched_at is not None
    assert isinstance(page.headers, dict)


@respx.mock
async def test_fetch_404():
    """Fetch a 404 and verify status_code is captured."""
    respx.get("https://example.com/missing").mock(
        return_value=httpx.Response(404, text="Not Found")
    )
    browser = WebBrowser()
    page = await browser.fetch("https://example.com/missing")

    assert page.status_code == 404
    assert "Not Found" in page.content


@respx.mock
async def test_fetch_timeout():
    """A timeout raises an httpx.TimeoutException."""
    respx.get("https://example.com/slow").mock(side_effect=httpx.TimeoutException("timed out"))
    browser = WebBrowser(timeout_seconds=1)

    with pytest.raises(httpx.TimeoutException):
        await browser.fetch("https://example.com/slow")


def test_extract_text_strips_scripts():
    """Script tags are removed from extracted text."""
    html = "<html><body><script>var x=1;</script><p>Visible text</p></body></html>"
    page = WebPage(
        url="https://example.com",
        status_code=200,
        content=html,
        headers={},
        fetched_at=None,  # type: ignore[arg-type]
    )
    browser = WebBrowser()
    text = browser.extract_text(page)

    assert "var x=1" not in text
    assert "Visible text" in text


def test_extract_text_strips_styles():
    """Style tags are removed from extracted text."""
    html = "<html><body><style>body{color:red;}</style><p>Styled text</p></body></html>"
    page = WebPage(
        url="https://example.com",
        status_code=200,
        content=html,
        headers={},
        fetched_at=None,  # type: ignore[arg-type]
    )
    browser = WebBrowser()
    text = browser.extract_text(page)

    assert "color:red" not in text
    assert "Styled text" in text


def test_extract_links_absolute():
    """Absolute links are extracted as-is."""
    html = (
        '<html><body>'
        '<a href="https://other.com/page1">One</a>'
        '<a href="https://other.com/page2">Two</a>'
        '</body></html>'
    )
    page = WebPage(
        url="https://example.com",
        status_code=200,
        content=html,
        headers={},
        fetched_at=None,  # type: ignore[arg-type]
    )
    browser = WebBrowser()
    links = browser.extract_links(page)

    assert "https://other.com/page1" in links
    assert "https://other.com/page2" in links


def test_extract_links_relative():
    """Relative links are resolved against the page URL."""
    html = (
        '<html><body>'
        '<a href="/about">About</a>'
        '<a href="sub/page">Sub</a>'
        '</body></html>'
    )
    page = WebPage(
        url="https://example.com/dir/index.html",
        status_code=200,
        content=html,
        headers={},
        fetched_at=None,  # type: ignore[arg-type]
    )
    browser = WebBrowser()
    links = browser.extract_links(page)

    assert "https://example.com/about" in links
    assert "https://example.com/dir/sub/page" in links


@respx.mock
async def test_rate_limiting():
    """Two rapid requests to the same domain update internal rate-limit state."""
    respx.get("https://example.com/a").mock(return_value=httpx.Response(200, text="A"))
    respx.get("https://example.com/b").mock(return_value=httpx.Response(200, text="B"))

    browser = WebBrowser(rate_limit_per_minute=1000)
    await browser.fetch("https://example.com/a")

    # After the first fetch, the domain timestamp should be recorded
    assert "example.com" in browser._domain_last_request
    first_ts = browser._domain_last_request["example.com"]

    await browser.fetch("https://example.com/b")
    second_ts = browser._domain_last_request["example.com"]

    # Second timestamp must be >= first (rate limiter updated it)
    assert second_ts >= first_ts


@respx.mock
async def test_audit_logged(tmp_path: Path):
    """Fetch logs an audit entry when an AuditLogger is provided."""
    respx.get("https://example.com/audited").mock(
        return_value=httpx.Response(200, text="<html>ok</html>")
    )
    logger = AuditLogger(tmp_path)
    browser = WebBrowser(audit_logger=logger)

    await browser.fetch("https://example.com/audited")

    entries = logger.read(limit=10)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["mutation_type"] == "web_fetch"
    assert entry["target_id"] == "https://example.com/audited"
    assert entry["evidence"]["status_code"] == 200
    assert "duration_ms" in entry["evidence"]
    assert "content_length" in entry["evidence"]
