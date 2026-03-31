"""Tests for benchmark metric helpers."""
from __future__ import annotations

import pytest

from agentgolem.benchmarks.metrics import (
    CalibrationPoint,
    brier_score,
    expected_calibration_error,
    ndcg_at_k,
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
