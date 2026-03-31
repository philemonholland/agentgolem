"""Offline benchmark runner for AgentGolem."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import structlog

from agentgolem.benchmarks.metrics import (
    BootstrapSummary,
    CalibrationPoint,
    bootstrap_mean,
    bootstrap_paired_statistic,
    bootstrap_statistic,
    brier_score,
    expected_calibration_error,
    mean,
    ndcg_at_k,
    paired_deltas,
    precision_at_k,
    reciprocal_rank,
)
from agentgolem.benchmarks.models import (
    BenchmarkReport,
    BenchmarkRunReport,
    BenchmarkStatus,
    BenchmarkSuite,
    ErrorRecoveryAggregateMetrics,
    ErrorRecoveryBenchmarkCase,
    ErrorRecoveryBenchmarkReport,
    ErrorRecoveryCaseResult,
    ErrorRecoveryScenario,
    MetricSummary,
    RetrievalAggregateMetrics,
    RetrievalBenchmarkReport,
    RetrievalCaseResult,
    TrustAggregateMetrics,
    TrustBenchmarkReport,
    TrustCaseResult,
    TrustDeltaMetrics,
)
from agentgolem.benchmarks.presets import load_preset_suites
from agentgolem.config.secrets import Secrets
from agentgolem.config.settings import Settings
from agentgolem.memory.models import (
    ConceptualNode,
    MemoryEdge,
    NodeFilter,
    NodeStatus,
    Source,
)
from agentgolem.memory.retrieval import MemoryRetriever
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore
from agentgolem.runtime.loop import MainLoop
from agentgolem.tools.browser import BrowserTool, WebPage

logger = structlog.get_logger(__name__)

ReportPayload = BenchmarkReport | BenchmarkRunReport


def interpret_report(report: BenchmarkReport, output_path: Path | None = None) -> str:
    """Return a concise human-readable summary of one suite report."""
    lines = [f"Suite: {report.suite_name}"]
    if report.run_label:
        lines.append(f"Run label: {report.run_label}")
    if report.description:
        lines.append(f"Description: {report.description}")
    if output_path is not None:
        lines.append(f"Report: {output_path}")

    if report.retrieval is not None:
        retrieval = report.retrieval
        lines.append("")
        lines.append(f"Retrieval ({retrieval.case_count} cases)")
        lines.append(
            f"- Baseline: {retrieval.baseline_name}"
        )
        lines.append(
            "- MRR: "
            f"{_format_metric_summary(retrieval.actual.mean_reciprocal_rank)} "
            f"vs baseline {_format_metric_summary(retrieval.baseline.mean_reciprocal_rank)} "
            f"(delta {_format_metric_summary(retrieval.delta.mean_reciprocal_rank)})"
        )
        lines.append(
            "- Precision@k: "
            f"{_format_metric_summary(retrieval.actual.mean_precision_at_k)} "
            f"vs baseline {_format_metric_summary(retrieval.baseline.mean_precision_at_k)} "
            f"(delta {_format_metric_summary(retrieval.delta.mean_precision_at_k)})"
        )
        lines.append(
            "- NDCG@k: "
            f"{_format_metric_summary(retrieval.actual.mean_ndcg_at_k)} "
            f"vs baseline {_format_metric_summary(retrieval.baseline.mean_ndcg_at_k)} "
            f"(delta {_format_metric_summary(retrieval.delta.mean_ndcg_at_k)})"
        )
        lines.append(
            "- Verdict: "
            + _interpret_status(
                report.retrieval_status,
                positive="retrieval ranking is helping on this suite.",
                neutral="retrieval ranking is mixed on this suite.",
                negative="retrieval ranking is not beating the simple baseline on this suite.",
            )
        )

    if report.trust is not None:
        trust = report.trust
        lines.append("")
        lines.append(f"Trust calibration ({trust.case_count} cases)")
        lines.append(
            f"- Baseline: {trust.baseline_name}"
        )
        lines.append(
            "- Brier score: "
            f"{_format_metric_summary(trust.actual.brier_score)} "
            f"vs baseline {_format_metric_summary(trust.baseline.brier_score)} "
            f"(delta {_format_metric_summary(trust.delta.brier_score)}, lower is better)"
        )
        lines.append(
            "- ECE: "
            f"{_format_metric_summary(trust.actual.expected_calibration_error)} "
            f"vs baseline {_format_metric_summary(trust.baseline.expected_calibration_error)} "
            "("
            f"delta {_format_metric_summary(trust.delta.expected_calibration_error)}, "
            "lower is better)"
        )
        lines.append(
            "- Avg predicted trust / observed reliable rate: "
            f"{_format_metric_summary(trust.actual.average_prediction)} / "
            f"{_format_metric_summary(trust.actual.observed_reliable_rate)}"
        )
        lines.append(
            "- Verdict: "
            + _interpret_status(
                report.trust_status,
                positive=(
                    "trust scores are better calibrated than the stronger "
                    "source-aware baseline."
                ),
                neutral="trust calibration is mixed on this suite.",
                negative="trust calibration is not improving on the stronger baseline here.",
            )
        )

    if report.error_recovery is not None:
        recovery = report.error_recovery
        lines.append("")
        lines.append(f"Error recovery ({recovery.case_count} cases)")
        lines.append(
            f"- Baseline: {recovery.baseline_name}"
        )
        lines.append(
            "- Accuracy: "
            f"{_format_metric_summary(recovery.actual.accuracy)} "
            f"vs baseline {_format_metric_summary(recovery.baseline.accuracy)} "
            f"(delta {_format_metric_summary(recovery.delta.accuracy)})"
        )
        lines.append(
            "- Expected failure handling: "
            f"{_format_metric_summary(recovery.actual.expected_failure_handling_rate)} "
            f"vs baseline "
            f"{_format_metric_summary(recovery.baseline.expected_failure_handling_rate)} "
            f"(delta {_format_metric_summary(recovery.delta.expected_failure_handling_rate)})"
        )
        lines.append(
            "- Expected recovery rate: "
            f"{_format_metric_summary(recovery.actual.expected_recovery_rate)} "
            f"vs baseline {_format_metric_summary(recovery.baseline.expected_recovery_rate)} "
            f"(delta {_format_metric_summary(recovery.delta.expected_recovery_rate)})"
        )
        lines.append(
            "- Verdict: "
            + _interpret_status(
                report.error_recovery_status,
                positive="error recovery is beating the naive baseline on this suite.",
                neutral="error recovery is mixed on this suite.",
                negative="error recovery is not beating the naive baseline on this suite.",
            )
        )

    lines.append("")
    lines.append(f"Overall: {_overall_status_summary(report.overall_status)}")
    lines.extend(
        _score_legend_lines(include_error_recovery=report.error_recovery is not None)
    )
    return "\n".join(lines)


def interpret_run_report(
    run_report: BenchmarkRunReport, output_path: Path | None = None
) -> str:
    """Return a concise human-readable summary of a multi-suite run."""
    lines = ["Benchmark run"]
    if run_report.run_label:
        lines.append(f"Run label: {run_report.run_label}")
    lines.append(f"Target: {run_report.target}")
    if output_path is not None:
        lines.append(f"Report: {output_path}")
    lines.append(
        "Suite results: "
        f"{run_report.suite_count} total "
        f"({run_report.passed_suite_count} pass, "
        f"{run_report.mixed_suite_count} mixed, "
        f"{run_report.failed_suite_count} fail)"
    )

    for suite_report in run_report.suite_reports:
        lines.append("")
        lines.append(
            f"- {suite_report.suite_name} [{suite_report.overall_status.value}]"
        )
        if suite_report.retrieval is not None:
            lines.append(
                "  retrieval: "
                f"{suite_report.retrieval_status.value}, "
                f"baseline {suite_report.retrieval.baseline_name}"
            )
            lines.append(
                "    MRR "
                f"{_format_metric_summary(suite_report.retrieval.actual.mean_reciprocal_rank)} "
                "vs "
                f"{_format_metric_summary(suite_report.retrieval.baseline.mean_reciprocal_rank)} "
                "(delta "
                f"{_format_metric_summary(suite_report.retrieval.delta.mean_reciprocal_rank)})"
            )
        if suite_report.trust is not None:
            lines.append(
                "  trust: "
                f"{suite_report.trust_status.value}, "
                f"baseline {suite_report.trust.baseline_name}"
            )
            lines.append(
                "    Brier "
                f"{_format_metric_summary(suite_report.trust.actual.brier_score)} "
                f"vs {_format_metric_summary(suite_report.trust.baseline.brier_score)} "
                f"(delta {_format_metric_summary(suite_report.trust.delta.brier_score)})"
            )
        if suite_report.error_recovery is not None:
            lines.append(
                "  error recovery: "
                f"{suite_report.error_recovery_status.value}, "
                f"baseline {suite_report.error_recovery.baseline_name}"
            )
            lines.append(
                "    accuracy "
                f"{_format_metric_summary(suite_report.error_recovery.actual.accuracy)} "
                f"vs {_format_metric_summary(suite_report.error_recovery.baseline.accuracy)} "
                f"(delta {_format_metric_summary(suite_report.error_recovery.delta.accuracy)})"
            )

    lines.extend(
        _score_legend_lines(
            include_error_recovery=any(
                report.error_recovery is not None for report in run_report.suite_reports
            )
        )
    )
    return "\n".join(lines)


def interpret_payload(payload: ReportPayload, output_path: Path | None = None) -> str:
    """Return a human-readable summary for any benchmark report payload."""
    if isinstance(payload, BenchmarkRunReport):
        return interpret_run_report(payload, output_path=output_path)
    return interpret_report(payload, output_path=output_path)


class BenchmarkRunner:
    """Run a benchmark suite against an isolated temporary memory store."""

    def __init__(self, suite: BenchmarkSuite, *, run_label: str = "") -> None:
        self._suite = suite
        self._run_label = run_label

    async def run(self) -> BenchmarkReport:
        """Execute the suite and return a structured report."""
        with TemporaryDirectory() as temp_dir:
            db = await init_db(Path(temp_dir) / "benchmark.db")
            try:
                store = SQLiteMemoryStore(db)
                await self._seed_store(store)

                retrieval_report = await self._run_retrieval_benchmark(store)
                trust_report = await self._run_trust_benchmark(store)
                error_recovery_report = await self._run_error_recovery_benchmark(
                    temp_root=Path(temp_dir)
                )
            finally:
                await close_db(db)

        retrieval_status = _retrieval_status(retrieval_report)
        trust_status = _trust_status(trust_report)
        error_recovery_status = _error_recovery_status(error_recovery_report)
        overall_status = _overall_status(
            [retrieval_status, trust_status, error_recovery_status]
        )

        logger.info(
            "benchmark_completed",
            run_label=self._run_label or "default",
            suite_name=self._suite.name,
            retrieval_cases=len(self._suite.retrieval_cases),
            trust_cases=len(self._suite.trust_cases),
            error_recovery_cases=len(self._suite.error_recovery_cases),
            overall_status=overall_status.value,
        )
        return BenchmarkReport(
            run_label=self._run_label,
            suite_name=self._suite.name,
            description=self._suite.description,
            retrieval=retrieval_report,
            trust=trust_report,
            error_recovery=error_recovery_report,
            retrieval_status=retrieval_status,
            trust_status=trust_status,
            error_recovery_status=error_recovery_status,
            overall_status=overall_status,
        )

    async def _seed_store(self, store: SQLiteMemoryStore) -> None:
        known_source_ids = {source.id for source in self._suite.sources}
        known_node_ids = {node.id for node in self._suite.nodes}

        for source_spec in self._suite.sources:
            await store.add_source(
                Source(
                    id=source_spec.id,
                    kind=source_spec.kind,
                    origin=source_spec.origin,
                    reliability=source_spec.reliability,
                    independence_group=source_spec.independence_group,
                    raw_reference=source_spec.raw_reference,
                )
            )

        for node_spec in self._suite.nodes:
            await store.add_node(
                ConceptualNode(
                    id=node_spec.id,
                    text=node_spec.text,
                    type=node_spec.type,
                    search_text=node_spec.search_text,
                    base_usefulness=node_spec.base_usefulness,
                    trustworthiness=node_spec.trustworthiness,
                    salience=node_spec.salience,
                    emotion_label=node_spec.emotion_label,
                    emotion_score=node_spec.emotion_score,
                    centrality=node_spec.centrality,
                    status=node_spec.status,
                    canonical=node_spec.canonical,
                )
            )
            for source_id in node_spec.source_ids:
                if source_id not in known_source_ids:
                    raise ValueError(
                        f"Node {node_spec.id!r} references missing source {source_id!r}"
                    )
                await store.link_node_source(node_spec.id, source_id)

        for edge_spec in self._suite.edges:
            if edge_spec.source_id not in known_node_ids:
                raise ValueError(
                    f"Edge references missing source node {edge_spec.source_id!r}"
                )
            if edge_spec.target_id not in known_node_ids:
                raise ValueError(
                    f"Edge references missing target node {edge_spec.target_id!r}"
                )
            await store.add_edge(
                MemoryEdge(
                    source_id=edge_spec.source_id,
                    target_id=edge_spec.target_id,
                    edge_type=edge_spec.edge_type,
                    weight=edge_spec.weight,
                )
            )

    async def _run_retrieval_benchmark(
        self, store: SQLiteMemoryStore
    ) -> RetrievalBenchmarkReport | None:
        if not self._suite.retrieval_cases:
            return None

        retriever = MemoryRetriever(store)
        case_results: list[RetrievalCaseResult] = []

        actual_rrs: list[float] = []
        actual_precisions: list[float] = []
        actual_ndcgs: list[float] = []
        baseline_rrs: list[float] = []
        baseline_precisions: list[float] = []
        baseline_ndcgs: list[float] = []

        for case in self._suite.retrieval_cases:
            actual_nodes = await retriever.retrieve(case.query, top_k=case.top_k)
            baseline_nodes = await self._retrieve_with_text_baseline(
                store, query=case.query, top_k=case.top_k
            )

            actual_ids = [node.id for node in actual_nodes]
            baseline_ids = [node.id for node in baseline_nodes]

            rr = reciprocal_rank(case.relevant_node_ids, actual_ids)
            precision = precision_at_k(case.relevant_node_ids, actual_ids, case.top_k)
            ndcg = ndcg_at_k(case.relevant_node_ids, actual_ids, case.top_k)

            baseline_rr = reciprocal_rank(case.relevant_node_ids, baseline_ids)
            baseline_precision = precision_at_k(
                case.relevant_node_ids, baseline_ids, case.top_k
            )
            baseline_ndcg = ndcg_at_k(case.relevant_node_ids, baseline_ids, case.top_k)

            actual_rrs.append(rr)
            actual_precisions.append(precision)
            actual_ndcgs.append(ndcg)
            baseline_rrs.append(baseline_rr)
            baseline_precisions.append(baseline_precision)
            baseline_ndcgs.append(baseline_ndcg)

            case_results.append(
                RetrievalCaseResult(
                    case_id=case.id,
                    query=case.query,
                    top_k=case.top_k,
                    relevant_node_ids=case.relevant_node_ids,
                    tags=case.tags,
                    retrieved_node_ids=actual_ids,
                    baseline_retrieved_node_ids=baseline_ids,
                    reciprocal_rank=rr,
                    baseline_reciprocal_rank=baseline_rr,
                    precision_at_k=precision,
                    baseline_precision_at_k=baseline_precision,
                    ndcg_at_k=ndcg,
                    baseline_ndcg_at_k=baseline_ndcg,
                )
            )

        return RetrievalBenchmarkReport(
            case_count=len(case_results),
            baseline_name="lexical_salience_no_trust",
            actual=self._build_retrieval_aggregate_metrics(
                actual_rrs,
                actual_precisions,
                actual_ndcgs,
                seed_offset=10,
            ),
            baseline=self._build_retrieval_aggregate_metrics(
                baseline_rrs,
                baseline_precisions,
                baseline_ndcgs,
                seed_offset=20,
            ),
            delta=self._build_retrieval_aggregate_metrics(
                paired_deltas(actual_rrs, baseline_rrs),
                paired_deltas(actual_precisions, baseline_precisions),
                paired_deltas(actual_ndcgs, baseline_ndcgs),
                seed_offset=30,
            ),
            cases=case_results,
        )

    async def _run_trust_benchmark(
        self, store: SQLiteMemoryStore
    ) -> TrustBenchmarkReport | None:
        if not self._suite.trust_cases:
            return None

        node_ids = [case.node_id for case in self._suite.trust_cases]
        nodes = await store.get_nodes_by_ids(node_ids)
        node_map = {node.id: node for node in nodes}

        actual_points: list[CalibrationPoint] = []
        baseline_points: list[CalibrationPoint] = []
        case_results: list[TrustCaseResult] = []

        for case in self._suite.trust_cases:
            node = node_map.get(case.node_id)
            if node is None:
                raise ValueError(
                    f"Trust case {case.id!r} references missing node {case.node_id!r}"
                )
            observed = 1.0 if case.expected_reliable else 0.0
            baseline_prediction = await self._trust_baseline_prediction(store, case.node_id)
            actual_points.append(
                CalibrationPoint(prediction=node.trustworthiness, observed=observed)
            )
            baseline_points.append(
                CalibrationPoint(prediction=baseline_prediction, observed=observed)
            )
            case_results.append(
                TrustCaseResult(
                    case_id=case.id,
                    node_id=case.node_id,
                    tags=case.tags,
                    prediction=node.trustworthiness,
                    baseline_prediction=baseline_prediction,
                    expected_reliable=case.expected_reliable,
                )
            )

        actual_predictions = [point.prediction for point in actual_points]
        observed_values = [point.observed for point in actual_points]
        baseline_predictions = [point.prediction for point in baseline_points]

        return TrustBenchmarkReport(
            case_count=len(case_results),
            baseline_name="source_reliability_prior",
            actual=self._build_trust_aggregate_metrics(
                actual_points,
                actual_predictions,
                observed_values,
                seed_offset=40,
            ),
            baseline=self._build_trust_aggregate_metrics(
                baseline_points,
                baseline_predictions,
                observed_values,
                seed_offset=50,
            ),
            delta=self._build_trust_delta_metrics(
                actual_points,
                baseline_points,
                paired_deltas(actual_predictions, baseline_predictions),
                seed_offset=60,
            ),
            cases=case_results,
        )

    async def _run_error_recovery_benchmark(
        self, *, temp_root: Path
    ) -> ErrorRecoveryBenchmarkReport | None:
        if not self._suite.error_recovery_cases:
            return None

        case_results: list[ErrorRecoveryCaseResult] = []
        actual_matches: list[float] = []
        baseline_matches: list[float] = []
        actual_failure_matches: list[float] = []
        baseline_failure_matches: list[float] = []
        actual_recovery_matches: list[float] = []
        baseline_recovery_matches: list[float] = []

        for case in self._suite.error_recovery_cases:
            actual_success = await self._execute_error_recovery_case(
                case, temp_root=temp_root
            )
            baseline_success = self._baseline_error_recovery_outcome(case)

            matched = actual_success == case.expected_success
            baseline_matched = baseline_success == case.expected_success

            case_results.append(
                ErrorRecoveryCaseResult(
                    case_id=case.id,
                    scenario=case.scenario,
                    url=case.url,
                    tags=case.tags,
                    expected_success=case.expected_success,
                    actual_success=actual_success,
                    baseline_success=baseline_success,
                    matched_expectation=matched,
                    baseline_matched_expectation=baseline_matched,
                )
            )

            actual_matches.append(1.0 if matched else 0.0)
            baseline_matches.append(1.0 if baseline_matched else 0.0)

            if case.expected_success:
                actual_recovery_matches.append(1.0 if matched else 0.0)
                baseline_recovery_matches.append(1.0 if baseline_matched else 0.0)
            else:
                actual_failure_matches.append(1.0 if matched else 0.0)
                baseline_failure_matches.append(1.0 if baseline_matched else 0.0)

        return ErrorRecoveryBenchmarkReport(
            case_count=len(case_results),
            baseline_name="always_allow_or_succeed",
            actual=self._build_error_recovery_aggregate_metrics(
                actual_matches,
                actual_failure_matches,
                actual_recovery_matches,
                seed_offset=70,
            ),
            baseline=self._build_error_recovery_aggregate_metrics(
                baseline_matches,
                baseline_failure_matches,
                baseline_recovery_matches,
                seed_offset=80,
            ),
            delta=self._build_error_recovery_aggregate_metrics(
                paired_deltas(actual_matches, baseline_matches),
                paired_deltas(actual_failure_matches, baseline_failure_matches),
                paired_deltas(actual_recovery_matches, baseline_recovery_matches),
                seed_offset=90,
            ),
            cases=case_results,
        )

    async def _execute_error_recovery_case(
        self, case: ErrorRecoveryBenchmarkCase, *, temp_root: Path
    ) -> bool:
        if case.scenario == ErrorRecoveryScenario.BROWSER_FETCH_RESULT:
            browser = _BenchmarkBrowserStub(
                status_code=case.status_code or 200,
                url=case.url,
                html=case.html,
                fetch_error=case.fetch_error,
            )
            tool = BrowserTool(browser)
            result = await tool.execute(action="fetch_text", url=case.url)
            return result.success

        if case.scenario == ErrorRecoveryScenario.EMBEDDED_BROWSE_GUARD:
            settings = Settings(data_dir=temp_root / "error_recovery_loop")
            secrets = Secrets(_env_file=None)
            loop = MainLoop(settings=settings, secrets=secrets)
            if case.known_urls:
                loop._remember_urls(case.known_urls)
            await loop._handle_embedded_response_actions(f"BROWSE {case.url}")
            return case.url in loop._browse_queue

        raise ValueError(f"Unsupported error recovery scenario: {case.scenario}")

    def _baseline_error_recovery_outcome(self, case: ErrorRecoveryBenchmarkCase) -> bool:
        # Baseline = no recovery guardrails: any fetched page or browse target is treated as okay.
        if case.scenario in (
            ErrorRecoveryScenario.BROWSER_FETCH_RESULT,
            ErrorRecoveryScenario.EMBEDDED_BROWSE_GUARD,
        ):
            return True
        raise ValueError(f"Unsupported error recovery scenario: {case.scenario}")

    async def _retrieve_with_text_baseline(
        self, store: SQLiteMemoryStore, *, query: str, top_k: int
    ) -> list[ConceptualNode]:
        candidates = await self._collect_candidate_nodes(store, query=query, top_k=top_k)
        query_words = {word.lower() for word in query.split() if len(word) >= 3}
        query_lower = query.lower()

        def sort_key(node: ConceptualNode) -> tuple[float, float, float, float, str]:
            searchable = f"{node.text} {node.search_text}".lower()
            keyword_hits = sum(1 for word in query_words if word in searchable)
            phrase_bonus = 1.0 if query_lower and query_lower in searchable else 0.0
            return (
                -phrase_bonus,
                -float(keyword_hits),
                -node.salience,
                -node.base_usefulness,
                node.id,
            )

        return sorted(candidates, key=sort_key)[:top_k]

    async def _collect_candidate_nodes(
        self, store: SQLiteMemoryStore, *, query: str, top_k: int
    ) -> list[ConceptualNode]:
        words = [word.strip() for word in query.split() if len(word.strip()) >= 3]
        all_results: dict[str, ConceptualNode] = {}

        for word in words:
            nodes = await store.query_nodes(
                NodeFilter(text_contains=word, status=NodeStatus.ACTIVE, limit=top_k * 5)
            )
            for node in nodes:
                all_results[node.id] = node

        phrase_results = await store.query_nodes(
            NodeFilter(text_contains=query, status=NodeStatus.ACTIVE, limit=top_k)
        )
        for node in phrase_results:
            all_results[node.id] = node

        return list(all_results.values())

    async def _trust_baseline_prediction(
        self,
        store: SQLiteMemoryStore,
        node_id: str,
    ) -> float:
        sources = await store.get_node_sources(node_id)
        if not sources:
            return 0.5
        return mean([source.reliability for source in sources])

    def _build_retrieval_aggregate_metrics(
        self,
        reciprocal_ranks: list[float],
        precisions: list[float],
        ndcgs: list[float],
        *,
        seed_offset: int,
    ) -> RetrievalAggregateMetrics:
        return RetrievalAggregateMetrics(
            mean_reciprocal_rank=self._metric_summary(
                reciprocal_ranks, seed_offset=seed_offset + 1
            ),
            mean_precision_at_k=self._metric_summary(
                precisions, seed_offset=seed_offset + 2
            ),
            mean_ndcg_at_k=self._metric_summary(ndcgs, seed_offset=seed_offset + 3),
        )

    def _build_trust_aggregate_metrics(
        self,
        points: list[CalibrationPoint],
        predictions: list[float],
        observed_values: list[float],
        *,
        seed_offset: int,
    ) -> TrustAggregateMetrics:
        return TrustAggregateMetrics(
            brier_score=self._statistic_metric_summary(
                points,
                statistic=brier_score,
                seed_offset=seed_offset + 1,
            ),
            expected_calibration_error=self._statistic_metric_summary(
                points,
                statistic=expected_calibration_error,
                seed_offset=seed_offset + 2,
            ),
            average_prediction=self._metric_summary(
                predictions, seed_offset=seed_offset + 3
            ),
            observed_reliable_rate=self._metric_summary(
                observed_values, seed_offset=seed_offset + 4
            ),
        )

    def _build_trust_delta_metrics(
        self,
        actual_points: list[CalibrationPoint],
        baseline_points: list[CalibrationPoint],
        prediction_deltas: list[float],
        *,
        seed_offset: int,
    ) -> TrustDeltaMetrics:
        return TrustDeltaMetrics(
            brier_score=self._paired_statistic_metric_summary(
                actual_points,
                baseline_points,
                statistic=brier_score,
                seed_offset=seed_offset + 1,
            ),
            expected_calibration_error=self._paired_statistic_metric_summary(
                actual_points,
                baseline_points,
                statistic=expected_calibration_error,
                seed_offset=seed_offset + 2,
            ),
            average_prediction=self._metric_summary(
                prediction_deltas, seed_offset=seed_offset + 3
            ),
        )

    def _build_error_recovery_aggregate_metrics(
        self,
        accuracy_values: list[float],
        failure_values: list[float],
        recovery_values: list[float],
        *,
        seed_offset: int,
    ) -> ErrorRecoveryAggregateMetrics:
        return ErrorRecoveryAggregateMetrics(
            accuracy=self._metric_summary(accuracy_values, seed_offset=seed_offset + 1),
            expected_failure_handling_rate=self._metric_summary(
                failure_values, seed_offset=seed_offset + 2
            ),
            expected_recovery_rate=self._metric_summary(
                recovery_values, seed_offset=seed_offset + 3
            ),
        )

    def _metric_summary(self, values: list[float], *, seed_offset: int) -> MetricSummary:
        summary = bootstrap_mean(
            values,
            resamples=self._suite.bootstrap_resamples,
            seed=self._suite.bootstrap_seed + seed_offset,
            confidence_level=self._suite.confidence_level,
        )
        return _to_metric_summary(summary)

    def _statistic_metric_summary(
        self,
        values: list[CalibrationPoint],
        *,
        statistic,
        seed_offset: int,
    ) -> MetricSummary:
        summary = bootstrap_statistic(
            values,
            statistic=statistic,
            resamples=self._suite.bootstrap_resamples,
            seed=self._suite.bootstrap_seed + seed_offset,
            confidence_level=self._suite.confidence_level,
        )
        return _to_metric_summary(summary)

    def _paired_statistic_metric_summary(
        self,
        actual_values: list[CalibrationPoint],
        baseline_values: list[CalibrationPoint],
        *,
        statistic,
        seed_offset: int,
    ) -> MetricSummary:
        summary = bootstrap_paired_statistic(
            actual_values,
            baseline_values,
            statistic=statistic,
            resamples=self._suite.bootstrap_resamples,
            seed=self._suite.bootstrap_seed + seed_offset,
            confidence_level=self._suite.confidence_level,
        )
        return _to_metric_summary(summary)


async def run_target(target: Path, *, run_label: str = "") -> ReportPayload:
    """Run one suite file or all suite files within a directory."""
    if target.is_file():
        return await BenchmarkRunner(load_suite(target), run_label=run_label).run()

    if not target.is_dir():
        raise FileNotFoundError(f"Benchmark target {target} does not exist")

    suite_paths = sorted(target.rglob("*.json"))
    if not suite_paths:
        raise ValueError(f"No benchmark suites found under {target}")

    suites = [load_suite(suite_path) for suite_path in suite_paths]
    return await run_suites(suites, target=str(target), run_label=run_label)


async def run_preset(preset: str, *, run_label: str = "") -> BenchmarkRunReport:
    """Run a named deterministic preset."""
    suites = load_preset_suites(preset)
    return await run_suites(suites, target=f"preset:{preset}", run_label=run_label)


async def run_suites(
    suites: list[BenchmarkSuite],
    *,
    target: str,
    run_label: str = "",
) -> BenchmarkRunReport:
    """Run multiple suites and aggregate them into a run report."""
    suite_reports: list[BenchmarkReport] = []
    for suite in suites:
        suite_reports.append(await BenchmarkRunner(suite, run_label=run_label).run())

    pass_count = sum(report.overall_status == BenchmarkStatus.PASS for report in suite_reports)
    mixed_count = sum(
        report.overall_status == BenchmarkStatus.MIXED for report in suite_reports
    )
    fail_count = sum(report.overall_status == BenchmarkStatus.FAIL for report in suite_reports)

    return BenchmarkRunReport(
        run_label=run_label,
        target=target,
        suite_count=len(suite_reports),
        passed_suite_count=pass_count,
        mixed_suite_count=mixed_count,
        failed_suite_count=fail_count,
        suite_reports=suite_reports,
    )


def load_suite(path: Path) -> BenchmarkSuite:
    """Load a benchmark suite from JSON."""
    return BenchmarkSuite.model_validate_json(path.read_text(encoding="utf-8"))


def load_report(path: Path) -> ReportPayload:
    """Load a benchmark report payload from JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if "suite_reports" in data:
        return BenchmarkRunReport.model_validate(data)
    return BenchmarkReport.model_validate(data)


