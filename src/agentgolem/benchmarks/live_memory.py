"""Read-only lifecycle audits over live agent memory graphs."""
from __future__ import annotations

import asyncio
import shutil
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

import aiosqlite
import structlog

from agentgolem.benchmarks.metrics import bootstrap_mean
from agentgolem.benchmarks.models import (
    BenchmarkStatus,
    LifecycleTraversalMetric,
    LiveMemoryLifecycleAggregateReport,
    LiveMemoryLifecycleAgentReport,
    LiveMemoryLifecycleRunReport,
    MetricSummary,
)
from agentgolem.memory.models import EdgeType, NodeFilter, NodeStatus
from agentgolem.memory.retrieval import MemoryRetriever
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore

logger = structlog.get_logger(__name__)

_LIFECYCLE_BOOTSTRAP_RESAMPLES = 300
_LIFECYCLE_BOOTSTRAP_SEED = 17
_LIFECYCLE_CONFIDENCE_LEVEL = 0.95
_MAX_NEIGHBORHOOD_CASES = 120


@dataclass
class _AgentLifecycleInputs:
    report: LiveMemoryLifecycleAgentReport
    provenance_values: list[float]
    source_count_values: list[float]
    edge_participation_values: list[float]
    trust_gap_values: list[float]
    zero_access_values: list[float]
    access_count_values: list[float]
    canonical_values: list[float]
    archived_values: list[float]
    neighborhood_successes: list[float]
    contradiction_successes: list[float]
    supersession_successes: list[float]


def _metric_summary(values: list[float], *, seed_offset: int) -> MetricSummary:
    if not values:
        return MetricSummary(value=0.0)
    summary = bootstrap_mean(
        values,
        resamples=_LIFECYCLE_BOOTSTRAP_RESAMPLES,
        seed=_LIFECYCLE_BOOTSTRAP_SEED + seed_offset,
        confidence_level=_LIFECYCLE_CONFIDENCE_LEVEL,
    )
    return MetricSummary(
        value=summary.value,
        ci_lower=summary.ci_lower,
        ci_upper=summary.ci_upper,
        confidence_level=summary.confidence_level,
    )


def _point_metric(value: float) -> MetricSummary:
    return MetricSummary(value=value)


def _traversal_metric(
    values: list[float],
    *,
    seed_offset: int,
) -> LifecycleTraversalMetric | None:
    if not values:
        return None
    return LifecycleTraversalMetric(
        case_count=len(values),
        success_rate=_metric_summary(values, seed_offset=seed_offset),
    )


def _format_metric_summary(summary: MetricSummary) -> str:
    if summary.confidence_level is not None and summary.ci_lower is not None and summary.ci_upper is not None:
        confidence = int(round(summary.confidence_level * 100))
        return (
            f"{summary.value:.3f} "
            f"[{confidence}% CI {summary.ci_lower:.3f}, {summary.ci_upper:.3f}]"
        )
    return f"{summary.value:.3f}"


def _combine_statuses(statuses: list[BenchmarkStatus]) -> BenchmarkStatus:
    relevant = [status for status in statuses if status != BenchmarkStatus.NOT_APPLICABLE]
    if not relevant:
        return BenchmarkStatus.NOT_APPLICABLE
    if all(status == BenchmarkStatus.PASS for status in relevant):
        return BenchmarkStatus.PASS
    if all(status == BenchmarkStatus.FAIL for status in relevant):
        return BenchmarkStatus.FAIL
    return BenchmarkStatus.MIXED


def _agent_status(report: LiveMemoryLifecycleAgentReport) -> BenchmarkStatus:
    if report.provenance_coverage.value < 0.85 or report.edge_participation_rate.value < 0.5:
        return BenchmarkStatus.FAIL

    traversal_metrics = [
        metric.success_rate.value
        for metric in (
            report.neighborhood_recall,
            report.contradiction_recall,
            report.supersession_recall,
        )
        if metric is not None
    ]
    if any(value < 0.8 for value in traversal_metrics):
        return BenchmarkStatus.FAIL

    if (
        report.provenance_coverage.value >= 0.95
        and report.edge_participation_rate.value >= 0.75
        and all(value >= 0.95 for value in traversal_metrics)
    ):
        return BenchmarkStatus.PASS
    return BenchmarkStatus.MIXED


