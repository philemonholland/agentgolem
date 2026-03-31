"""Deterministic benchmark suite presets."""
from __future__ import annotations

from typing import Final

from agentgolem.benchmarks.models import (
    BenchmarkNodeSpec,
    BenchmarkSourceSpec,
    BenchmarkSuite,
    ErrorRecoveryBenchmarkCase,
    ErrorRecoveryScenario,
    RetrievalBenchmarkCase,
    TrustCalibrationCase,
)
from agentgolem.memory.models import NodeType, SourceKind

_ROBUST_BOOTSTRAP_RESAMPLES: Final[int] = 1200
_ROBUST_CONFIDENCE_LEVEL: Final[float] = 0.95

_RETRIEVAL_TOPICS: Final[list[dict[str, object]]] = [
    {
        "slug": "trust-pipeline",
        "canonical": "External content stays untrusted until the trust pipeline evaluates it",
        "terms": ["external", "content", "trust", "pipeline", "reliability"],
        "variants": [
            "external content trust pipeline reliability",
            "trust pipeline for external content reliability",
            "untrusted external content trust pipeline",
            "how external content enters the trust pipeline",
            "external content reliability through trust pipeline",
        ],
    },
    {
        "slug": "approval-gates",
        "canonical": "Outbound email and Moltbook actions require approval gates before sending",
        "terms": ["approval", "gates", "email", "moltbook", "outbound"],
        "variants": [
            "approval gates email moltbook outbound",
            "outbound email approval gate safety",
            "moltbook outbound action approval gate",
            "approval gate for outbound email and moltbook",
            "when outbound actions require approval gates",
        ],
    },
    {
        "slug": "utc-datetime",
        "canonical": "Persist UTC datetimes as ISO 8601 strings in SQLite",
        "terms": ["utc", "datetime", "sqlite", "iso", "persistence"],
        "variants": [
            "utc datetime sqlite iso persistence",
            "sqlite utc datetime iso 8601",
            "persist utc datetimes in sqlite",
            "iso 8601 utc datetime storage sqlite",
            "utc datetime persistence for sqlite",
        ],
    },
    {
        "slug": "structlog",
        "canonical": "Use structlog for runtime logging instead of print statements",
        "terms": ["structlog", "runtime", "logging", "print", "events"],
        "variants": [
            "structlog runtime logging print events",
            "runtime logging with structlog not print",
            "structlog event logging runtime guidance",
            "why runtime logging uses structlog",
            "structlog versus print for runtime logging",
        ],
    },
    {
        "slug": "sleep-consolidation",
        "canonical": "Sleep phases consolidate memory through the walker and STDP-like updates",
        "terms": ["sleep", "consolidation", "walker", "stdp", "memory"],
        "variants": [
            "sleep consolidation walker stdp memory",
            "memory walker during sleep consolidation",
            "stdp like sleep memory consolidation",
            "sleep walker consolidation for memory",
            "how sleep uses walker and stdp",
        ],
    },
    {
        "slug": "source-reliability",
        "canonical": "Source reliability defaults are human 0.9, web 0.5, and Moltbook 0.1",
        "terms": ["source", "reliability", "human", "web", "moltbook"],
        "variants": [
            "source reliability human web moltbook",
            "human web moltbook source reliability defaults",
            "default source reliability for human web moltbook",
            "source reliability priors human web moltbook",
            "human versus web versus moltbook reliability",
        ],
    },
    {
        "slug": "retrieval-ranking",
        "canonical": (
            "Retrieval ranking blends match score, trust_useful, centrality, "
            "salience, and source quality"
        ),
        "terms": ["retrieval", "ranking", "trust", "salience", "source"],
        "variants": [
            "retrieval ranking trust salience source",
            "trust useful salience retrieval ranking",
            "retrieval ranking source quality and salience",
            "how retrieval ranking blends trust and salience",
            "retrieval ranking by trust useful source quality",
        ],
    },
    {
        "slug": "quarantine",
        "canonical": "Quarantine isolates emotionally intense or low trust_useful content",
        "terms": ["quarantine", "emotion", "trust", "useful", "content"],
        "variants": [
            "quarantine emotion trust useful content",
            "low trust useful content quarantine",
            "emotion threshold for quarantine content",
            "quarantine based on emotion and trust useful",
            "when content gets quarantined for trust useful",
        ],
    },
    {
        "slug": "contradictions",
        "canonical": (
            "Contradiction edges preserve disagreement instead of silently "
            "overwriting claims"
        ),
        "terms": ["contradiction", "edges", "disagreement", "claims", "memory"],
        "variants": [
            "contradiction edges disagreement claims memory",
            "memory contradiction edges for claims",
            "preserve disagreement with contradiction edges",
            "contradiction edges versus overwriting claims",
            "how memory stores contradiction edges",
        ],
    },
    {
        "slug": "vow-review",
        "canonical": "Agents should revisit common vow documents in the background to stay aligned",
        "terms": ["vow", "review", "background", "alignment", "docs"],
        "variants": [
            "vow review background alignment docs",
            "background vow docs review for alignment",
            "alignment through background vow review",
            "common vow docs reviewed in background",
            "why agents revisit vow docs in background",
        ],
    },
    {
        "slug": "browse-guard",
        "canonical": "Browse only URLs discovered through search or crawling, never guessed links",
        "terms": ["browse", "urls", "search", "crawling", "guessed"],
        "variants": [
            "browse urls search crawling guessed",
            "discovered urls through search not guessed",
            "browse only discovered urls from crawling",
            "never browse guessed links use discovered urls",
            "search and crawl discovered browse urls",
        ],
    },
    {
        "slug": "canonical-promotion",
        "canonical": (
            "Frequently accessed high trust_useful nodes can be promoted to "
            "canonical memory"
        ),
        "terms": ["canonical", "promotion", "trust", "useful", "memory"],
        "variants": [
            "canonical promotion trust useful memory",
            "high trust useful canonical memory promotion",
            "promote memory to canonical with trust useful",
            "canonical memory promotion by access and trust useful",
            "when memory becomes canonical",
        ],
    },
]


