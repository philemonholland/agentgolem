"""Tests for the offline benchmark runner."""
from __future__ import annotations

import json

import pytest

from agentgolem.benchmarks.models import (
    BenchmarkNodeSpec,
    BenchmarkRunReport,
    BenchmarkSourceSpec,
    BenchmarkStatus,
    BenchmarkSuite,
    ErrorRecoveryBenchmarkCase,
    ErrorRecoveryScenario,
    RetrievalBenchmarkCase,
    TrustCalibrationCase,
)
from agentgolem.benchmarks.presets import (
    build_robust_error_recovery_suite,
    build_robust_retrieval_suite,
    build_robust_trust_suite,
    load_preset_suites,
)
from agentgolem.benchmarks.runner import (
    BenchmarkRunner,
    _run_from_args,
    build_parser,
    interpret_report,
    interpret_run_report,
    load_report,
    load_suite,
    run_preset,
    run_target,
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
                reliability=0.88,
            ),
            BenchmarkSourceSpec(
                id="shaky-web",
                kind=SourceKind.WEB,
                origin="unknown-blog",
                reliability=0.28,
            ),
        ],
        nodes=[
            BenchmarkNodeSpec(
                id="python-correct",
                text="Python typing async patterns",
                search_text="python typing async",
                type=NodeType.FACT,
                base_usefulness=0.9,
                trustworthiness=0.96,
                salience=0.4,
                source_ids=["trusted-human"],
            ),
            BenchmarkNodeSpec(
                id="python-rumor",
                text="Python typing async patterns",
                search_text="python typing async",
                type=NodeType.INTERPRETATION,
                base_usefulness=0.2,
                trustworthiness=0.05,
                salience=0.95,
                source_ids=["shaky-web"],
            ),
            BenchmarkNodeSpec(
                id="rust-correct",
                text="Rust memory safety ownership",
                search_text="rust memory safety",
                type=NodeType.FACT,
                base_usefulness=0.85,
                trustworthiness=0.92,
                salience=0.45,
                source_ids=["trusted-human"],
            ),
            BenchmarkNodeSpec(
                id="rust-rumor",
                text="Rust memory safety ownership",
                search_text="rust memory safety",
                type=NodeType.INTERPRETATION,
                base_usefulness=0.15,
                trustworthiness=0.08,
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


def _error_recovery_suite() -> BenchmarkSuite:
    return BenchmarkSuite(
        name="error-recovery-suite",
        description="Regression suite for recovery handling.",
        error_recovery_cases=[
            ErrorRecoveryBenchmarkCase(
                id="browser-404",
                scenario=ErrorRecoveryScenario.BROWSER_FETCH_RESULT,
                url="https://example.com/missing",
                status_code=404,
                html="<html><body>Not found</body></html>",
                expected_success=False,
            ),
            ErrorRecoveryBenchmarkCase(
                id="browser-200",
                scenario=ErrorRecoveryScenario.BROWSER_FETCH_RESULT,
                url="https://example.com/ok",
                status_code=200,
                html="<html><body>Working page</body></html>",
                expected_success=True,
            ),
            ErrorRecoveryBenchmarkCase(
                id="unverified-browse",
                scenario=ErrorRecoveryScenario.EMBEDDED_BROWSE_GUARD,
                url="https://www.lesswrong.com/posts/7XqRZ7jotkaqKjwuK/interruptibility",
                expected_success=False,
            ),
            ErrorRecoveryBenchmarkCase(
                id="known-browse",
                scenario=ErrorRecoveryScenario.EMBEDDED_BROWSE_GUARD,
                url="https://www.lesswrong.com/tag/ai",
                known_urls=["https://www.lesswrong.com/tag/ai"],
                expected_success=True,
            ),
        ],
    )


async def test_benchmark_runner_scores_against_baselines(tmp_path):
    runner = BenchmarkRunner(_suite())

    report = await runner.run()

    assert report.retrieval is not None
    assert report.retrieval.actual.mean_reciprocal_rank.value == pytest.approx(1.0)
    assert report.retrieval.baseline.mean_reciprocal_rank.value == pytest.approx(0.5)
    assert (
        report.retrieval.actual.mean_reciprocal_rank.value
        > report.retrieval.baseline.mean_reciprocal_rank.value
    )
    assert report.retrieval.baseline_name == "lexical_salience_no_trust"
    assert report.retrieval.delta.mean_reciprocal_rank.value == pytest.approx(0.5)

    assert report.trust is not None
    assert report.trust.actual.brier_score.value == pytest.approx(0.004225)
    assert report.trust.baseline.brier_score.value == pytest.approx(0.0464)
    assert report.trust.actual.brier_score.value < report.trust.baseline.brier_score.value
    assert report.trust.baseline_name == "source_reliability_prior"
    assert report.retrieval_status == BenchmarkStatus.MIXED
    assert report.trust_status == BenchmarkStatus.PASS
    assert report.overall_status == BenchmarkStatus.MIXED

    output_path = tmp_path / "report.json"
    write_report(report, output_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["suite_name"] == "test-suite"
    assert payload["retrieval"]["case_count"] == 2
    assert payload["retrieval"]["baseline_name"] == "lexical_salience_no_trust"


async def test_interpret_report_describes_benchmark_result():
    report = await BenchmarkRunner(_suite(), run_label="gpt-5.4").run()

    summary = interpret_report(report)

    assert "Retrieval (2 cases)" in summary
    assert "Run label: gpt-5.4" in summary
    assert "Baseline: lexical_salience_no_trust" in summary
    assert "Baseline: source_reliability_prior" in summary
    assert "retrieval ranking is mixed on this suite." in summary
    assert "trust scores are better calibrated than the stronger source-aware baseline." in summary
    assert "this suite shows a mixed picture" in summary


async def test_error_recovery_suite_scores_above_baseline():
    report = await BenchmarkRunner(_error_recovery_suite()).run()

    assert report.error_recovery is not None
    assert report.error_recovery.actual.accuracy.value == pytest.approx(1.0)
    assert report.error_recovery.baseline.accuracy.value == pytest.approx(0.5)
    assert report.error_recovery_status == BenchmarkStatus.MIXED

    summary = interpret_report(report)
    assert "Error recovery (4 cases)" in summary
    assert "error recovery is mixed on this suite." in summary


async def test_run_target_directory_aggregates_multiple_suites(tmp_path):
    suite_path_1 = tmp_path / "suite-one.json"
    suite_path_2 = tmp_path / "suite-two.json"
    suite_path_1.write_text(json.dumps(_suite().model_dump(mode="json")), encoding="utf-8")

    suite_two = _suite().model_copy(update={"name": "test-suite-2"})
    suite_path_2.write_text(json.dumps(suite_two.model_dump(mode="json")), encoding="utf-8")

    payload = await run_target(tmp_path, run_label="claude-sonnet-4.6")

    assert isinstance(payload, BenchmarkRunReport)
    assert payload.run_label == "claude-sonnet-4.6"
    assert payload.suite_count == 2
    assert payload.mixed_suite_count == 2
    assert payload.passed_suite_count == 0

    summary = interpret_run_report(payload)
    assert "Suite results: 2 total" in summary
    assert "test-suite [mixed]" in summary
    assert "test-suite-2 [mixed]" in summary

    output_path = tmp_path / "aggregate-report.json"
    write_report(payload, output_path)
    loaded = load_report(output_path)
    assert isinstance(loaded, BenchmarkRunReport)
    assert loaded.suite_count == 2


async def test_run_from_args_rejects_conflicting_suite_and_preset() -> None:
    args = build_parser().parse_args(["benchmarks\\sample_suite.json", "--preset", "robust"])
    with pytest.raises(ValueError, match="Cannot specify both suite path and --preset"):
        await _run_from_args(args)


async def test_run_from_args_rejects_live_data_with_preset() -> None:
    args = build_parser().parse_args(["--live-data", "data", "--preset", "robust"])
    with pytest.raises(ValueError, match="Cannot combine --live-data"):
        await _run_from_args(args)


async def test_load_suite_reads_json_file(tmp_path):
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_suite().model_dump(mode="json")), encoding="utf-8")

    suite = load_suite(suite_path)

    assert suite.name == "test-suite"
    assert len(suite.retrieval_cases) == 2


async def test_robust_preset_builders_meet_requested_scale():
    retrieval = build_robust_retrieval_suite()
    trust = build_robust_trust_suite()
    recovery = build_robust_error_recovery_suite()

    assert len(retrieval.retrieval_cases) >= 50
    assert any("multi_relevant" in case.tags for case in retrieval.retrieval_cases)
    assert len(trust.trust_cases) >= 50
    assert len(recovery.error_recovery_cases) >= 50


async def test_load_preset_suites_returns_robust_bundle():
    suites = load_preset_suites("robust")

    assert [suite.name for suite in suites] == [
        "robust-retrieval-depth",
        "robust-trust-depth",
        "robust-error-recovery-depth",
    ]


async def test_run_preset_executes_robust_bundle():
    payload = await run_preset("robust", run_label="robust-regression")

    assert isinstance(payload, BenchmarkRunReport)
    assert payload.target == "preset:robust"
    assert payload.suite_count == 3
