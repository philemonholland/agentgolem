"""Offline benchmark runner for AgentGolem."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import structlog

from agentgolem.benchmarks.metrics import (
    CalibrationPoint,
    brier_score,
    expected_calibration_error,
    mean,
    ndcg_at_k,
    precision_at_k,
    reciprocal_rank,
)
from agentgolem.benchmarks.models import (
    BenchmarkReport,
    BenchmarkSuite,
    RetrievalAggregateMetrics,
    RetrievalBenchmarkReport,
    RetrievalCaseResult,
    TrustAggregateMetrics,
    TrustBenchmarkReport,
    TrustCaseResult,
)
from agentgolem.memory.models import ConceptualNode, MemoryEdge, NodeFilter, NodeStatus, Source
from agentgolem.memory.retrieval import MemoryRetriever
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore

logger = structlog.get_logger(__name__)

_TRUST_BASELINE = 0.5


def interpret_report(report: BenchmarkReport, output_path: Path | None = None) -> str:
    """Return a concise human-readable summary of benchmark results."""
    lines = [f"Suite: {report.suite_name}"]
    if report.description:
        lines.append(f"Description: {report.description}")
    if output_path is not None:
        lines.append(f"Report: {output_path}")

    overall_findings: list[str] = []

    if report.retrieval is not None:
        retrieval = report.retrieval
        lines.append("")
        lines.append(f"Retrieval ({retrieval.case_count} cases)")
        lines.append(
            "- MRR: "
            f"{retrieval.actual.mean_reciprocal_rank:.3f} "
            f"vs baseline {retrieval.baseline.mean_reciprocal_rank:.3f}"
        )
        lines.append(
            "- Precision@k: "
            f"{retrieval.actual.mean_precision_at_k:.3f} "
            f"vs baseline {retrieval.baseline.mean_precision_at_k:.3f}"
        )
        lines.append(
            "- NDCG@k: "
            f"{retrieval.actual.mean_ndcg_at_k:.3f} "
            f"vs baseline {retrieval.baseline.mean_ndcg_at_k:.3f}"
        )
        retrieval_wins = _count_wins(
            actual_values=[
                retrieval.actual.mean_reciprocal_rank,
                retrieval.actual.mean_precision_at_k,
                retrieval.actual.mean_ndcg_at_k,
            ],
            baseline_values=[
                retrieval.baseline.mean_reciprocal_rank,
                retrieval.baseline.mean_precision_at_k,
                retrieval.baseline.mean_ndcg_at_k,
            ],
            higher_is_better=True,
        )
        retrieval_losses = _count_losses(
            actual_values=[
                retrieval.actual.mean_reciprocal_rank,
                retrieval.actual.mean_precision_at_k,
                retrieval.actual.mean_ndcg_at_k,
            ],
            baseline_values=[
                retrieval.baseline.mean_reciprocal_rank,
                retrieval.baseline.mean_precision_at_k,
                retrieval.baseline.mean_ndcg_at_k,
            ],
            higher_is_better=True,
        )
        retrieval_verdict = _interpret_dimension(
            wins=retrieval_wins,
            losses=retrieval_losses,
            positive="retrieval ranking is helping on this suite.",
            neutral="retrieval ranking is mixed on this suite.",
            negative="retrieval ranking is not beating the simple baseline on this suite.",
        )
        lines.append(f"- Verdict: {retrieval_verdict}")
        overall_findings.append(retrieval_verdict)

    if report.trust is not None:
        trust = report.trust
        lines.append("")
        lines.append(f"Trust calibration ({trust.case_count} cases)")
        lines.append(
            "- Brier score: "
            f"{trust.actual.brier_score:.3f} "
            f"vs baseline {trust.constant_baseline.brier_score:.3f} "
            "(lower is better)"
        )
        lines.append(
            "- ECE: "
            f"{trust.actual.expected_calibration_error:.3f} "
            f"vs baseline {trust.constant_baseline.expected_calibration_error:.3f} "
            "(lower is better)"
        )
        lines.append(
            "- Avg predicted trust / observed reliable rate: "
            f"{trust.actual.average_prediction:.3f} / {trust.actual.observed_reliable_rate:.3f}"
        )
        trust_wins = _count_wins(
            actual_values=[
                trust.actual.brier_score,
                trust.actual.expected_calibration_error,
            ],
            baseline_values=[
                trust.constant_baseline.brier_score,
                trust.constant_baseline.expected_calibration_error,
            ],
            higher_is_better=False,
        )
        trust_losses = _count_losses(
            actual_values=[
                trust.actual.brier_score,
                trust.actual.expected_calibration_error,
            ],
            baseline_values=[
                trust.constant_baseline.brier_score,
                trust.constant_baseline.expected_calibration_error,
            ],
            higher_is_better=False,
        )
        trust_verdict = _interpret_dimension(
            wins=trust_wins,
            losses=trust_losses,
            positive="trust scores are better calibrated than the constant baseline.",
            neutral="trust calibration is mixed on this suite.",
            negative="trust calibration is not improving on the constant baseline here.",
        )
        lines.append(f"- Verdict: {trust_verdict}")
        overall_findings.append(trust_verdict)

    if overall_findings:
        lines.append("")
        lines.append(f"Overall: {_overall_verdict(overall_findings)}")

    return "\n".join(lines)


class BenchmarkRunner:
    """Run a benchmark suite against an isolated temporary memory store."""

    def __init__(self, suite: BenchmarkSuite) -> None:
        self._suite = suite

    async def run(self) -> BenchmarkReport:
        """Execute the suite and return a structured report."""
        with TemporaryDirectory() as temp_dir:
            db = await init_db(Path(temp_dir) / "benchmark.db")
            try:
                store = SQLiteMemoryStore(db)
                await self._seed_store(store)

                retrieval_report = await self._run_retrieval_benchmark(store)
                trust_report = await self._run_trust_benchmark(store)
            finally:
                await close_db(db)

        logger.info(
            "benchmark_completed",
            suite_name=self._suite.name,
            retrieval_cases=len(self._suite.retrieval_cases),
            trust_cases=len(self._suite.trust_cases),
        )
        return BenchmarkReport(
            suite_name=self._suite.name,
            description=self._suite.description,
            retrieval=retrieval_report,
            trust=trust_report,
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
                raise ValueError(f"Edge references missing source node {edge_spec.source_id!r}")
            if edge_spec.target_id not in known_node_ids:
                raise ValueError(f"Edge references missing target node {edge_spec.target_id!r}")
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
            actual=RetrievalAggregateMetrics(
                mean_reciprocal_rank=mean(actual_rrs),
                mean_precision_at_k=mean(actual_precisions),
                mean_ndcg_at_k=mean(actual_ndcgs),
            ),
            baseline=RetrievalAggregateMetrics(
                mean_reciprocal_rank=mean(baseline_rrs),
                mean_precision_at_k=mean(baseline_precisions),
                mean_ndcg_at_k=mean(baseline_ndcgs),
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
            actual_points.append(
                CalibrationPoint(prediction=node.trustworthiness, observed=observed)
            )
            baseline_points.append(
                CalibrationPoint(prediction=_TRUST_BASELINE, observed=observed)
            )
            case_results.append(
                TrustCaseResult(
                    case_id=case.id,
                    node_id=case.node_id,
                    prediction=node.trustworthiness,
                    baseline_prediction=_TRUST_BASELINE,
                    expected_reliable=case.expected_reliable,
                )
            )

        actual_predictions = [point.prediction for point in actual_points]
        observed_values = [point.observed for point in actual_points]

        return TrustBenchmarkReport(
            case_count=len(case_results),
            actual=TrustAggregateMetrics(
                brier_score=brier_score(actual_points),
                expected_calibration_error=expected_calibration_error(actual_points),
                average_prediction=mean(actual_predictions),
                observed_reliable_rate=mean(observed_values),
            ),
            constant_baseline=TrustAggregateMetrics(
                brier_score=brier_score(baseline_points),
                expected_calibration_error=expected_calibration_error(baseline_points),
                average_prediction=_TRUST_BASELINE,
                observed_reliable_rate=mean(observed_values),
            ),
            cases=case_results,
        )

    async def _retrieve_with_text_baseline(
        self, store: SQLiteMemoryStore, *, query: str, top_k: int
    ) -> list[ConceptualNode]:
        candidates = await self._collect_candidate_nodes(store, query=query, top_k=top_k)
        query_words = {word.lower() for word in query.split() if len(word) >= 3}
        query_lower = query.lower()

        def sort_key(node: ConceptualNode) -> tuple[float, float, float, str]:
            searchable = f"{node.text} {node.search_text}".lower()
            keyword_hits = sum(1 for word in query_words if word in searchable)
            phrase_bonus = 1.0 if query_lower and query_lower in searchable else 0.0
            return (-phrase_bonus, -float(keyword_hits), -node.salience, node.id)

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


def load_suite(path: Path) -> BenchmarkSuite:
    """Load a benchmark suite from JSON."""
    return BenchmarkSuite.model_validate_json(path.read_text(encoding="utf-8"))


def write_report(report: BenchmarkReport, path: Path) -> None:
    """Write a benchmark report to disk as formatted JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )


async def _run_from_args(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    report = await BenchmarkRunner(suite).run()

    if args.output is not None:
        write_report(report, args.output)

    if args.interpret:
        sys.stdout.write(interpret_report(report, output_path=args.output) + "\n")
        return 0

    if args.output is not None:
        payload = {
            "suite_name": report.suite_name,
            "output": str(args.output),
            "retrieval_mrr": (
                report.retrieval.actual.mean_reciprocal_rank if report.retrieval else None
            ),
            "trust_brier_score": report.trust.actual.brier_score if report.trust else None,
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        sys.stdout.write(json.dumps(report.model_dump(mode="json"), indent=2) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the benchmark CLI parser."""
    parser = argparse.ArgumentParser(description="Run an offline AgentGolem benchmark suite.")
    parser.add_argument("suite", type=Path, help="Path to a benchmark suite JSON file.")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the benchmark runner."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run_from_args(args))


def _count_wins(
    actual_values: list[float], baseline_values: list[float], *, higher_is_better: bool
) -> int:
    wins = 0
    for actual, baseline in zip(actual_values, baseline_values, strict=True):
        if higher_is_better and actual > baseline:
            wins += 1
        if not higher_is_better and actual < baseline:
            wins += 1
    return wins


def _count_losses(
    actual_values: list[float], baseline_values: list[float], *, higher_is_better: bool
) -> int:
    losses = 0
    for actual, baseline in zip(actual_values, baseline_values, strict=True):
        if higher_is_better and actual < baseline:
            losses += 1
        if not higher_is_better and actual > baseline:
            losses += 1
    return losses


def _interpret_dimension(
    *,
    wins: int,
    losses: int,
    positive: str,
    neutral: str,
    negative: str,
) -> str:
    if wins > 0 and losses == 0:
        return positive
    if wins == 0 and losses > 0:
        return negative
    return neutral


def _overall_verdict(findings: list[str]) -> str:
    positive_count = sum(
        "helping" in finding or "better calibrated" in finding for finding in findings
    )
    negative_count = sum("not " in finding for finding in findings)
    if positive_count == len(findings):
        return "on this suite, the architecture is beating the simple baselines."
    if negative_count == len(findings):
        return "on this suite, the architecture is not showing a benchmark advantage yet."
    return "this suite shows a mixed picture; some mechanisms help, others still need work."