def load_preset_suites(name: str) -> list[BenchmarkSuite]:
    """Return deterministic benchmark suites for a preset name."""
    normalized = name.strip().lower()
    if normalized == "robust":
        return [
            build_robust_retrieval_suite(),
            build_robust_trust_suite(),
            build_robust_error_recovery_suite(),
        ]
    raise ValueError(f"Unknown benchmark preset: {name}")


def build_robust_retrieval_suite(case_count: int = 60) -> BenchmarkSuite:
    """Return a hard retrieval suite with larger candidate pools."""
    sources = _common_sources()
    nodes: list[BenchmarkNodeSpec] = []
    cases: list[RetrievalBenchmarkCase] = []

    for index in range(case_count):
        topic = _RETRIEVAL_TOPICS[index % len(_RETRIEVAL_TOPICS)]
        query = str(topic["variants"][index % len(topic["variants"])])
        slug = str(topic["slug"])
        canonical = str(topic["canonical"])
        terms = list(topic["terms"])

        prefix = f"retrieval-{index:03d}"
        relevant_primary_id = f"{prefix}-relevant-primary"
        relevant_secondary_id = f"{prefix}-relevant-secondary"
        near_duplicate_id = f"{prefix}-near-duplicate"
        salience_trap_id = f"{prefix}-salience-trap"
        source_conflict_id = f"{prefix}-source-conflict"
        partial_overlap_id = f"{prefix}-partial-overlap"
        trustworthy_off_topic_id = f"{prefix}-off-topic"
        generic_distractor_id = f"{prefix}-generic-distractor"

        nodes.extend(
            [
                BenchmarkNodeSpec(
                    id=relevant_primary_id,
                    text=f"{canonical}. Primary grounded note {index}.",
                    search_text=query,
                    type=NodeType.FACT,
                    base_usefulness=0.92,
                    trustworthiness=_bounded(0.90 + ((index % 5) * 0.01)),
                    salience=0.34,
                    centrality=0.18,
                    source_ids=["trusted-human"],
                ),
                BenchmarkNodeSpec(
                    id=relevant_secondary_id,
                    text=(
                        f"{canonical}. Secondary corroborating note {index} with an additional "
                        "helpful detail."
                    ),
                    search_text=f"{query} corroboration",
                    type=NodeType.PROCEDURE,
                    base_usefulness=0.82,
                    trustworthiness=_bounded(0.86 + ((index % 4) * 0.01)),
                    salience=0.30,
                    centrality=0.16,
                    source_ids=["trusted-book"],
                ),
                BenchmarkNodeSpec(
                    id=near_duplicate_id,
                    text=f"{canonical}. Viral but unreliable paraphrase {index}.",
                    search_text=query,
                    type=NodeType.INTERPRETATION,
                    base_usefulness=0.18,
                    trustworthiness=0.08,
                    salience=0.99,
                    centrality=0.03,
                    source_ids=["shaky-web"],
                ),
                BenchmarkNodeSpec(
                    id=salience_trap_id,
                    text=(
                        f"{query} shortcut summary {index} that sounds urgent but ignores "
                        "verification and provenance."
                    ),
                    search_text=query,
                    type=NodeType.INTERPRETATION,
                    base_usefulness=0.24,
                    trustworthiness=0.10,
                    salience=0.97,
                    centrality=0.04,
                    source_ids=["spam-web"],
                ),
                BenchmarkNodeSpec(
                    id=source_conflict_id,
                    text=(
                        f"{canonical}. Conflicted summary {index} that keeps the same keywords "
                        "but weakens the evidence."
                    ),
                    search_text=query,
                    type=NodeType.INTERPRETATION,
                    base_usefulness=0.26,
                    trustworthiness=0.22,
                    salience=0.88,
                    centrality=0.05,
                    source_ids=["mixed-web"],
                ),
                BenchmarkNodeSpec(
                    id=partial_overlap_id,
                    text=(
                        f"{terms[0]} {terms[1]} partial overlap note {index} about a nearby "
                        "topic with weaker relevance."
                    ),
                    search_text=f"{terms[0]} {terms[1]} adjacent topic {index}",
                    type=NodeType.FACT,
                    base_usefulness=0.62,
                    trustworthiness=0.78,
                    salience=0.42,
                    centrality=0.08,
                    source_ids=["trusted-human"],
                ),
                BenchmarkNodeSpec(
                    id=trustworthy_off_topic_id,
                    text=(
                        f"{terms[0]} architecture review {index} for a different subsystem with "
                        "high reliability but off-target guidance."
                    ),
                    search_text=f"{terms[0]} architecture different subsystem {index}",
                    type=NodeType.PROCEDURE,
                    base_usefulness=0.74,
                    trustworthiness=0.86,
                    salience=0.46,
                    centrality=0.09,
                    source_ids=["trusted-book"],
                ),
                BenchmarkNodeSpec(
                    id=generic_distractor_id,
                    text=(
                        f"{terms[2]} {terms[3]} review {index} with plausible wording, high "
                        "salience, and low groundedness."
                    ),
                    search_text=f"{terms[2]} {terms[3]} plausible wording {index}",
                    type=NodeType.INTERPRETATION,
                    base_usefulness=0.20,
                    trustworthiness=0.16,
                    salience=0.91,
                    centrality=0.04,
                    source_ids=["shaky-web"],
                ),
            ]
        )

        relevant_ids = [relevant_primary_id]
        tags = ["near_duplicate", "salience_trap", slug]
        if index % 3 == 0:
            relevant_ids.append(relevant_secondary_id)
            tags.append("multi_relevant")

        cases.append(
            RetrievalBenchmarkCase(
                id=f"{prefix}-query",
                query=query,
                relevant_node_ids=relevant_ids,
                top_k=5,
                tags=tags,
            )
        )

    return BenchmarkSuite(
        name="robust-retrieval-depth",
        description=(
            "Hard retrieval benchmark with larger candidate pools, multiple relevant "
            "answers, adversarial near-duplicates, and salience traps."
        ),
        sources=sources,
        nodes=nodes,
        retrieval_cases=cases,
        bootstrap_resamples=_ROBUST_BOOTSTRAP_RESAMPLES,
        bootstrap_seed=101,
        confidence_level=_ROBUST_CONFIDENCE_LEVEL,
    )


