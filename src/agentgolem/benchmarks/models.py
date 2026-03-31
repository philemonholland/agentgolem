"""Pydantic models for benchmark suites and reports."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from agentgolem.memory.models import EdgeType, NodeStatus, NodeType, SourceKind


class BenchmarkStatus(StrEnum):
    """Normalized status labels for benchmark dimensions and suites."""

    PASS = "pass"
    MIXED = "mixed"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class ErrorRecoveryScenario(StrEnum):
    """Scenario types for error-recovery benchmarking."""

    BROWSER_FETCH_RESULT = "browser_fetch_result"
    EMBEDDED_BROWSE_GUARD = "embedded_browse_guard"


class BenchmarkSourceSpec(BaseModel):
    """A source to seed into an offline benchmark store."""

    id: str
    kind: SourceKind
    origin: str
    reliability: float = 0.5
    independence_group: str = ""
    raw_reference: str = ""


class BenchmarkNodeSpec(BaseModel):
    """A node to seed into an offline benchmark store."""

    id: str
    text: str
    type: NodeType
    search_text: str = ""
    base_usefulness: float = 0.5
    trustworthiness: float = 0.5
    salience: float = 0.5
    emotion_label: str = "neutral"
    emotion_score: float = 0.0
    centrality: float = 0.0
    status: NodeStatus = NodeStatus.ACTIVE
    canonical: bool = False
    source_ids: list[str] = Field(default_factory=list)


class BenchmarkEdgeSpec(BaseModel):
    """An edge to seed into an offline benchmark store."""

    source_id: str
    target_id: str
    edge_type: EdgeType
    weight: float = 1.0


class MetricSummary(BaseModel):
    """Point estimate with an optional confidence interval."""

    value: float
    ci_lower: float | None = None
    ci_upper: float | None = None
    confidence_level: float | None = None


class RetrievalBenchmarkCase(BaseModel):
    """A query with a labeled relevant set."""

    id: str
    query: str
    relevant_node_ids: list[str]
    top_k: int = 5
    tags: list[str] = Field(default_factory=list)


class TrustCalibrationCase(BaseModel):
    """A node with a binary reliability label."""

    id: str
    node_id: str
    expected_reliable: bool
    tags: list[str] = Field(default_factory=list)


class ErrorRecoveryBenchmarkCase(BaseModel):
    """A deterministic recovery scenario with an expected outcome."""

    id: str
    scenario: ErrorRecoveryScenario
    url: str
    expected_success: bool
    status_code: int | None = None
    html: str = ""
    fetch_error: str = ""
    known_urls: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class BenchmarkSuite(BaseModel):
    """A complete offline benchmark suite."""

    name: str
    description: str = ""
    sources: list[BenchmarkSourceSpec] = Field(default_factory=list)
    nodes: list[BenchmarkNodeSpec] = Field(default_factory=list)
    edges: list[BenchmarkEdgeSpec] = Field(default_factory=list)
    retrieval_cases: list[RetrievalBenchmarkCase] = Field(default_factory=list)
    trust_cases: list[TrustCalibrationCase] = Field(default_factory=list)
    error_recovery_cases: list[ErrorRecoveryBenchmarkCase] = Field(default_factory=list)
    bootstrap_resamples: int = 1000
    bootstrap_seed: int = 0
    confidence_level: float = 0.95


class RetrievalAggregateMetrics(BaseModel):
    """Aggregate retrieval metrics across all retrieval cases."""

    mean_reciprocal_rank: MetricSummary
    mean_precision_at_k: MetricSummary
    mean_ndcg_at_k: MetricSummary


class RetrievalCaseResult(BaseModel):
    """Per-case retrieval metrics for actual vs. baseline ranking."""

    case_id: str
    query: str
    top_k: int
    relevant_node_ids: list[str]
    tags: list[str]
    retrieved_node_ids: list[str]
    baseline_retrieved_node_ids: list[str]
    reciprocal_rank: float
    baseline_reciprocal_rank: float
    precision_at_k: float
    baseline_precision_at_k: float
    ndcg_at_k: float
    baseline_ndcg_at_k: float


class RetrievalBenchmarkReport(BaseModel):
    """Retrieval benchmark summary."""

    case_count: int
    baseline_name: str
    actual: RetrievalAggregateMetrics
    baseline: RetrievalAggregateMetrics
    delta: RetrievalAggregateMetrics
    cases: list[RetrievalCaseResult]


class TrustAggregateMetrics(BaseModel):
    """Aggregate trust calibration metrics."""

    brier_score: MetricSummary
    expected_calibration_error: MetricSummary
    average_prediction: MetricSummary
    observed_reliable_rate: MetricSummary


class TrustCaseResult(BaseModel):
    """Per-case trust calibration outputs."""

    case_id: str
    node_id: str
    tags: list[str]
    prediction: float
    baseline_prediction: float
    expected_reliable: bool


class TrustBenchmarkReport(BaseModel):
    """Trust calibration benchmark summary."""

    case_count: int
    baseline_name: str
    actual: TrustAggregateMetrics
    baseline: TrustAggregateMetrics
    delta: TrustAggregateMetrics
    cases: list[TrustCaseResult]


class ErrorRecoveryAggregateMetrics(BaseModel):
    """Aggregate error-recovery metrics."""

    accuracy: MetricSummary
    expected_failure_handling_rate: MetricSummary
    expected_recovery_rate: MetricSummary


class ErrorRecoveryCaseResult(BaseModel):
    """Per-case error-recovery outcomes."""

    case_id: str
    scenario: ErrorRecoveryScenario
    url: str
    tags: list[str]
    expected_success: bool
    actual_success: bool
    baseline_success: bool
    matched_expectation: bool
    baseline_matched_expectation: bool


class ErrorRecoveryBenchmarkReport(BaseModel):
    """Error recovery benchmark summary."""

    case_count: int
    baseline_name: str
    actual: ErrorRecoveryAggregateMetrics
    baseline: ErrorRecoveryAggregateMetrics
    delta: ErrorRecoveryAggregateMetrics
    cases: list[ErrorRecoveryCaseResult]


class BenchmarkReport(BaseModel):
    """Full benchmark report for a single suite."""

    run_label: str = ""
    suite_name: str
    description: str = ""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    retrieval: RetrievalBenchmarkReport | None = None
    trust: TrustBenchmarkReport | None = None
    error_recovery: ErrorRecoveryBenchmarkReport | None = None
    retrieval_status: BenchmarkStatus = BenchmarkStatus.NOT_APPLICABLE
    trust_status: BenchmarkStatus = BenchmarkStatus.NOT_APPLICABLE
    error_recovery_status: BenchmarkStatus = BenchmarkStatus.NOT_APPLICABLE
    overall_status: BenchmarkStatus = BenchmarkStatus.NOT_APPLICABLE


class BenchmarkRunReport(BaseModel):
    """Report for a run that executed multiple suites."""

    run_label: str = ""
    target: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    suite_count: int
    passed_suite_count: int
    mixed_suite_count: int
    failed_suite_count: int
    suite_reports: list[BenchmarkReport]