def write_report(payload: ReportPayload, path: Path) -> None:
    """Write any benchmark report payload to disk as formatted JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )


async def _run_from_args(args: argparse.Namespace) -> int:
    if args.preset and args.suite is not None:
        raise ValueError("Cannot specify both suite path and --preset.")
    if args.preset:
        payload = await run_preset(args.preset, run_label=args.label)
    elif args.suite is not None:
        payload = await run_target(args.suite, run_label=args.label)
    else:
        payload = await run_preset("robust", run_label=args.label)

    if args.output is not None:
        write_report(payload, args.output)

    if args.interpret:
        sys.stdout.write(interpret_payload(payload, output_path=args.output) + "\n")
        return 0

    if args.output is not None:
        if isinstance(payload, BenchmarkRunReport):
            summary = {
                "run_label": payload.run_label,
                "output": str(args.output),
                "suite_count": payload.suite_count,
                "passed_suite_count": payload.passed_suite_count,
                "mixed_suite_count": payload.mixed_suite_count,
                "failed_suite_count": payload.failed_suite_count,
            }
        else:
            summary = {
                "run_label": payload.run_label,
                "suite_name": payload.suite_name,
                "output": str(args.output),
                "overall_status": payload.overall_status.value,
                "retrieval_mrr": (
                    payload.retrieval.actual.mean_reciprocal_rank.value
                    if payload.retrieval is not None
                    else None
                ),
                "trust_brier_score": (
                    payload.trust.actual.brier_score.value if payload.trust is not None else None
                ),
                "error_recovery_accuracy": (
                    payload.error_recovery.actual.accuracy.value
                    if payload.error_recovery is not None
                    else None
                ),
            }
        sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    else:
        sys.stdout.write(json.dumps(payload.model_dump(mode="json"), indent=2) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the benchmark CLI parser."""
    parser = argparse.ArgumentParser(
        description="Run offline AgentGolem benchmark suites from files, directories, or presets."
    )
    parser.add_argument(
        "suite",
        type=Path,
        nargs="?",
        help=(
            "Optional path to a benchmark suite JSON file or a directory "
            "containing suite JSON files."
        ),
    )
    parser.add_argument(
        "--preset",
        default="",
        help=(
            "Optional named preset, such as 'robust'. Defaults to 'robust' "
            "when no suite path is given."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path for a JSON benchmark report.",
    )
    parser.add_argument(
        "--interpret",
        action="store_true",
        help="Print a human-readable interpretation instead of JSON output.",
    )
    parser.add_argument(
        "--label",
        default="",
        help="Optional run label, e.g. a model or provider name for later comparison.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the benchmark runner."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run_from_args(args))


def _error_recovery_status(
    error_recovery: ErrorRecoveryBenchmarkReport | None,
) -> BenchmarkStatus:
    if error_recovery is None:
        return BenchmarkStatus.NOT_APPLICABLE
    return _status_from_deltas(
        deltas=[
            error_recovery.delta.accuracy,
            error_recovery.delta.expected_failure_handling_rate,
            error_recovery.delta.expected_recovery_rate,
        ],
        higher_is_better=True,
    )


def _retrieval_status(
    retrieval: RetrievalBenchmarkReport | None,
) -> BenchmarkStatus:
    if retrieval is None:
        return BenchmarkStatus.NOT_APPLICABLE
    return _status_from_deltas(
        deltas=[
            retrieval.delta.mean_reciprocal_rank,
            retrieval.delta.mean_precision_at_k,
            retrieval.delta.mean_ndcg_at_k,
        ],
        higher_is_better=True,
    )


def _trust_status(trust: TrustBenchmarkReport | None) -> BenchmarkStatus:
    if trust is None:
        return BenchmarkStatus.NOT_APPLICABLE
    return _status_from_deltas(
        deltas=[
            trust.delta.brier_score,
            trust.delta.expected_calibration_error,
        ],
        higher_is_better=False,
    )


def _status_from_deltas(
    deltas: list[MetricSummary],
    *,
    higher_is_better: bool,
) -> BenchmarkStatus:
    wins = 0
    losses = 0
    uncertain = 0
    for delta in deltas:
        lower = delta.ci_lower if delta.ci_lower is not None else delta.value
        upper = delta.ci_upper if delta.ci_upper is not None else delta.value
        if higher_is_better:
            if lower > 0:
                wins += 1
            elif upper < 0:
                losses += 1
            else:
                uncertain += 1
        else:
            if upper < 0:
                wins += 1
            elif lower > 0:
                losses += 1
            else:
                uncertain += 1

    if wins > 0 and losses == 0 and uncertain == 0:
        return BenchmarkStatus.PASS
    if losses > 0 and wins == 0 and uncertain == 0:
        return BenchmarkStatus.FAIL
    return BenchmarkStatus.MIXED


def _overall_status(statuses: list[BenchmarkStatus]) -> BenchmarkStatus:
    relevant = [status for status in statuses if status != BenchmarkStatus.NOT_APPLICABLE]
    if not relevant:
        return BenchmarkStatus.NOT_APPLICABLE
    if all(status == BenchmarkStatus.PASS for status in relevant):
        return BenchmarkStatus.PASS
    if all(status == BenchmarkStatus.FAIL for status in relevant):
        return BenchmarkStatus.FAIL
    return BenchmarkStatus.MIXED


def _interpret_status(
    status: BenchmarkStatus,
    *,
    positive: str,
    neutral: str,
    negative: str,
) -> str:
    if status == BenchmarkStatus.PASS:
        return positive
    if status == BenchmarkStatus.FAIL:
        return negative
    if status == BenchmarkStatus.MIXED:
        return neutral
    return "no applicable benchmark cases were present for this dimension."


def _overall_status_summary(status: BenchmarkStatus) -> str:
    if status == BenchmarkStatus.PASS:
        return (
            "on this suite, the architecture is beating the stronger baselines "
            "with supportive uncertainty estimates."
        )
    if status == BenchmarkStatus.FAIL:
        return "on this suite, the architecture is not showing a robust benchmark advantage yet."
    if status == BenchmarkStatus.MIXED:
        return (
            "this suite shows a mixed picture; some mechanisms help, but the "
            "gains are not yet clean or stable."
        )
    return "this suite did not include enough benchmark dimensions to score."


def _score_legend_lines(*, include_error_recovery: bool) -> list[str]:
    lines = [
        "",
        "How to read scores:",
        "- pass = delta confidence intervals support beating the baseline on every tracked metric",
        "- mixed = some gains exist, but uncertainty or metric disagreement remains",
        "- fail = the stronger baseline still wins once uncertainty is considered",
        "- MRR = where the first relevant memory appears; 1.0 is first place, 0.5 is second",
        "- Precision@k = fraction of the top-k results that were relevant; higher is better",
        "- NDCG@k = ranking quality across the top-k results; 1.0 is ideal ordering",
        "- Brier score / ECE = trust calibration error; lower is better, 0.0 is perfect",
        (
            "- delta = actual minus baseline; positive is better for "
            "retrieval/recovery, negative is better for trust error metrics"
        ),
    ]
    if include_error_recovery:
        lines.append(
            "- Error recovery accuracy = share of failure/recovery scenarios handled as expected"
        )
    return lines


def _format_metric_summary(summary: MetricSummary) -> str:
    if summary.ci_lower is None or summary.ci_upper is None or summary.confidence_level is None:
        return f"{summary.value:.3f}"
    confidence_pct = int(round(summary.confidence_level * 100))
    return (
        f"{summary.value:.3f} "
        f"[{confidence_pct}% CI {summary.ci_lower:.3f}, {summary.ci_upper:.3f}]"
    )


def _to_metric_summary(summary: BootstrapSummary) -> MetricSummary:
    return MetricSummary(
        value=summary.value,
        ci_lower=summary.ci_lower,
        ci_upper=summary.ci_upper,
        confidence_level=summary.confidence_level,
    )


class _BenchmarkBrowserStub:
    """Minimal browser stub for deterministic benchmark cases."""

    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        html: str,
        fetch_error: str = "",
    ) -> None:
        self._status_code = status_code
        self._url = url
        self._html = html or "<html><body>ok</body></html>"
        self._fetch_error = fetch_error

    async def fetch(self, url: str) -> WebPage:
        if self._fetch_error:
            raise RuntimeError(self._fetch_error)
        return WebPage(
            url=self._url or url,
            status_code=self._status_code,
            content=self._html,
            headers={},
            fetched_at=datetime.now(UTC),
        )

    def extract_text(self, page: WebPage) -> str:
        return page.content

    def extract_links(self, page: WebPage) -> list[str]:
        return []
