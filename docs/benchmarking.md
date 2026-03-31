# Benchmarking AgentGolem

AgentGolem already exposes rich telemetry, but telemetry alone does not answer
the main question: does the system retrieve the right memories, assign useful
trust scores, recover from failures, and beat stronger alternatives in a way
that still looks robust once uncertainty is considered?

The offline benchmark harness in `src\agentgolem\benchmarks\` turns that
question into repeatable runs with labeled suites, deterministic presets, and
JSON reports.

## What is implemented now

The current benchmark stack covers three dimensions, each with a deterministic
`robust` preset plus optional JSON smoke suites:

- **Retrieval quality**
  - metrics: `MRR`, `Precision@k`, `NDCG@k`
  - robust preset: `60` hard cases
  - baseline: `lexical_salience_no_trust`
  - difficulty features: larger candidate pools, multiple relevant answers,
    adversarial near-duplicates, and salience traps

- **Trust calibration**
  - metrics: `Brier score`, `Expected Calibration Error`
  - robust preset: `120` imbalanced cases
  - baseline: `source_reliability_prior`
  - difficulty features: reliable and unreliable exceptions, mixed-source cases,
    and stronger baselines than constant `0.5`

- **Error recovery**
  - metrics: expected-case accuracy, failure-handling rate, recovery rate
  - robust preset: `60` deterministic cases
  - baseline: `always_allow_or_succeed`
  - difficulty features: HTTP failures, timeout-style failures, discovered URLs,
    guessed URLs, and near-match browse traps

Aggregate reports now include:

- point estimates;
- bootstrap confidence intervals;
- actual-minus-baseline deltas;
- confidence-aware `pass` / `mixed` / `fail` verdicts.

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

## Suite formats

A benchmark suite can come from either:

- a JSON file for smoke tests or custom cases; or
- a deterministic preset generated in `src\agentgolem\benchmarks\presets.py`.

JSON suites support:

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
- `error_recovery_cases`
  - deterministic browse/fetch scenarios with expected outcomes
- `bootstrap_resamples`, `bootstrap_seed`, `confidence_level`
  - reproducible uncertainty settings for aggregate metrics

See `benchmarks\sample_suite.json` for a minimal working example. The directory
runner can execute every `*.json` suite under `benchmarks\` in one pass. The
`robust` preset is the default “real benchmark” path.

## Running the harness

```powershell
benchmark.bat
python -m agentgolem.benchmarks --preset robust
python -m agentgolem.benchmarks --preset robust --output data\benchmarks\robust_run.json --interpret
python -m agentgolem.benchmarks --preset robust --output data\benchmarks\gpt-5.4.json --label gpt-5.4
python -m agentgolem.benchmarks benchmarks\sample_suite.json --output data\benchmarks\latest_report.json
python -m agentgolem.benchmarks benchmarks
python -m agentgolem.benchmarks benchmarks --output data\benchmarks\latest_run.json --interpret
python -m agentgolem.benchmarks.compare data\benchmarks\gpt-5.4.json data\benchmarks\claude-sonnet-4.6.json
```

`benchmark.bat` is the one-click Windows launcher. With no arguments it runs
the `robust` preset, writes `data\benchmarks\latest_run.json`, and prints a
human-readable interpretation before pausing so the result stays visible.

Without `--interpret`, the Python CLI keeps its JSON-oriented behavior. With
`--interpret`, it prints a concise verdict against the configured baselines.

The compare command is meant for labeled runs such as `gpt-5.4`,
`claude-sonnet-4.6`, or `deepseek-reasoner`. It now shows raw point metrics
plus delta-vs-baseline summaries, which makes cross-model runs less flattering
when a model only beats a weak baseline by noise.

## Report shape

Each report includes:

- suite metadata
- aggregate metrics for the current system, the named baseline, and the
  actual-minus-baseline delta
- bootstrap confidence intervals for aggregate metrics
- per-case outputs showing retrieved ids, trust predictions, or recovery
  outcomes
- baseline names so the report is explicit about what “better than baseline”
  actually means

This makes it easy to diff benchmark runs across commits or settings snapshots.

## How to read the scores

- `pass`
  - the delta confidence intervals support beating the baseline on every tracked
    metric
- `mixed`
  - some metrics improved, but uncertainty or disagreement remains
- `fail`
  - the stronger baseline still wins once uncertainty is considered

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
- `delta`
  - actual minus baseline
  - positive is better for retrieval and recovery metrics
  - negative is better for trust error metrics such as `Brier` and `ECE`

## Known gaps and next layers

The harness now covers much deeper offline retrieval / trust / recovery
testing, but several benchmark tracks still need to be added:

- repeated-mistake avoidance and richer recovery sequences
- autonomy usefulness and approval-aware productivity
- vow adherence / alignment drift
- multi-agent coordination quality and personality differentiation
- latency, token cost, and long-run degradation tests

Those later tracks can reuse the same suite/report pattern instead of creating
a separate evaluation stack.
