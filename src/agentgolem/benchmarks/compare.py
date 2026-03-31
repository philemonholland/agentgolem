"""Comparison helpers for benchmark report files."""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from agentgolem.benchmarks.models import BenchmarkReport, BenchmarkRunReport, BenchmarkStatus
from agentgolem.benchmarks.runner import load_report


def format_report_comparison(report_paths: list[Path]) -> str:
    """Return a side-by-side comparison for one or more report JSON files."""
    grouped: dict[str, list[tuple[str, BenchmarkReport]]] = defaultdict(list)

    for report_path in report_paths:
        payload = load_report(report_path)
        for suite_report in _flatten_reports(payload):
            label = suite_report.run_label or report_path.stem
            grouped[suite_report.suite_name].append((label, suite_report))

    lines: list[str] = []
    for suite_name in sorted(grouped):
        lines.append(f"Suite: {suite_name}")
        rows = sorted(grouped[suite_name], key=_comparison_sort_key)
        for label, report in rows:
            retrieval = (
                f"retrieval_mrr={report.retrieval.actual.mean_reciprocal_rank.value:.3f}, "
                f"retrieval_delta_mrr={report.retrieval.delta.mean_reciprocal_rank.value:.3f}"
                if report.retrieval is not None
                else "retrieval_mrr=n/a, retrieval_delta_mrr=n/a"
            )
            trust = (
                f"trust_brier={report.trust.actual.brier_score.value:.3f}, "
                f"trust_delta_brier={report.trust.delta.brier_score.value:.3f}"
                if report.trust is not None
                else "trust_brier=n/a, trust_delta_brier=n/a"
            )
            error_recovery = (
                f"error_recovery_accuracy={report.error_recovery.actual.accuracy.value:.3f}, "
                f"error_recovery_delta_accuracy={report.error_recovery.delta.accuracy.value:.3f}"
                if report.error_recovery is not None
                else "error_recovery_accuracy=n/a, error_recovery_delta_accuracy=n/a"
            )
            lines.append(
                f"- {label}: overall={report.overall_status.value}, "
                f"retrieval={report.retrieval_status.value}, "
                f"trust={report.trust_status.value}, "
                f"error_recovery={report.error_recovery_status.value}, "
                f"{retrieval}, {trust}, {error_recovery}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for report comparison."""
    parser = argparse.ArgumentParser(
        description="Compare one or more AgentGolem benchmark report JSON files."
    )
    parser.add_argument(
        "reports",
        nargs="+",
        type=Path,
        help="Benchmark report JSON files to compare.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for report comparison."""
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.stdout.write(format_report_comparison(args.reports))
    return 0


def _flatten_reports(payload: BenchmarkReport | BenchmarkRunReport) -> list[BenchmarkReport]:
    if isinstance(payload, BenchmarkRunReport):
        return payload.suite_reports
    return [payload]


def _comparison_sort_key(item: tuple[str, BenchmarkReport]) -> tuple[int, float, float, str]:
    label, report = item
    retrieval_mrr = (
        report.retrieval.actual.mean_reciprocal_rank.value
        if report.retrieval is not None
        else -1.0
    )
    trust_brier = report.trust.actual.brier_score.value if report.trust is not None else 999.0
    return (_status_rank(report.overall_status), -retrieval_mrr, trust_brier, label)


def _status_rank(status: BenchmarkStatus) -> int:
    order = {
        BenchmarkStatus.PASS: 0,
        BenchmarkStatus.MIXED: 1,
        BenchmarkStatus.FAIL: 2,
        BenchmarkStatus.NOT_APPLICABLE: 3,
    }
    return order[status]


if __name__ == "__main__":
    raise SystemExit(main())
