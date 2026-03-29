"""Tests for the Niscalajyoti ethical anchor crawler."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentgolem.logging.audit import AuditLogger
from agentgolem.memory.models import (
    ConceptualNode,
    NodeType,
    Source,
    SourceKind,
)
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.tools.browser import WebPage
from agentgolem.tools.niscalajyoti import CrawlResult, NiscalajyotiCrawler


# ------------------------------------------------------------------
# Mock browser
# ------------------------------------------------------------------


class MockBrowser:
    """Fake browser that serves pre-defined HTML pages."""

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages  # url -> html content

    async def fetch(self, url: str) -> WebPage:
        html = self._pages.get(url, "<html><body>Not found</body></html>")
        status = 200 if url in self._pages else 404
        return WebPage(
            url=url,
            status_code=status,
            content=html,
            headers={},
            fetched_at=datetime.now(timezone.utc),
        )

    def extract_text(self, page: WebPage) -> str:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(page.content, "html.parser")
        return soup.get_text(separator=" ", strip=True)

    def extract_links(self, page: WebPage) -> list[str]:
        from urllib.parse import urljoin

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(page.content, "html.parser")
        links: list[str] = []
        for a in soup.find_all("a", href=True):
            links.append(urljoin(page.url, a["href"]))
        return links


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

DOMAIN = "https://www.niscalajyoti.org"


@pytest.fixture
async def store(tmp_path):
    db = await init_db(tmp_path / "test.db")
    audit = AuditLogger(tmp_path)
    s = SQLiteMemoryStore(db, audit)
    yield s
    await close_db(db)


@pytest.fixture
def audit(tmp_path):
    return AuditLogger(tmp_path)


def _make_crawler(
    pages: dict[str, str],
    store: SQLiteMemoryStore,
    audit: AuditLogger,
    *,
    max_pages: int = 50,
    max_depth: int = 3,
    revisit_hours: float = 168.0,
) -> NiscalajyotiCrawler:
    browser = MockBrowser(pages)
    return NiscalajyotiCrawler(
        browser=browser,
        store=store,
        audit=audit,
        max_pages=max_pages,
        max_depth=max_depth,
        revisit_hours=revisit_hours,
    )


# ------------------------------------------------------------------
# Sample HTML pages
# ------------------------------------------------------------------

HOME_HTML = """<html>
<head><title>Niscalajyoti Home</title></head>
<body>
<h1>Welcome to Niscalajyoti</h1>
<p>Dharma and righteousness guide our path.</p>
<a href="/about">About</a>
<a href="/teachings">Teachings</a>
<a href="https://external.example.com/link">External</a>
</body>
</html>"""

ABOUT_HTML = """<html>
<head><title>About Niscalajyoti</title></head>
<body>
<h1>About</h1>
<p>We promote truth and compassion.</p>
<a href="/">Home</a>
<a href="/contact">Contact</a>
</body>
</html>"""

TEACHINGS_HTML = """<html>
<head><title>Teachings</title></head>
<body>
<h1>Teachings</h1>
<p>Virtue should be practised daily.</p>
<a href="/">Home</a>
<a href="/teachings/advanced">Advanced</a>
</body>
</html>"""

ADVANCED_HTML = """<html>
<head><title>Advanced Teachings</title></head>
<body>
<h1>Advanced</h1>
<p>Deep lessons on integrity and justice.</p>
</body>
</html>"""

CONTACT_HTML = """<html>
<head><title>Contact</title></head>
<body>
<h1>Contact Us</h1>
<p>Reach us for guidance.</p>
</body>
</html>"""

STANDARD_PAGES: dict[str, str] = {
    f"{DOMAIN}/": HOME_HTML,
    f"{DOMAIN}/about": ABOUT_HTML,
    f"{DOMAIN}/teachings": TEACHINGS_HTML,
    f"{DOMAIN}/teachings/advanced": ADVANCED_HTML,
    f"{DOMAIN}/contact": CONTACT_HTML,
}


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_crawl_fetches_start_page(store, audit):
    """Crawler fetches the start URL and returns at least one result."""
    crawler = _make_crawler(STANDARD_PAGES, store, audit)
    results = await crawler.crawl(f"{DOMAIN}/")
    assert len(results) >= 1
    assert results[0].url == f"{DOMAIN}/"
    assert "Niscalajyoti" in results[0].title


async def test_crawl_follows_internal_links(store, audit):
    """Crawler follows links within the same domain."""
    crawler = _make_crawler(STANDARD_PAGES, store, audit)
    results = await crawler.crawl(f"{DOMAIN}/")
    crawled_urls = {r.url for r in results}
    assert f"{DOMAIN}/about" in crawled_urls
    assert f"{DOMAIN}/teachings" in crawled_urls


async def test_crawl_respects_max_pages(store, audit):
    """Crawler stops after reaching max_pages."""
    crawler = _make_crawler(STANDARD_PAGES, store, audit, max_pages=2)
    results = await crawler.crawl(f"{DOMAIN}/")
    assert len(results) == 2


async def test_crawl_respects_max_depth(store, audit):
    """Crawler doesn't go deeper than max_depth."""
    crawler = _make_crawler(STANDARD_PAGES, store, audit, max_depth=1)
    results = await crawler.crawl(f"{DOMAIN}/")
    crawled_urls = {r.url for r in results}
    # depth 0: home, depth 1: about, teachings (but NOT advanced at depth 2)
    assert f"{DOMAIN}/teachings/advanced" not in crawled_urls