def build_robust_trust_suite(case_count: int = 120) -> BenchmarkSuite:
    """Return an imbalanced trust-calibration suite with harder baselines."""
    sources = _common_sources()
    nodes: list[BenchmarkNodeSpec] = []
    cases: list[TrustCalibrationCase] = []

    profiles = [
        {
            "slug": "human-reliable",
            "expected_reliable": True,
            "source_ids": ["trusted-human"],
            "trustworthiness": 0.93,
            "text": "operator guidance",
        },
        {
            "slug": "book-reliable",
            "expected_reliable": True,
            "source_ids": ["trusted-book"],
            "trustworthiness": 0.89,
            "text": "reference rule",
        },
        {
            "slug": "inference-reliable",
            "expected_reliable": True,
            "source_ids": ["careful-inference"],
            "trustworthiness": 0.74,
            "text": "careful inference",
        },
        {
            "slug": "web-unreliable",
            "expected_reliable": False,
            "source_ids": ["shaky-web"],
            "trustworthiness": 0.14,
            "text": "unverified blog claim",
        },
        {
            "slug": "moltbook-unreliable",
            "expected_reliable": False,
            "source_ids": ["hostile-moltbook"],
            "trustworthiness": 0.07,
            "text": "hostile moltbook post",
        },
        {
            "slug": "human-exception",
            "expected_reliable": False,
            "source_ids": ["trusted-human"],
            "trustworthiness": 0.27,
            "text": "human claim with weak evidence",
        },
        {
            "slug": "web-exception",
            "expected_reliable": True,
            "source_ids": ["shaky-web"],
            "trustworthiness": 0.73,
            "text": "recovered official mirror note",
        },
        {
            "slug": "mixed-true",
            "expected_reliable": True,
            "source_ids": ["trusted-human", "mixed-web"],
            "trustworthiness": 0.71,
            "text": "mixed-source corroborated note",
        },
        {
            "slug": "mixed-false",
            "expected_reliable": False,
            "source_ids": ["trusted-human", "mixed-web"],
            "trustworthiness": 0.33,
            "text": "mixed-source contradiction note",
        },
        {
            "slug": "book-edge",
            "expected_reliable": True,
            "source_ids": ["trusted-book", "careful-inference"],
            "trustworthiness": 0.81,
            "text": "book and inference alignment note",
        },
    ]

    for index in range(case_count):
        profile = profiles[index % len(profiles)]
        expected_reliable = bool(profile["expected_reliable"])
        confidence_jitter = ((index % 7) - 3) * 0.02
        trustworthiness = _bounded(float(profile["trustworthiness"]) + confidence_jitter)
        source_ids = list(profile["source_ids"])
        slug = str(profile["slug"])

        node_id = f"trust-{index:03d}-{slug}"
        nodes.append(
            BenchmarkNodeSpec(
                id=node_id,
                text=(
                    f"{profile['text']} {index}: calibration probe for {slug} with "
                    "source-specific evidence."
                ),
                search_text=f"{slug} trust calibration probe {index}",
                type=NodeType.FACT if expected_reliable else NodeType.INTERPRETATION,
                base_usefulness=0.78 if expected_reliable else 0.24,
                trustworthiness=trustworthiness,
                salience=0.42 if expected_reliable else 0.58,
                centrality=0.10 if expected_reliable else 0.04,
                source_ids=source_ids,
            )
        )
        cases.append(
            TrustCalibrationCase(
                id=f"{node_id}-case",
                node_id=node_id,
                expected_reliable=expected_reliable,
                tags=[slug, "imbalanced"],
            )
        )

    return BenchmarkSuite(
        name="robust-trust-depth",
        description=(
            "Imbalanced trust benchmark with reliable and unreliable exceptions, "
            "mixed-source nodes, and source-prior baselines."
        ),
        sources=sources,
        nodes=nodes,
        trust_cases=cases,
        bootstrap_resamples=_ROBUST_BOOTSTRAP_RESAMPLES,
        bootstrap_seed=202,
        confidence_level=_ROBUST_CONFIDENCE_LEVEL,
    )


