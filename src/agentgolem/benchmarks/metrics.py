"""Metric helpers for offline benchmark reports."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class CalibrationPoint:
    """A prediction/observation pair for calibration metrics."""

    prediction: float
    observed: float


def mean(values: Sequence[float]) -> float:
    """Return the arithmetic mean, or ``0.0`` for an empty sequence."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def reciprocal_rank(relevant_ids: Sequence[str], ranked_ids: Sequence[str]) -> float:
    """Return reciprocal rank for the first relevant item in ``ranked_ids``."""
    relevant = set(relevant_ids)
    for index, node_id in enumerate(ranked_ids, start=1):
        if node_id in relevant:
            return 1.0 / index
    return 0.0


def precision_at_k(relevant_ids: Sequence[str], ranked_ids: Sequence[str], k: int) -> float:
    """Return precision@k using binary relevance."""
    if k <= 0:
        raise ValueError("k must be positive")
    relevant = set(relevant_ids)
    top_k = ranked_ids[:k]
    hits = sum(1 for node_id in top_k if node_id in relevant)
    return hits / k


def ndcg_at_k(relevant_ids: Sequence[str], ranked_ids: Sequence[str], k: int) -> float:
    """Return NDCG@k using binary relevance labels."""
    if k <= 0:
        raise ValueError("k must be positive")

    relevant = set(relevant_ids)
    if not relevant:
        return 0.0

    dcg = 0.0
    for rank, node_id in enumerate(ranked_ids[:k], start=1):
        if node_id in relevant:
            dcg += 1.0 / math.log2(rank + 1)

    ideal_hits = min(len(relevant), k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    if ideal_dcg == 0.0:
        return 0.0
    return dcg / ideal_dcg


def brier_score(points: Sequence[CalibrationPoint]) -> float:
    """Return the mean squared error of probabilistic predictions."""
    if not points:
        return 0.0

    squared_errors = []
    for point in points:
        prediction = _validate_probability(point.prediction)
        observed = _validate_probability(point.observed)
        squared_errors.append((prediction - observed) ** 2)
    return mean(squared_errors)


def expected_calibration_error(points: Sequence[CalibrationPoint], bins: int = 10) -> float:
    """Return expected calibration error using equal-width bins."""
    if bins <= 0:
        raise ValueError("bins must be positive")
    if not points:
        return 0.0

    bin_counts = [0] * bins
    bin_prediction_sums = [0.0] * bins
    bin_observed_sums = [0.0] * bins

    for point in points:
        prediction = _validate_probability(point.prediction)
        observed = _validate_probability(point.observed)
        index = min(int(prediction * bins), bins - 1)
        bin_counts[index] += 1
        bin_prediction_sums[index] += prediction
        bin_observed_sums[index] += observed

    total_points = len(points)
    error = 0.0
    for index, count in enumerate(bin_counts):
        if count == 0:
            continue
        avg_prediction = bin_prediction_sums[index] / count
        avg_observed = bin_observed_sums[index] / count
        error += abs(avg_prediction - avg_observed) * (count / total_points)
    return error


def _validate_probability(value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"Probability must be between 0.0 and 1.0, got {value}")
    return value