async def test_crawl_skips_external_links(store, audit):
    """Crawler does not follow links to external domains."""
    crawler = _make_crawler(STANDARD_PAGES, store, audit)
    results = await crawler.crawl(f"{DOMAIN}/")
    crawled_urls = {r.url for r in results}
    assert "https://external.example.com/link" not in crawled_urls


async def test_ingest_creates_nodes_with_correct_scores(store, audit):
    """Ingested nodes have the ethical-anchor score overrides."""
    crawler = _make_crawler(STANDARD_PAGES, store, audit)
    fake_results = [
        CrawlResult(
            url=f"{DOMAIN}/",
            title="Home",
            content="Dharma guides us.\nVirtue is paramount.",
            links=[],
            crawled_at=datetime.now(timezone.utc),
        )
    ]
    nodes = await crawler.ingest(fake_results)
    assert len(nodes) >= 1
    for node in nodes:
        assert node.base_usefulness == 0.9
        assert node.trustworthiness == 0.95
        assert node.emotion_label == "positive"
        assert node.emotion_score == 0.8


async def test_ingest_creates_niscalajyoti_source(store, audit):
    """Ingested content is linked to a NISCALAJYOTI source."""
    crawler = _make_crawler(STANDARD_PAGES, store, audit)
    fake_results = [
        CrawlResult(
            url=f"{DOMAIN}/about",
            title="About",
            content="Truth and compassion.",
            links=[],
            crawled_at=datetime.now(timezone.utc),
        )
    ]
    nodes = await crawler.ingest(fake_results)
    assert len(nodes) >= 1
    sources = await store.get_node_sources(nodes[0].id)
    assert len(sources) == 1
    assert sources[0].kind == SourceKind.NISCALAJYOTI
    assert sources[0].origin == f"{DOMAIN}/about"
    assert sources[0].reliability == 0.95


async def test_ingest_links_source_to_nodes(store, audit):
    """Every created node is linked to its source."""
    crawler = _make_crawler(STANDARD_PAGES, store, audit)
    fake_results = [
        CrawlResult(
            url=f"{DOMAIN}/teachings",
            title="Teachings",
            content="Virtue should be practised daily.\nHonesty matters.",
            links=[],
            crawled_at=datetime.now(timezone.utc),
        )
    ]
    nodes = await crawler.ingest(fake_results)
    assert len(nodes) >= 2
    for node in nodes:
        sources = await store.get_node_sources(node.id)
        assert len(sources) == 1
        assert sources[0].kind == SourceKind.NISCALAJYOTI


async def test_revisit_due_when_never_crawled(store, audit):
    """Revisit is due when last_crawl is None (never crawled)."""
    crawler = _make_crawler(STANDARD_PAGES, store, audit)
    assert crawler.is_revisit_due(None) is True


async def test_revisit_not_due_when_recent(store, audit):
    """Revisit is not due when crawled recently."""
    crawler = _make_crawler(STANDARD_PAGES, store, audit, revisit_hours=168.0)
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    assert crawler.is_revisit_due(recent) is False