def _discover_graph_paths(target: Path) -> list[tuple[str, Path]]:
    if target.is_file():
        if target.name.lower() != "graph.db":
            raise ValueError("Live memory target file must be a graph.db snapshot.")
        return [(target.parent.parent.name, target)]

    if not target.exists():
        raise FileNotFoundError(f"Live memory target {target} does not exist")

    agent_graph = target / "memory" / "graph.db"
    if agent_graph.exists():
        return [(target.name, agent_graph)]

    graph_paths = sorted(
        (child.name, child / "memory" / "graph.db")
        for child in target.iterdir()
        if child.is_dir() and (child / "memory" / "graph.db").exists()
    )
    if not graph_paths:
        raise ValueError(f"No live memory graphs found under {target}")
    return graph_paths


def _snapshot_graph(source_path: Path, snapshot_path: Path) -> None:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source:
        with sqlite3.connect(snapshot_path) as destination:
            source.backup(destination)


class LiveMemoryLifecycleRunner:
    """Audit live agent memory graphs without mutating the originals."""

    def __init__(self, target: Path, *, run_label: str = "") -> None:
        self._target = target
        self._run_label = run_label

    async def run(self) -> LiveMemoryLifecycleRunReport:
        graph_paths = _discover_graph_paths(self._target)
        lifecycle_inputs: list[_AgentLifecycleInputs] = []

        temp_root = Path(mkdtemp())
        try:
            for index, (agent_id, graph_path) in enumerate(graph_paths):
                snapshot_path = temp_root / f"{index:02d}_{agent_id}.db"
                _snapshot_graph(graph_path, snapshot_path)
                db = await init_db(snapshot_path)
                try:
                    store = SQLiteMemoryStore(db)
                    lifecycle_inputs.append(
                        await self._audit_agent(agent_id, graph_path, db, store)
                    )
                finally:
                    await close_db(db)
        finally:
            for _ in range(5):
                try:
                    shutil.rmtree(temp_root)
                    break
                except PermissionError:
                    await asyncio.sleep(0.1)
            else:
                logger.warning("live_memory_temp_cleanup_failed", path=str(temp_root))

        reports = [entry.report for entry in lifecycle_inputs]
        statuses = [report.overall_status for report in reports]
        aggregate = self._build_aggregate(lifecycle_inputs)
        overall_status = _combine_statuses(statuses)
        pass_count = sum(status == BenchmarkStatus.PASS for status in statuses)
        mixed_count = sum(status == BenchmarkStatus.MIXED for status in statuses)
        fail_count = sum(status == BenchmarkStatus.FAIL for status in statuses)

        logger.info(
            "live_memory_lifecycle_completed",
            run_label=self._run_label or "default",
            target=str(self._target),
            agent_count=len(reports),
            overall_status=overall_status.value,
        )

        return LiveMemoryLifecycleRunReport(
            run_label=self._run_label,
            target=str(self._target),
            agent_count=len(reports),
            passed_agent_count=pass_count,
            mixed_agent_count=mixed_count,
            failed_agent_count=fail_count,
            overall_status=overall_status,
            aggregate=aggregate,
            agent_reports=reports,
        )

    async def _audit_agent(
        self,
        agent_id: str,
        original_graph_path: Path,
        db: aiosqlite.Connection,
        store: SQLiteMemoryStore,
    ) -> _AgentLifecycleInputs:
        nodes = await store.query_nodes(NodeFilter(limit=50_000))
        retriever = MemoryRetriever(store)

        node_count = len(nodes)
        node_type_counts = Counter(node.type.value for node in nodes)
        archived_values = [
            1.0 if node.status == NodeStatus.ARCHIVED else 0.0 for node in nodes
        ]
        canonical_values = [1.0 if node.canonical else 0.0 for node in nodes]
        access_count_values = [float(node.access_count) for node in nodes]
        zero_access_values = [1.0 if node.access_count == 0 else 0.0 for node in nodes]

        node_source_counts = await self._count_rows_by_key(
            db,
            "SELECT node_id, COUNT(*) AS count FROM node_sources GROUP BY node_id",
        )
        source_averages = await self._average_source_reliability_by_node(db)

        source_count_values = [
            float(node_source_counts.get(node.id, 0)) for node in nodes
        ]
        provenance_values = [
            1.0 if node.id in node_source_counts else 0.0 for node in nodes
        ]
        trust_gap_values = [
            abs(node.trustworthiness - source_averages[node.id])
            for node in nodes
            if node.id in source_averages
        ]

        edge_rows = await self._fetch_edge_rows(db)
        edge_type_counts = Counter(edge_type for _, _, edge_type in edge_rows)
        edge_participating_nodes = {
            node_id
            for source_id, target_id, _ in edge_rows
            for node_id in (source_id, target_id)
        }
        edge_participation_values = [
            1.0 if node.id in edge_participating_nodes else 0.0 for node in nodes
        ]

        outgoing_map: dict[str, list[tuple[str, str]]] = defaultdict(list)
        incoming_map: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for source_id, target_id, edge_type in edge_rows:
            outgoing_map[source_id].append((target_id, edge_type))
            incoming_map[target_id].append((source_id, edge_type))

        neighborhood_successes = await self._run_neighborhood_cases(
            retriever,
            outgoing_map,
            limit=_MAX_NEIGHBORHOOD_CASES,
        )
        contradiction_successes = await self._run_contradiction_cases(
            retriever,
            outgoing_map,
            incoming_map,
        )
        supersession_successes = await self._run_supersession_cases(
            retriever,
            outgoing_map,
        )

        report = LiveMemoryLifecycleAgentReport(
            agent_id=agent_id,
            graph_path=str(original_graph_path),
            node_count=node_count,
            edge_count=len(edge_rows),
            source_count=await self._count_scalar(db, "SELECT COUNT(*) FROM sources"),
            node_type_counts=dict(sorted(node_type_counts.items())),
            edge_type_counts=dict(sorted(edge_type_counts.items())),
            provenance_coverage=_metric_summary(provenance_values, seed_offset=10),
            average_sources_per_node=_metric_summary(source_count_values, seed_offset=20),
            edge_participation_rate=_metric_summary(
                edge_participation_values,
                seed_offset=30,
            ),
            trust_source_alignment_gap=_metric_summary(
                trust_gap_values,
                seed_offset=40,
            ),
            zero_access_rate=_metric_summary(zero_access_values, seed_offset=50),
            average_access_count=_metric_summary(access_count_values, seed_offset=60),
            canonical_rate=_metric_summary(canonical_values, seed_offset=70),
            archived_rate=_metric_summary(archived_values, seed_offset=80),
            neighborhood_recall=_traversal_metric(
                neighborhood_successes,
                seed_offset=90,
            ),
            contradiction_recall=_traversal_metric(
                contradiction_successes,
                seed_offset=100,
            ),
            supersession_recall=_traversal_metric(
                supersession_successes,
                seed_offset=110,
            ),
        )
        report.overall_status = _agent_status(report)
        report.notes = self._build_notes(report)

        return _AgentLifecycleInputs(
            report=report,
            provenance_values=provenance_values,
            source_count_values=source_count_values,
            edge_participation_values=edge_participation_values,
            trust_gap_values=trust_gap_values,
            zero_access_values=zero_access_values,
            access_count_values=access_count_values,
            canonical_values=canonical_values,
            archived_values=archived_values,
            neighborhood_successes=neighborhood_successes,
            contradiction_successes=contradiction_successes,
            supersession_successes=supersession_successes,
        )

    async def _run_neighborhood_cases(
        self,
        retriever: MemoryRetriever,
        outgoing_map: dict[str, list[tuple[str, str]]],
        *,
        limit: int,
    ) -> list[float]:
        successes: list[float] = []
        case_node_ids = [
            node_id for node_id, edges in sorted(outgoing_map.items()) if edges
        ][:limit]
        for node_id in case_node_ids:
            expected = {target_id for target_id, _ in outgoing_map[node_id]}
            visited = await retriever.retrieve_neighborhood(node_id, depth=1)
            found_ids = {node.id for node, _ in visited}
            successes.append(1.0 if expected.issubset(found_ids) else 0.0)
        return successes

    async def _run_contradiction_cases(
        self,
        retriever: MemoryRetriever,
        outgoing_map: dict[str, list[tuple[str, str]]],
        incoming_map: dict[str, list[tuple[str, str]]],
    ) -> list[float]:
        successes: list[float] = []
        candidate_ids = sorted(set(outgoing_map) | set(incoming_map))
        for node_id in candidate_ids:
            expected = {
                target_id
                for target_id, edge_type in outgoing_map.get(node_id, [])
                if edge_type == EdgeType.CONTRADICTS.value
            }
            expected |= {
                source_id
                for source_id, edge_type in incoming_map.get(node_id, [])
                if edge_type == EdgeType.CONTRADICTS.value
            }
            if not expected:
                continue
            found = await retriever.retrieve_contradictions(node_id)
            found_ids = {other.id for other, _ in found}
            successes.append(1.0 if expected.issubset(found_ids) else 0.0)
        return successes

    async def _run_supersession_cases(
        self,
        retriever: MemoryRetriever,
        outgoing_map: dict[str, list[tuple[str, str]]],
    ) -> list[float]:
        successes: list[float] = []
        for node_id in sorted(outgoing_map):
            supersedes_targets = [
                target_id
                for target_id, edge_type in outgoing_map[node_id]
                if edge_type == EdgeType.SUPERSEDES.value
            ]
            if len(supersedes_targets) != 1:
                continue
            chain = await retriever.retrieve_supersession_chain(node_id)
            chain_ids = {node.id for node in chain}
            successes.append(1.0 if supersedes_targets[0] in chain_ids else 0.0)
        return successes

    def _build_notes(self, report: LiveMemoryLifecycleAgentReport) -> list[str]:
        notes: list[str] = []
        if report.provenance_coverage.value < 0.95:
            notes.append("Not all memory nodes carry explicit provenance yet.")
        if report.edge_participation_rate.value < 0.75:
            notes.append("A sizeable slice of nodes are still structurally isolated.")
        if report.canonical_rate.value < 0.01:
            notes.append("Canonical promotion is still very sparse in this graph.")
        if report.archived_rate.value == 0.0:
            notes.append("No nodes are archived yet, so retention is still in an early phase.")
        if report.zero_access_rate.value > 0.9:
            notes.append("Most stored memories have not been re-accessed yet.")
        if report.trust_source_alignment_gap.value > 0.3:
            notes.append(
                "Node trust scores often diverge materially from average source reliability."
            )
        if report.contradiction_recall is None:
            notes.append("No contradiction-linked nodes were available for traversal checks.")
        if report.supersession_recall is None:
            notes.append("No single-step supersession chains were available for traversal checks.")
        return notes[:4]

    def _build_aggregate(
        self,
        entries: list[_AgentLifecycleInputs],
    ) -> LiveMemoryLifecycleAggregateReport:
        def flatten(attribute: str) -> list[float]:
            values: list[float] = []
            for entry in entries:
                values.extend(getattr(entry, attribute))
            return values

        return LiveMemoryLifecycleAggregateReport(
            provenance_coverage=_metric_summary(flatten("provenance_values"), seed_offset=200),
            average_sources_per_node=_metric_summary(
                flatten("source_count_values"),
                seed_offset=210,
            ),
            edge_participation_rate=_metric_summary(
                flatten("edge_participation_values"),
                seed_offset=220,
            ),
            trust_source_alignment_gap=_metric_summary(
                flatten("trust_gap_values"),
                seed_offset=230,
            ),
            zero_access_rate=_metric_summary(
                flatten("zero_access_values"),
                seed_offset=240,
            ),
            average_access_count=_metric_summary(
                flatten("access_count_values"),
                seed_offset=250,
            ),
            canonical_rate=_metric_summary(flatten("canonical_values"), seed_offset=260),
            archived_rate=_metric_summary(flatten("archived_values"), seed_offset=270),
            neighborhood_recall=_traversal_metric(
                flatten("neighborhood_successes"),
                seed_offset=280,
            ),
            contradiction_recall=_traversal_metric(
                flatten("contradiction_successes"),
                seed_offset=290,
            ),
            supersession_recall=_traversal_metric(
                flatten("supersession_successes"),
                seed_offset=300,
            ),
        )

    @staticmethod
    async def _count_scalar(db: aiosqlite.Connection, sql: str) -> int:
        async with db.execute(sql) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    @staticmethod
    async def _count_rows_by_key(
        db: aiosqlite.Connection,
        sql: str,
    ) -> dict[str, int]:
        async with db.execute(sql) as cursor:
            rows = await cursor.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    @staticmethod
    async def _average_source_reliability_by_node(
        db: aiosqlite.Connection,
    ) -> dict[str, float]:
        async with db.execute(
            """
            SELECT n.id, AVG(s.reliability) AS avg_reliability
            FROM nodes n
            JOIN node_sources ns ON ns.node_id = n.id
            JOIN sources s ON s.id = ns.source_id
            GROUP BY n.id
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return {str(row[0]): float(row[1]) for row in rows if row[1] is not None}

    @staticmethod
    async def _fetch_edge_rows(
        db: aiosqlite.Connection,
    ) -> list[tuple[str, str, str]]:
        async with db.execute(
            "SELECT source_id, target_id, edge_type FROM edges"
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            (str(row[0]), str(row[1]), str(row[2]))
            for row in rows
        ]


async def run_live_memory_target(
    target: Path,
    *,
    run_label: str = "",
) -> LiveMemoryLifecycleRunReport:
    """Run a read-only lifecycle audit over one or more live agent graphs."""
    return await LiveMemoryLifecycleRunner(target, run_label=run_label).run()


def interpret_live_memory_run_report(
    run_report: LiveMemoryLifecycleRunReport,
    output_path: Path | None = None,
) -> str:
    """Return a concise human-readable summary of a live memory lifecycle audit."""
    lines = ["Live memory lifecycle audit"]
    if run_report.run_label:
        lines.append(f"Run label: {run_report.run_label}")
    lines.append(f"Target: {run_report.target}")
    if output_path is not None:
        lines.append(f"Report: {output_path}")
    lines.append(
        "Agents scanned: "
        f"{run_report.agent_count} total "
        f"({run_report.passed_agent_count} pass, "
        f"{run_report.mixed_agent_count} mixed, "
        f"{run_report.failed_agent_count} fail)"
    )
    lines.append(
        "Overall: "
        + {
            BenchmarkStatus.PASS: "live graphs look structurally healthy on this audit.",
            BenchmarkStatus.MIXED: "live graphs show a mixed lifecycle picture.",
            BenchmarkStatus.FAIL: "live graphs show structural or traversal gaps that need attention.",
            BenchmarkStatus.NOT_APPLICABLE: "no live graph metrics were available.",
        }[run_report.overall_status]
    )
    lines.append("")
    lines.append("Aggregate lifecycle metrics")
    aggregate = run_report.aggregate
    lines.append(
        "- Provenance coverage: "
        + _format_metric_summary(aggregate.provenance_coverage)
    )
    lines.append(
        "- Sources per node: "
        + _format_metric_summary(aggregate.average_sources_per_node)
    )
    lines.append(
        "- Edge participation: "
        + _format_metric_summary(aggregate.edge_participation_rate)
    )
    lines.append(
        "- Trust/source gap: "
        + _format_metric_summary(aggregate.trust_source_alignment_gap)
        + " (lower is tighter)"
    )
    lines.append(
        "- Zero-access rate: "
        + _format_metric_summary(aggregate.zero_access_rate)
    )
    lines.append(
        "- Avg access count: "
        + _format_metric_summary(aggregate.average_access_count)
    )
    lines.append(
        "- Canonical rate / archived rate: "
        f"{_format_metric_summary(aggregate.canonical_rate)} / "
        f"{_format_metric_summary(aggregate.archived_rate)}"
    )
    for label, metric in (
        ("Direct-neighbor recall", aggregate.neighborhood_recall),
        ("Contradiction recall", aggregate.contradiction_recall),
        ("Supersession recall", aggregate.supersession_recall),
    ):
        if metric is not None:
            lines.append(
                f"- {label}: {_format_metric_summary(metric.success_rate)} "
                f"across {metric.case_count} cases"
            )
    lines.append(
        "- Note: this mode snapshots live `graph.db` files into temporary copies "
        "before auditing them, so it does not mutate the running agents."
    )

    for report in run_report.agent_reports:
        lines.append("")
        lines.append(
            f"- {report.agent_id} [{report.overall_status.value}]: "
            f"{report.node_count} nodes, {report.edge_count} edges, "
            f"provenance {_format_metric_summary(report.provenance_coverage)}, "
            f"edge participation {_format_metric_summary(report.edge_participation_rate)}, "
            f"zero-access {_format_metric_summary(report.zero_access_rate)}"
        )
        if report.notes:
            lines.append("  notes: " + "; ".join(report.notes[:2]))

    return "\n".join(lines)
