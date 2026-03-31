# Benchmarking AgentGolem

AgentGolem already exposes rich telemetry, but telemetry alone does not answer
the main question: does the system retrieve the right memories, assign useful
trust scores, and improve relative to simpler alternatives?

The offline benchmark harness in `src\agentgolem\benchmarks\` turns that question
into repeatable runs with labeled suites, explicit metrics, and JSON reports.

## What is implemented now

The current benchmark stack covers three dimensions:

- **Retrieval quality**
  - metrics: `MRR`, `Precision@k`, `NDCG@k`
  - comparison baseline: text-only ranking without trust/usefulness weighting

- **Trust calibration**
  - metrics: `Brier score`, `Expected Calibration Error`
  - comparison baseline: constant `0.5` trust for every labeled node

- **Error recovery**
  - metrics: expected-case accuracy, failure-handling rate, recovery rate
  - comparison baseline: naive always-allow / always-success behavior

This is intentionally offline-first. The harness seeds an isolated temporary
SQLite memory store, runs benchmark cases against it, and writes a stable JSON
report without touching live agent state.

## Existing measurable surfaces

The current benchmark work builds on signals that already exist in the codebase:

- `src\agentgolem\memory\retrieval.py`
  - retrieval ranking logic and source-reliability-aware scoring
- `src\agentgolem\trust\bayesian.py`
  - probabilistic trust scores that can be checked for calibration
- `src\agentgolem\memory\store.py`
  - queryable memory graph state for deterministic fixtures
- `src\agentgolem\logging\audit.py`
  - append-only mutation traces for later robustness and autonomy benchmarks
- `src\agentgolem\dashboard\replay.py`
  - replay-ready traces for future scenario-driven evaluation
- `src\agentgolem\runtime\loop.py`
  - tool use, browsing, approvals, and multi-agent execution surfaces for later
    benchmark tracks

## Suite format

A benchmark suite is a JSON file containing:

- `sources`
  - source definitions with reliability values
- `nodes`
  - seeded memory nodes, including trust/usefulness fields and linked sources
- `edges`
  - optional graph edges for future scenario expansion
- `retrieval_cases`
  - queries plus labeled relevant node ids
- `trust_cases`
  - node ids plus expected reliability labels

See `benchmarks\sample_suite.json` for a minimal working example.
The directory runner can execute every `*.json` suite under `benchmarks\` in one pass.

## Running the harness

```powershell
benchmark.bat
benchmark.bat benchmarks\sample_suite.json --output data\benchmarks\latest_report.json
python -m agentgolem.benchmarks benchmarks
python -m agentgolem.benchmarks benchmarks --output data\benchmarks\latest_run.json --interpret
python -m agentgolem.benchmarks benchmarks --output data\benchmarks\gpt-5.4.json --label gpt-5.4
python -m agentgolem.benchmarks benchmarks --output data\benchmarks\claude-sonnet-4.6.json --label claude-sonnet-4.6
python -m agentgolem.benchmarks.compare data\benchmarks\gpt-5.4.json data\benchmarks\claude-sonnet-4.6.json
```

`benchmark.bat` is the one-click Windows launcher. With no arguments it runs the
whole `benchmarks\` suite directory, writes `data\benchmarks\latest_run.json`, and prints a
human-readable interpretation before pausing so the result stays visible.

Without `--interpret`, the Python CLI keeps its JSON-oriented behavior.
With `--interpret`, it prints a concise verdict against the configured baselines.

The compare command is meant for labeled runs such as `gpt-5.4`,
`claude-sonnet-4.6`, or `deepseek-reasoner`. The current offline suites mostly
exercise retrieval, trust, and deterministic recovery logic, so model-vs-model
comparisons are still limited, but the reporting pipeline is now ready for
broader future suites.

## Report shape

Each report includes:

- suite metadata
- aggregate retrieval metrics for the current system and the text-only baseline
- aggregate trust metrics for the current system and the constant-trust baseline
- per-case outputs showing retrieved ids or trust predictions

This makes it easy to diff benchmark runs across commits or settings snapshots.

## How to read the scores

- `pass`
  - better than the configured baseline with no metric losses
- `mixed`
  - some metrics improved, but not cleanly enough to call it a full win
- `fail`
  - not beating the baseline

Metric guide:

- `MRR`
  - where the first relevant memory appears
  - `1.0` means first place, `0.5` means second place
- `Precision@k`
  - fraction of the top-`k` results that were actually relevant
  - higher is better
- `NDCG@k`
  - overall ranking quality within the top `k`
  - `1.0` is ideal ordering
- `Brier score`
  - mean squared error of trust probabilities
  - lower is better, `0.0` is perfect
- `ECE`
  - calibration gap between predicted trust and observed reliability
  - lower is better, `0.0` is perfect
- `Error recovery accuracy`
  - fraction of failure/recovery scenarios handled the way the suite expected
  - higher is better

## Known gaps and next layers

The harness now covers the most objective starting point, but several benchmark
tracks still need to be added:

- repeated-mistake avoidance and richer recovery sequences
- autonomy usefulness and approval-aware productivity
- vow adherence / alignment drift
- multi-agent coordination quality and personality differentiation
- latency, token cost, and long-run degradation tests

Those later tracks can reuse the same suite/report pattern instead of creating a
separate evaluation stack.
