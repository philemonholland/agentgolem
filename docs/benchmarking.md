# Benchmarking AgentGolem

AgentGolem already exposes rich telemetry, but telemetry alone does not answer
the main question: does the system retrieve the right memories, assign useful
trust scores, recover from failures, and beat stronger alternatives in a way
that still looks robust once uncertainty is considered?

The benchmark tooling in `src\agentgolem\benchmarks\` now covers two lanes:

- an offline harness with labeled suites, deterministic presets, and JSON
  reports;
- a read-only live-memory lifecycle audit that inspects real `graph.db`
  snapshots without mutating the running agents.

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

The live-memory audit is complementary rather than a replacement. Instead of
asking "does this benchmark suite beat a baseline?", it asks "what shape is the
real memory graph in right now, and do core traversal paths still work on a
snapshot of it?"

## Live memory lifecycle audit

The live audit scans one or more real memory graphs under a data root such as
`data\council_*\memory\graph.db`.

It is meant for fuller lifecycle checks that offline suites do not yet cover
well:

- provenance coverage across organically grown memories;
- source density per node;
- edge participation and graph connectedness;
- trust-vs-source alignment gaps;
- access distribution, including how much memory remains untouched;
- canonical / archived rates in the current graph;
- traversal integrity for neighborhoods, contradictions, and supersession chains.

The runner never benchmarks the live DBs in place. It first copies each
`graph.db` to a temporary snapshot, then runs all traversal checks on that
snapshot so `access_count` and `last_accessed` in the real agent state are not
changed.

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
python -m agentgolem.benchmarks --live-data data --output data\benchmarks\live_memory.json --interpret
python -m agentgolem.benchmarks --live-data data\council_3 --interpret
python -m agentgolem.benchmarks --live-data data\council_3\memory\graph.db --interpret
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

`--live-data` switches the CLI into lifecycle-audit mode. It accepts a data root
(`data`), a single agent folder (`data\council_3`), or a direct database path
(`data\council_3\memory\graph.db`). It cannot be combined with a suite path or
`--preset`.

The compare command is meant for labeled runs such as `gpt-5.4`,
`claude-sonnet-4.6`, or `deepseek-reasoner`. It now shows raw point metrics
plus delta-vs-baseline summaries, which makes cross-model runs less flattering
when a model only beats a weak baseline by noise.

The compare command is for offline benchmark reports only. Live lifecycle audit
reports have a different schema and are intentionally rejected by
`agentgolem.benchmarks.compare`.

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

Live lifecycle reports instead include:

- aggregate lifecycle metrics across every scanned agent graph;
- per-agent graph metrics and notes;
- traversal recall checks for neighborhoods, contradictions, and supersession;
- pass / mixed / fail statuses based on memory-health thresholds rather than
  baseline deltas.

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

Live lifecycle metric guide:

- `Provenance coverage`
  - fraction of nodes linked to at least one source
  - higher is better; low values mean memory is growing without traceable origin
- `Average sources per node`
  - mean provenance density across the graph
  - higher is usually better until it reflects redundant spam rather than richer grounding
- `Edge participation`
  - fraction of nodes that participate in at least one graph edge
  - higher is better; very low values suggest the graph is becoming a flat pile of notes
- `Trust/source alignment gap`
  - mean absolute gap between node trustworthiness and linked source reliability
  - lower is better; large gaps suggest trust is drifting away from evidence
- `Zero-access rate`
  - fraction of nodes that have never been revisited
  - lower is generally healthier, though a growing graph can raise it temporarily
- `Average access count`
  - how often nodes are being revisited on average
  - higher is usually better if it reflects useful recall rather than loops
- `Canonical rate`
  - fraction of nodes promoted to canonical memory
  - context-dependent; near-zero can mean the system is not consolidating much
- `Archived rate`
  - fraction of nodes marked archived
  - context-dependent; zero is normal in a very fresh graph
- `Neighborhood / contradiction / supersession recall`
  - whether the retriever can still walk the graph structure correctly on the snapshot
  - higher is better; failures here suggest broken traversal integrity, not just weak ranking

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
