"""Tests for benchmark report comparison."""
from __future__ import annotations

from agentgolem.benchmarks.compare import format_report_comparison
from agentgolem.benchmarks.runner import BenchmarkRunner, write_report
from tests.test_benchmark_runner import _suite


async def test_format_report_comparison_includes_run_labels(tmp_path):
    report_one = await BenchmarkRunner(_suite(), run_label="gpt-5.4").run()
    report_two = await BenchmarkRunner(_suite(), run_label="claude-sonnet-4.6").run()

    path_one = tmp_path / "gpt.json"
    path_two = tmp_path / "claude.json"
    write_report(report_one, path_one)
    write_report(report_two, path_two)

    summary = format_report_comparison([path_one, path_two])

    assert "Suite: test-suite" in summary
    assert "gpt-5.4" in summary
    assert "claude-sonnet-4.6" in summary