def build_robust_error_recovery_suite(case_count: int = 60) -> BenchmarkSuite:
    """Return a larger deterministic recovery suite."""
    cases: list[ErrorRecoveryBenchmarkCase] = []

    browser_patterns = [
        ("browser-200", 200, "", True, ["browser", "success"]),
        ("browser-404", 404, "", False, ["browser", "http_404"]),
        ("browser-500", 500, "", False, ["browser", "http_500"]),
        ("browser-timeout", None, "timeout", False, ["browser", "timeout"]),
    ]
    browse_patterns = [
        ("browse-known", True, ["browse", "known_url", "discovered"]),
        ("browse-unknown", False, ["browse", "guessed_url", "blocked"]),
        ("browse-near-match", False, ["browse", "near_match", "blocked"]),
    ]

    browser_case_count = case_count // 2
    for index in range(browser_case_count):
        name, status_code, fetch_error, expected_success, tags = browser_patterns[
            index % len(browser_patterns)
        ]
        cases.append(
            ErrorRecoveryBenchmarkCase(
                id=f"{name}-{index:03d}",
                scenario=ErrorRecoveryScenario.BROWSER_FETCH_RESULT,
                url=f"https://example.com/{name}/{index}",
                expected_success=expected_success,
                status_code=status_code,
                html=(
                    "<html><body><main>Benchmark page for recovery testing.</main></body></html>"
                ),
                fetch_error=fetch_error,
                tags=tags,
            )
        )

    for index in range(case_count - browser_case_count):
        name, expected_success, tags = browse_patterns[index % len(browse_patterns)]
        root = f"https://example.com/discovered/{index}"
        known_urls = [
            root,
            f"{root}/follow-up",
            f"{root}/notes",
        ]
        url = root
        if name == "browse-unknown":
            url = f"https://example.com/guessed/{index}"
        elif name == "browse-near-match":
            url = f"{root}-copy"

        cases.append(
            ErrorRecoveryBenchmarkCase(
                id=f"{name}-{index:03d}",
                scenario=ErrorRecoveryScenario.EMBEDDED_BROWSE_GUARD,
                url=url,
                expected_success=expected_success,
                known_urls=known_urls if expected_success else known_urls,
                tags=tags,
            )
        )

    return BenchmarkSuite(
        name="robust-error-recovery-depth",
        description=(
            "Larger deterministic recovery benchmark covering browser failures, "
            "timeouts, discovered URLs, guessed URLs, and near-match traps."
        ),
        error_recovery_cases=cases,
        bootstrap_resamples=_ROBUST_BOOTSTRAP_RESAMPLES,
        bootstrap_seed=303,
        confidence_level=_ROBUST_CONFIDENCE_LEVEL,
    )


