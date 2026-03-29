"""Web browsing tool with rate limiting, text extraction, and audit logging."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

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
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, headers={"User-Agent": self.user_agent})
        duration_ms = (time.monotonic() - start) * 1000

        page = WebPage(
            url=url,
            status_code=response.status_code,
            content=response.text,
            headers=dict(response.headers),
            fetched_at=datetime.now(timezone.utc),
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
        """Extract readable text from a WebPage, stripping boilerplate elements."""
        soup = BeautifulSoup(page.content, "html.parser")
        for tag in soup.find_all(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
        return re.sub(r"\s+", " ", text).strip()

    def extract_links(self, page: WebPage) -> list[str]:
        """Extract all absolute URLs from anchor tags in a WebPage."""
        soup = BeautifulSoup(page.content, "html.parser")
        links: list[str] = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            absolute = urljoin(page.url, href)
            links.append(absolute)
        return links
