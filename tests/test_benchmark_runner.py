"""Tests for the offline benchmark runner."""
from __future__ import annotations

import json

import pytest

from agentgolem.benchmarks.models import (
    BenchmarkNodeSpec,
    BenchmarkSourceSpec,
    BenchmarkSuite,
    RetrievalBenchmarkCase,
    TrustCalibrationCase,
)
from agentgolem.benchmarks.runner import (
    BenchmarkRunner,
    interpret_report,
    load_suite,
    write_report,
)
from agentgolem.memory.models import NodeType, SourceKind


def _suite() -> BenchmarkSuite:
    return BenchmarkSuite(
        name="test-suite",
        description="Regression suite for retrieval and trust.",
        sources=[
            BenchmarkSourceSpec(
                id="trusted-human",
                kind=SourceKind.HUMAN,
                origin="operator",
                reliability=0.95,
            ),
            BenchmarkSourceSpec(
                id="shaky-web",
                kind=SourceKind.WEB,
                origin="unknown-blog",
                reliability=0.2,
            ),
        ],
        nodes=[
            BenchmarkNodeSpec(
                id="python-correct",
                text="Python typing async patterns",
                search_text="python typing async",
                type=NodeType.FACT,
                base_usefulness=0.9,
                trustworthiness=0.9,
                salience=0.4,
                source_ids=["trusted-human"],
            ),
            BenchmarkNodeSpec(
                id="python-rumor",
                text="Python typing async patterns",
                search_text="python typing async",
                type=NodeType.INTERPRETATION,
                base_usefulness=0.2,
                trustworthiness=0.2,
                salience=0.95,
                source_ids=["shaky-web"],
            ),
            BenchmarkNodeSpec(
                id="rust-correct",
                text="Rust memory safety ownership",
                search_text="rust memory safety",
                type=NodeType.FACT,
                base_usefulness=0.85,
                trustworthiness=0.85,
                salience=0.45,
                source_ids=["trusted-human"],
            ),
            BenchmarkNodeSpec(
                id="rust-rumor",
                text="Rust memory safety ownership",
                search_text="rust memory safety",
                type=NodeType.INTERPRETATION,
                base_usefulness=0.15,
                trustworthiness=0.15,
                salience=0.9,
                source_ids=["shaky-web"],
            ),
        ],
        retrieval_cases=[
            RetrievalBenchmarkCase(
                id="python-query",
                query="python typing async",
                relevant_node_ids=["python-correct"],
                top_k=2,
            ),
            RetrievalBenchmarkCase(
                id="rust-query",
                query="rust memory safety",
                relevant_node_ids=["rust-correct"],
                top_k=2,
            ),
        ],
        trust_cases=[
            TrustCalibrationCase(
                id="python-correct-trust",
                node_id="python-correct",
                expected_reliable=True,
            ),
            TrustCalibrationCase(
                id="python-rumor-trust",
                node_id="python-rumor",
                expected_reliable=False,
            ),
            TrustCalibrationCase(
                id="rust-correct-trust",
                node_id="rust-correct",
                expected_reliable=True,
            ),
            TrustCalibrationCase(
                id="rust-rumor-trust",
                node_id="rust-rumor",
                expected_reliable=False,
            ),
        ],
    )


async def test_benchmark_runner_scores_against_baselines(tmp_path):
    runner = BenchmarkRunner(_suite())

    report = await runner.run()

    assert report.retrieval is not None
    assert report.retrieval.actual.mean_reciprocal_rank == pytest.approx(1.0)
    assert report.retrieval.baseline.mean_reciprocal_rank == pytest.approx(0.5)
    assert (
        report.retrieval.actual.mean_reciprocal_rank
        > report.retrieval.baseline.mean_reciprocal_rank
    )

    assert report.trust is not None
    assert report.trust.actual.brier_score == pytest.approx(0.02375)
    assert report.trust.constant_baseline.brier_score == pytest.approx(0.25)
    assert report.trust.actual.brier_score < report.trust.constant_baseline.brier_score

    output_path = tmp_path / "report.json"
    write_report(report, output_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["suite_name"] == "test-suite"
    assert payload["retrieval"]["case_count"] == 2


async def test_interpret_report_describes_benchmark_result():
    report = await BenchmarkRunner(_suite()).run()

    summary = interpret_report(report)

    assert "Retrieval (2 cases)" in summary
    assert "retrieval ranking is helping on this suite." in summary
    assert "trust calibration is mixed on this suite." in summary
    assert "this suite shows a mixed picture" in summary


async def test_load_suite_reads_json_file(tmp_path):
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_suite().model_dump(mode="json")), encoding="utf-8")

    suite = load_suite(suite_path)

    assert suite.name == "test-suite"
    assert len(suite.retrieval_cases) == 2
