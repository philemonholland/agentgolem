"""Tests for benchmark metric helpers."""
from __future__ import annotations

import pytest

from agentgolem.benchmarks.metrics import (
    CalibrationPoint,
    bootstrap_mean,
    bootstrap_paired_statistic,
    brier_score,
    expected_calibration_error,
    ndcg_at_k,
    paired_deltas,
    precision_at_k,
    reciprocal_rank,
)


async def test_reciprocal_rank_returns_first_relevant_position():
    assert reciprocal_rank(["node-2"], ["node-1", "node-2", "node-3"]) == pytest.approx(0.5)


async def test_precision_at_k_penalizes_missing_hits():
    assert precision_at_k(["node-2"], ["node-1", "node-2"], 3) == pytest.approx(1 / 3)


async def test_ndcg_at_k_is_one_for_perfect_order():
    assert ndcg_at_k(["a", "b"], ["a", "b", "c"], 2) == pytest.approx(1.0)


async def test_brier_score_and_ece_capture_calibration_quality():
    points = [
        CalibrationPoint(prediction=0.9, observed=1.0),
        CalibrationPoint(prediction=0.1, observed=0.0),
        CalibrationPoint(prediction=0.8, observed=1.0),
        CalibrationPoint(prediction=0.2, observed=0.0),
    ]

    assert brier_score(points) == pytest.approx(0.025)
    assert expected_calibration_error(points, bins=5) == pytest.approx(0.15)


async def test_bootstrap_mean_returns_interval():
    summary = bootstrap_mean([0.0, 1.0, 1.0, 0.0], resamples=200, seed=7)

    assert summary.value == pytest.approx(0.5)
    assert summary.ci_lower is not None
    assert summary.ci_upper is not None
    assert summary.ci_lower <= summary.value <= summary.ci_upper


async def test_paired_deltas_preserve_casewise_difference():
    deltas = paired_deltas([1.0, 0.8, 0.2], [0.5, 0.5, 0.4])

    assert deltas == pytest.approx([0.5, 0.3, -0.2])


async def test_bootstrap_paired_statistic_returns_delta_interval():
    actual = [
        CalibrationPoint(prediction=0.9, observed=1.0),
        CalibrationPoint(prediction=0.8, observed=1.0),
        CalibrationPoint(prediction=0.2, observed=0.0),
        CalibrationPoint(prediction=0.1, observed=0.0),
    ]
    baseline = [
        CalibrationPoint(prediction=0.7, observed=1.0),
        CalibrationPoint(prediction=0.7, observed=1.0),
        CalibrationPoint(prediction=0.3, observed=0.0),
        CalibrationPoint(prediction=0.3, observed=0.0),
    ]

    summary = bootstrap_paired_statistic(
        actual,
        baseline,
        statistic=brier_score,
        resamples=200,
        seed=11,
    )

    assert summary.value < 0.0
    assert summary.ci_lower is not None
    assert summary.ci_upper is not None
