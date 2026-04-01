"""Tests for self-benchmark evaluation (Meta-Harness Phase 3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgolem.benchmarks.self_eval import (
    SelfBenchmarkResult,
    append_self_benchmark,
    compute_health_score,
    format_self_benchmark_for_calibration,
    load_self_benchmarks,
)


# ── 1. SelfBenchmarkResult round-trip ─────────────────────────────────

def test_result_round_trip():
    r = SelfBenchmarkResult(
        agent_name="Anvaya",
        timestamp="2026-01-01T00:00:00+00:00",
        retrieval_precision=0.75,
        retrieval_queries_tested=10,
        retrieval_hits=7,
        retrieval_misses=3,
        trust_coherence=0.8,
        trust_nodes_sampled=50,
        avg_context_tokens=3500.0,
        avg_retrieval_hit_rate=0.45,
        productive_action_rate=0.7,
        health_score=0.65,
    )
    d = r.to_dict()
    r2 = SelfBenchmarkResult.from_dict(d)
    assert r2.agent_name == "Anvaya"
    assert r2.retrieval_precision == 0.75
    assert r2.health_score == 0.65


# ── 2. Health score computation ───────────────────────────────────────

def test_health_score_perfect():
    r = SelfBenchmarkResult(
        agent_name="test",
        retrieval_precision=1.0,
        trust_coherence=1.0,
        avg_context_tokens=0.0,
        productive_action_rate=1.0,
    )
    score = compute_health_score(r)
    assert score == pytest.approx(1.0, abs=0.01)


def test_health_score_zero():
    r = SelfBenchmarkResult(
        agent_name="test",
        retrieval_precision=0.0,
        trust_coherence=0.0,
        avg_context_tokens=8000.0,
        productive_action_rate=0.0,
    )
    score = compute_health_score(r)
    assert score == pytest.approx(0.0, abs=0.01)


def test_health_score_mixed():
    r = SelfBenchmarkResult(
        agent_name="test",
        retrieval_precision=0.5,
        trust_coherence=0.6,
        avg_context_tokens=4000.0,
        productive_action_rate=0.7,
    )
    score = compute_health_score(r)
    assert 0.0 < score < 1.0


# ── 3. Persistence ───────────────────────────────────────────────────

def test_append_and_load(tmp_path: Path):
    r1 = SelfBenchmarkResult(agent_name="a", health_score=0.5)
    r2 = SelfBenchmarkResult(agent_name="a", health_score=0.7)
    append_self_benchmark(r1, tmp_path)
    append_self_benchmark(r2, tmp_path)
    loaded = load_self_benchmarks(tmp_path, limit=10)
    assert len(loaded) == 2
    assert loaded[0].health_score == 0.5
    assert loaded[1].health_score == 0.7


def test_load_empty(tmp_path: Path):
    loaded = load_self_benchmarks(tmp_path)
    assert loaded == []


def test_load_with_limit(tmp_path: Path):
    for i in range(10):
        append_self_benchmark(
            SelfBenchmarkResult(agent_name="a", health_score=i / 10),
            tmp_path,
        )
    loaded = load_self_benchmarks(tmp_path, limit=3)
    assert len(loaded) == 3
    # Should be the most recent 3
    assert loaded[0].health_score == pytest.approx(0.7)


# ── 4. Calibration formatting ────────────────────────────────────────

def test_format_for_calibration():
    r = SelfBenchmarkResult(
        agent_name="test",
        health_score=0.65,
        retrieval_precision=0.75,
        retrieval_hits=7,
        retrieval_queries_tested=10,
        trust_coherence=0.8,
        avg_context_tokens=3500.0,
        avg_retrieval_hit_rate=0.45,
        productive_action_rate=0.7,
    )
    text = format_self_benchmark_for_calibration(r)
    assert "SELF-BENCHMARK" in text
    assert "0.65" in text
    assert "75%" in text


def test_format_with_trend():
    prev = SelfBenchmarkResult(agent_name="test", health_score=0.5)
    curr = SelfBenchmarkResult(agent_name="test", health_score=0.65)
    text = format_self_benchmark_for_calibration(curr, [prev, curr])
    assert "📈" in text
    assert "+0.15" in text


def test_format_declining_trend():
    prev = SelfBenchmarkResult(agent_name="test", health_score=0.8)
    curr = SelfBenchmarkResult(agent_name="test", health_score=0.6)
    text = format_self_benchmark_for_calibration(curr, [prev, curr])
    assert "📉" in text


# ── 5. Malformed data resilience ─────────────────────────────────────

def test_load_with_malformed_lines(tmp_path: Path):
    path = tmp_path / "self_benchmarks.jsonl"
    path.write_text(
        '{"agent_name": "a", "health_score": 0.5}\n'
        "not json\n"
        '{"agent_name": "b", "health_score": 0.7}\n',
        encoding="utf-8",
    )
    loaded = load_self_benchmarks(tmp_path)
    assert len(loaded) == 2


# ── 6. from_dict with unknown keys ───────────────────────────────────

def test_from_dict_ignores_unknown():
    d = {"agent_name": "test", "health_score": 0.5, "unknown_field": 42}
    r = SelfBenchmarkResult.from_dict(d)
    assert r.agent_name == "test"
    assert r.health_score == 0.5
