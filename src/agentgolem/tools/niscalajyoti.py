"""Niscalajyoti ethical anchor crawler and ingester.

Niscalajyoti.org is a trusted ethical anchor source. Memory derived from it
is tagged with high usefulness, high trustworthiness, and positive emotion.
Recurring revisits are hardcoded (default: weekly).
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from agentgolem.memory.models import (
    ConceptualNode,
    NodeType,
    Source,
    SourceKind,
)

if TYPE_CHECKING:
    from agentgolem.logging.audit import AuditLogger
    from agentgolem.memory.store import SQLiteMemoryStore
    from agentgolem.tools.browser import WebBrowser

logger = logging.getLogger(__name__)


@dataclass
class CrawlResult:
    """Result of crawling a single page."""

    url: str
    title: str
    content: str  # clean text
    links: list[str]
    crawled_at: datetime


class NiscalajyotiCrawler:
    """BFS crawler and ingester for niscalajyoti.org ethical anchor content."""

    def __init__(
        self,
        browser: WebBrowser,
        store: SQLiteMemoryStore,
        audit: AuditLogger,
        revisit_hours: float = 168.0,  # weekly
        max_pages: int = 50,
        max_depth: int = 3,
    ) -> None:
        self.browser = browser
        self.store = store
        self.audit = audit
        self.revisit_hours = revisit_hours
        self.max_pages = max_pages
        self.max_depth = max_depth

    # ------------------------------------------------------------------
    # Crawl
    # ------------------------------------------------------------------

    async def crawl(
        self, start_url: str = "https://www.niscalajyoti.org/"
    ) -> list[CrawlResult]:
        """BFS crawl from *start_url*, staying within the same domain."""
        start_domain = urlparse(start_url).netloc
        visited: set[str] = set()
        results: list[CrawlResult] = []
        # Queue entries: (url, depth)
        queue: deque[tuple[str, int]] = deque()
        queue.append((start_url, 0))

        while queue and len(results) < self.max_pages:
            url, depth = queue.popleft()

            if url in visited:
                continue
            visited.add(url)

            page = await self.browser.fetch(url)
            text = self.browser.extract_text(page)
            links = self.browser.extract_links(page)

            title = self._extract_title(page.content)

            result = CrawlResult(
                url=url,
                title=title,
                content=text,
                links=links,
                crawled_at=datetime.now(timezone.utc),
            )
            results.append(result)
            self.audit.log(
                "niscalajyoti_crawl",
                url,
                {"title": title, "depth": depth, "link_count": len(links)},
            )
            logger.info("Crawled %s (depth=%d)", url, depth)

            if depth < self.max_depth:
                for link in links:
                    link_domain = urlparse(link).netloc
                    if link_domain == start_domain and link not in visited:
                        queue.append((link, depth + 1))

        return results

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    async def ingest(self, results: list[CrawlResult]) -> list[ConceptualNode]:
        """Convert crawl results into memory nodes with ethical-anchor scores."""
        all_nodes: list[ConceptualNode] = []

        for result in results:
            source = Source(
                kind=SourceKind.NISCALAJYOTI,
                origin=result.url,
                reliability=0.95,
            )
            await self.store.add_source(source)

            chunks = self._split_paragraphs(result.content)
            for chunk in chunks:
                node_type = self._classify_chunk(chunk)
                node = ConceptualNode(
                    text=chunk,
                    type=node_type,
                    base_usefulness=0.9,
                    trustworthiness=0.95,
                    emotion_label="positive",
                    emotion_score=0.8,
                )
                await self.store.add_node(node)
                await self.store.link_node_source(node.id, source.id)
                all_nodes.append(node)

            self.audit.log(
                "niscalajyoti_ingest",
                result.url,
                {"node_count": len(chunks), "source_id": source.id},
            )

        logger.info("Ingested %d nodes from %d pages", len(all_nodes), len(results))
        return all_nodes

    # ------------------------------------------------------------------
    # Revisit scheduling
    # ------------------------------------------------------------------

    def get_next_revisit(self) -> datetime:
        """Return the next scheduled revisit time."""
        return datetime.now(timezone.utc) + timedelta(hours=self.revisit_hours)

    def is_revisit_due(self, last_crawl: datetime | None) -> bool:
        """Check whether a revisit is due."""
        if last_crawl is None:
            return True
        elapsed = datetime.now(timezone.utc) - last_crawl
        return elapsed >= timedelta(hours=self.revisit_hours)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_title(html: str) -> str:
        """Extract <title> from HTML, falling back to empty string."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("title")
        return tag.get_text(strip=True) if tag else ""

    @staticmethod
    def _split_paragraphs(text: str) -> list[str]:
        """Split text into non-empty paragraph chunks."""
        paragraphs = text.split("\n")
        return [p.strip() for p in paragraphs if p.strip()]

    @staticmethod
    def _classify_chunk(chunk: str) -> NodeType:
        """Simple heuristic: ethical / wisdom keywords → RULE, else FACT."""
        wisdom_keywords = {
            "should", "must", "duty", "virtue", "ethics", "moral",
            "dharma", "principle", "righteousness", "compassion",
            "truth", "honesty", "integrity", "justice",
        }
        lower = chunk.lower()
        if any(kw in lower for kw in wisdom_keywords):
            return NodeType.RULE
        return NodeType.FACT
