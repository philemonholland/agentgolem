"""Web browsing tool with rate limiting, text extraction, and audit logging."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from agentgolem.tools.base import Tool, ToolArgument, ToolResult

if TYPE_CHECKING:
    from agentgolem.logging.audit import AuditLogger


@dataclass
class WebPage:
    """Represents a fetched web page."""

    url: str
    status_code: int
    content: str  # raw HTML
    headers: dict[str, str]
    fetched_at: datetime


class WebBrowser:
    """Async web browser with rate limiting and content extraction."""

    def __init__(
        self,
        rate_limit_per_minute: int = 10,
        timeout_seconds: int = 30,
        user_agent: str = "AgentGolem/1.0 (respectful-crawler)",
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.rate_limit_per_minute = rate_limit_per_minute
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.audit_logger = audit_logger
        self._domain_last_request: dict[str, float] = {}
        self._min_interval = 60.0 / rate_limit_per_minute

    async def _check_rate_limit(self, domain: str) -> None:
        """Sleep if needed to respect per-domain rate limit."""
        import asyncio

        now = time.monotonic()
        last = self._domain_last_request.get(domain)
        if last is not None:
            elapsed = now - last
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
        self._domain_last_request[domain] = time.monotonic()

    async def fetch(self, url: str) -> WebPage:
        """Fetch a URL and return a WebPage."""
        domain = urlparse(url).netloc
        await self._check_rate_limit(domain)

        start = time.monotonic()
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers={"User-Agent": self.user_agent})
        duration_ms = (time.monotonic() - start) * 1000

        page = WebPage(
            url=str(response.url),
            status_code=response.status_code,
            content=response.text,
            headers=dict(response.headers),
            fetched_at=datetime.now(UTC),
        )

        if self.audit_logger is not None:
            self.audit_logger.log(
                mutation_type="web_fetch",
                target_id=url,
                evidence={
                    "status_code": response.status_code,
                    "content_length": len(response.text),
                    "duration_ms": round(duration_ms, 1),
                },
            )

        return page

    def extract_text(self, page: WebPage) -> str:
        """Extract readable text from a WebPage, stripping boilerplate elements.

        Falls back to full-page text if stripping yields very little content.
        """
        soup = BeautifulSoup(page.content, "html.parser")

        # Try aggressive stripping first
        stripped_soup = BeautifulSoup(page.content, "html.parser")
        for tag in stripped_soup.find_all(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        stripped = re.sub(r"\s+", " ", stripped_soup.get_text(separator=" ")).strip()

        if len(stripped) >= 100:
            return stripped

        # Fall back to full text (only remove script/style)
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()
        full = re.sub(r"\s+", " ", soup.get_text(separator=" ")).strip()
        return full

    def extract_links(self, page: WebPage) -> list[str]:
        """Extract all absolute URLs from anchor tags in a WebPage."""
        soup = BeautifulSoup(page.content, "html.parser")
        links: list[str] = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            absolute = urljoin(page.url, href)
            links.append(absolute)
        return links


class BrowserTool(Tool):
    """Tool wrapper around :class:`WebBrowser` for capability-driven exploration."""

    name = "browser"
    description = "Fetch readable web content from a URL"
    domains = ("web", "research")
    safety_class = "external_read"
    side_effect_class = "network_read"
    supports_dry_run = True
    supported_actions = ("fetch_text",)
    action_descriptions = {
        "fetch_text": "Fetch a web page, extract readable text, and return links",
    }
    action_arguments = {
        "fetch_text": (
            ToolArgument("url", "Absolute URL to fetch"),
            ToolArgument(
                "max_chars",
                "Optional maximum number of extracted text characters to keep",
                kind="int",
                required=False,
            ),
        ),
    }
    usage_hint = "browser.fetch_text(url=https://..., max_chars=4000)"

    def __init__(self, browser: WebBrowser) -> None:
        self._browser = browser

    async def execute(self, action: str = "fetch_text", **kwargs: Any) -> ToolResult:
        """Fetch a web page and return extracted text plus metadata."""
        if action != "fetch_text":
            return ToolResult(success=False, error=f"Unknown action: {action}")

        url = str(kwargs.get("url", "")).strip()
        if not url.startswith("http"):
            return ToolResult(success=False, error="BrowserTool requires an absolute http(s) URL")

        max_chars_raw = kwargs.get("max_chars", 4000)
        max_chars = int(max_chars_raw) if max_chars_raw not in (None, "") else 4000

        try:
            page = await self._browser.fetch(url)
            text = self._browser.extract_text(page)
            links = self._browser.extract_links(page)
        except Exception as exc:
            return ToolResult(success=False, error=f"Browser fetch failed: {exc}")

        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars] + "\n[…truncated]"

        return ToolResult(
            success=True,
            data={
                "url": page.url,
                "status_code": page.status_code,
                "text": text,
                "links": links[:50],
                "fetched_at": page.fetched_at.isoformat(),
            },
        )
