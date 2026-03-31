"""Metric helpers for offline benchmark reports."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


@dataclass(frozen=True, slots=True)
class CalibrationPoint:
    """A prediction/observation pair for calibration metrics."""

    prediction: float
    observed: float


@dataclass(frozen=True, slots=True)
class BootstrapSummary:
    """Mean summary with a deterministic bootstrap confidence interval."""

    value: float
    ci_lower: float | None
    ci_upper: float | None
    confidence_level: float | None


def mean(values: Sequence[float]) -> float:
    """Return the arithmetic mean, or ``0.0`` for an empty sequence."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def bootstrap_mean(
    values: Sequence[float],
    *,
    resamples: int = 1000,
    seed: int = 0,
    confidence_level: float = 0.95,
) -> BootstrapSummary:
    """Return a mean plus percentile bootstrap confidence interval."""
    return bootstrap_statistic(
        values,
        statistic=mean,
        resamples=resamples,
        seed=seed,
        confidence_level=confidence_level,
    )


def bootstrap_statistic(
    values: Sequence[object],
    *,
    statistic: Callable[[Sequence[object]], float],
    resamples: int = 1000,
    seed: int = 0,
    confidence_level: float = 0.95,
) -> BootstrapSummary:
    """Return a statistic plus percentile bootstrap confidence interval."""
    if not values:
        return BootstrapSummary(
            value=0.0,
            ci_lower=None,
            ci_upper=None,
            confidence_level=confidence_level,
        )

    point_estimate = statistic(values)
    if len(values) == 1 or resamples <= 1:
        return BootstrapSummary(
            value=point_estimate,
            ci_lower=point_estimate,
            ci_upper=point_estimate,
            confidence_level=confidence_level,
        )

    sample_size = len(values)
    rng = random.Random(seed)
    bootstrap_estimates: list[float] = []
    for _ in range(resamples):
        sample = [values[rng.randrange(sample_size)] for _ in range(sample_size)]
        bootstrap_estimates.append(statistic(sample))
    bootstrap_estimates.sort()

    alpha = max(0.0, min(1.0, 1.0 - confidence_level))
    lower = _percentile(bootstrap_estimates, alpha / 2)
    upper = _percentile(bootstrap_estimates, 1.0 - (alpha / 2))
    return BootstrapSummary(
        value=point_estimate,
        ci_lower=lower,
        ci_upper=upper,
        confidence_level=confidence_level,
    )


def paired_deltas(actual_values: Sequence[float], baseline_values: Sequence[float]) -> list[float]:
    """Return per-case actual-minus-baseline deltas."""
    if len(actual_values) != len(baseline_values):
        raise ValueError("actual and baseline sequences must be the same length")
    return [
        actual - baseline
        for actual, baseline in zip(actual_values, baseline_values, strict=True)
    ]


def bootstrap_paired_statistic(
    actual_values: Sequence[object],
    baseline_values: Sequence[object],
    *,
    statistic: Callable[[Sequence[object]], float],
    resamples: int = 1000,
    seed: int = 0,
    confidence_level: float = 0.95,
) -> BootstrapSummary:
    """Return a paired statistic delta plus percentile bootstrap confidence interval."""
    if len(actual_values) != len(baseline_values):
        raise ValueError("actual and baseline sequences must be the same length")
    if not actual_values:
        return BootstrapSummary(
            value=0.0,
            ci_lower=None,
            ci_upper=None,
            confidence_level=confidence_level,
        )

    point_estimate = statistic(actual_values) - statistic(baseline_values)
    if len(actual_values) == 1 or resamples <= 1:
        return BootstrapSummary(
            value=point_estimate,
            ci_lower=point_estimate,
            ci_upper=point_estimate,
            confidence_level=confidence_level,
        )

    sample_size = len(actual_values)
    rng = random.Random(seed)
    bootstrap_estimates: list[float] = []
    for _ in range(resamples):
        indices = [rng.randrange(sample_size) for _ in range(sample_size)]
        actual_sample = [actual_values[index] for index in indices]
        baseline_sample = [baseline_values[index] for index in indices]
        bootstrap_estimates.append(
            statistic(actual_sample) - statistic(baseline_sample)
        )
    bootstrap_estimates.sort()

    alpha = max(0.0, min(1.0, 1.0 - confidence_level))
    lower = _percentile(bootstrap_estimates, alpha / 2)
    upper = _percentile(bootstrap_estimates, 1.0 - (alpha / 2))
    return BootstrapSummary(
        value=point_estimate,
        ci_lower=lower,
        ci_upper=upper,
        confidence_level=confidence_level,
    )


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


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    clamped = max(0.0, min(1.0, quantile))
    if len(values) == 1:
        return float(values[0])
    index = clamped * (len(values) - 1)
    lower_index = math.floor(index)
    upper_index = math.ceil(index)
    if lower_index == upper_index:
        return float(values[lower_index])
    lower_value = float(values[lower_index])
    upper_value = float(values[upper_index])
    weight = index - lower_index
    return lower_value + ((upper_value - lower_value) * weight)
