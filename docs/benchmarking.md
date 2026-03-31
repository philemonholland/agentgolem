# Benchmarking AgentGolem

AgentGolem already exposes rich telemetry, but telemetry alone does not answer
the main question: does the system retrieve the right memories, assign useful
trust scores, and improve relative to simpler alternatives?

The offline benchmark harness in `src\agentgolem\benchmarks\` turns that question
into repeatable runs with labeled suites, explicit metrics, and JSON reports.

## What is implemented now

The first benchmark slice covers two dimensions:

- **Retrieval quality**
  - metrics: `MRR`, `Precision@k`, `NDCG@k`
  - comparison baseline: text-only ranking without trust/usefulness weighting

- **Trust calibration**
  - metrics: `Brier score`, `Expected Calibration Error`
  - comparison baseline: constant `0.5` trust for every labeled node

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

## Running the harness

```powershell
benchmark.bat
benchmark.bat benchmarks\sample_suite.json --output data\benchmarks\latest_report.json
python -m agentgolem.benchmarks benchmarks\sample_suite.json
python -m agentgolem.benchmarks benchmarks\sample_suite.json --output data\benchmarks\sample_report.json
python -m agentgolem.benchmarks benchmarks\sample_suite.json --output data\benchmarks\sample_report.json --interpret
```

`benchmark.bat` is the one-click Windows launcher. With no arguments it runs the
default sample suite, writes `data\benchmarks\latest_report.json`, and prints a
human-readable interpretation before pausing so the result stays visible.

Without `--interpret`, the Python CLI keeps its JSON-oriented behavior.
With `--interpret`, it prints a concise verdict against the configured baselines.

## Report shape

Each report includes:

- suite metadata
- aggregate retrieval metrics for the current system and the text-only baseline
- aggregate trust metrics for the current system and the constant-trust baseline
- per-case outputs showing retrieved ids or trust predictions

This makes it easy to diff benchmark runs across commits or settings snapshots.

## Known gaps and next layers

The harness now covers the most objective starting point, but several benchmark
tracks still need to be added:

- error recovery and repeated-mistake avoidance
- autonomy usefulness and approval-aware productivity
- vow adherence / alignment drift
- multi-agent coordination quality and personality differentiation
- latency, token cost, and long-run degradation tests

Those later tracks can reuse the same suite/report pattern instead of creating a
separate evaluation stack.