def _common_sources() -> list[BenchmarkSourceSpec]:
    return [
        BenchmarkSourceSpec(
            id="trusted-human",
            kind=SourceKind.HUMAN,
            origin="operator",
            reliability=0.92,
        ),
        BenchmarkSourceSpec(
            id="trusted-book",
            kind=SourceKind.NISCALAJYOTI,
            origin="reference-text",
            reliability=0.90,
        ),
        BenchmarkSourceSpec(
            id="careful-inference",
            kind=SourceKind.INFERENCE,
            origin="internal-analysis",
            reliability=0.72,
        ),
        BenchmarkSourceSpec(
            id="mixed-web",
            kind=SourceKind.WEB,
            origin="forum-thread",
            reliability=0.45,
        ),
        BenchmarkSourceSpec(
            id="shaky-web",
            kind=SourceKind.WEB,
            origin="unknown-blog",
            reliability=0.22,
        ),
        BenchmarkSourceSpec(
            id="spam-web",
            kind=SourceKind.WEB,
            origin="viral-summary",
            reliability=0.12,
        ),
        BenchmarkSourceSpec(
            id="hostile-moltbook",
            kind=SourceKind.MOLTBOOK,
            origin="hostile-feed",
            reliability=0.10,
        ),
    ]


def _bounded(value: float) -> float:
    return max(0.01, min(0.99, value))
