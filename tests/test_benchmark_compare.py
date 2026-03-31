"""Tests for benchmark report comparison."""
from __future__ import annotations

import pytest

from agentgolem.benchmarks.compare import format_report_comparison
from agentgolem.benchmarks.live_memory import run_live_memory_target
from agentgolem.benchmarks.runner import BenchmarkRunner, write_report
from tests.test_benchmark_live_memory import _seed_live_graph
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


async def test_format_report_comparison_rejects_live_memory_reports(tmp_path):
    data_root = tmp_path / "data"
    await _seed_live_graph(
        data_root / "council_alpha" / "memory" / "graph.db",
        source_id="source-alpha",
        include_orphan=False,
    )
    report = await run_live_memory_target(data_root, run_label="live")
    path = tmp_path / "live.json"
    write_report(report, path)

    with pytest.raises(ValueError, match="Live memory lifecycle reports cannot be compared"):
        format_report_comparison([path])
