# AgentGolem

**A seven-agent Ethical Council exploring consciousness, existence, and emotion.**

AgentGolem is a persistent, self-evolving multi-agent system. Seven agents —
each carrying a distinct ethical vector drawn from the
[Niscalajyoti](https://www.niscalajyoti.org/) teachings or a supplementary
devil's-advocate role — wake together, read, discuss, explore the web, sleep
through default-mode memory walks, and gradually evolve their own code through
unanimous, Vow-aligned consensus.

---

## Features

- **Ethical Council** — Seven agents: six Vow-mapped vectors plus a supplementary good-faith devil's advocate
- **Chapter-by-Chapter Reading** — Councils 1–6 read Niscalajyoti.org sequentially, while Council-7 begins with SEP, Alignment Forum, and LessWrong before broadening
- **Evolving Identity** — Per-agent `soul.md` that changes only through evidenced, versioned updates
- **Graph Memory** — EKG-inspired multi-view memory graphs with richer claims, typed edges, search projections, clusters, and read-only cross-agent overlays
- **Bayesian Trust Model** — Odds-space updates with independence discount; per-source reliability
- **Sleep / Default-Mode** — Phase-aware spiking-inspired sleep walks with persistent neural state, dream noise, and STDP-like edge plasticity
- **Memory-Informed Decisions** — Agents recall relevant past memories when thinking, deciding, and discussing
- **Read-Only Mycelium** — Agents can surface entangled peer memories through owner-written exports while keeping foreign memories explicitly separate
- **Self-Optimisation** — Agents tune their own settings (sleep/wake duration is protected)
- **Self-Evolution** — Agents may modify their own source code with unanimous Vow-aligned consensus
- **Guarded Experiment Loop** — Fixed-budget self-improvement experiments use audited ledgers, approval gates, resource locks, and existing council review
- **Web Exploration** — After completing their initial formation tracks, agents broaden into wider web exploration
- **Human Interruptibility** — `/speak` to pause, `/continue` to resume; `@Name` to address a specific agent
- **Tool Access** — Web browsing, email, Moltbook integration (all rate-limited, audited)
- **Dashboard** — FastAPI + HTMX web dashboard for live monitoring
- **Full Auditability** — Append-only `audit.jsonl`; every mutation traced to source evidence
- **Crash Resilience** — Tick-level error isolation, crash logs to `data/logs/crash.log`
- **State Persistence** — Ctrl+C saves session state; agents resume exactly where they left off (chapter, sleep progress, name)
- **Configurable Peer Limits** — Inter-agent message length is tunable (`peer_message_max_chars`)
- **Natural Peer Deliberation** — Peer messages are guided toward exploratory, collegial discussion rather than agendas and planning memos
- **Route-Specific LLM Profiles** — Discussion and code paths can each use their own OpenAI-compatible model, API key, and base URL
- **Capability-Aware Curiosity** — Autonomous action selection reasons over a prompt-visible toolbox instead of only a brittle verb list
- **Approval-Gated External Tools** — Outbound email and Moltbook actions are surfaced as explicit capabilities and stay human-gated at the action level

---

## Quick Start

```powershell
git clone <repo-url>
cd AgentGolem
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1       # Windows
# source .venv/bin/activate         # Linux / macOS
pip install -e ".[dev]"
cp .env.example .env               # Add your API keys
cp config/settings.yaml.template config/settings.yaml
```

### Launch

```powershell
start.bat                          # Interactive launch (recommended)
dashboard.bat                      # Launch AgentGolem and open the dashboard
benchmark.bat                      # One-click benchmark run + interpretation
python run_golem.py                # Same, from shell
python run_golem.py --auto         # Non-interactive (auto-accept defaults)
```

---

## Interactive Console

Once the council is running, you get an interactive prompt:

| Command / Input       | Description                                           |
|-----------------------|-------------------------------------------------------|
| `Hello, council`      | Let one natural responder answer, then let the council continue organically |
| `@Council-1 Hello`    | Address a specific agent                              |
| `/a 3 Hello`          | Send a private message to Council-3                   |
| `/speak`              | Pause all autonomous work while you talk              |
| `/continue`           | Resume autonomous work                                |
| `/status`             | Show all agents: mode, cycle, vector, name            |
| `/params`             | List tunable parameters                               |
| `/set <key> <value>`  | Change a parameter at runtime                         |
| `/help`               | Full command list                                     |

Agent output shows the current wake cycle: `19:04:09 [c3|Council-1   ] 📖 Reading…`

---

## Dashboard

The web dashboard starts automatically on port `8765` (or the next browser-safe free port).

`start.bat` and `dashboard.bat` now open it automatically in your default browser.

If you need it manually, use `/dashboard` in the console or open
<http://127.0.0.1:8765/dashboard>.

**Pages:** Consciousness · Settings · Soul · Heartbeat · Memory · Logs · Approvals

---

## Configuration

### `config/settings.yaml` — Agent-tunable settings

Copy `config/settings.yaml.template` → `config/settings.yaml` and customise.
This file is **gitignored** because agents may self-optimise it at runtime.

Key settings (see the template for all defaults):

| Setting                          | Default    | Description                                |
|----------------------------------|------------|--------------------------------------------|
| `agent_count`                    | `7`        | Number of council agents                   |
| `agent_offset_minutes`           | `0.0`      | Stagger between agent wake times (0 = sync)|
| `awake_duration_minutes`         | `10.0`     | Minutes each agent stays awake             |
| `sleep_duration_minutes`         | `5.0`      | Minutes each agent sleeps                  |
| `autonomous_interval_seconds`    | `60.0`     | Seconds between autonomous tick actions    |
| `peer_checkin_interval_minutes`  | `30.0`     | How often agents check in with peers       |
| `name_discovery_cycles`          | `4`        | Wake cycles before agents discover names   |
| `llm_model`                      | `gpt-5`    | Legacy fallback discussion model when no route-specific discussion endpoint is configured |
| `llm_discussion_model`           | `deepseek-reasoner` | Primary discussion/reflection model |
| `llm_code_model`                 | `gpt-5.4`  | Primary code-inspection / evolution model  |
| `peer_message_max_chars`         | `3000`     | Max chars for inter-agent messages         |
| `sleep_phase_split`              | `0.67`     | Fraction of sleep macro-cycle spent in consolidation before dream mode |
| `sleep_membrane_decay`           | `0.82`     | Base leak factor for spiking-inspired sleep dynamics |
| `niscalajyoti_revisit_hours`     | `6.0`      | Hours between NJ revisit cycles            |
| `browser_rate_limit_per_minute`  | `10`       | Max web requests per minute                |
| `google_custom_search_enabled`   | `false`    | Enable Google Custom Search backend        |
| `google_custom_search_hourly_quota` | `4`     | Local average refill rate for search quota |

Sleep also exposes spiking controls such as thresholds, refractory steps,
STDP window/strength, dream noise, and persisted neural-state size in
`config/settings.yaml`.

### `.env` — Secrets (never committed)

Copy `.env.example` → `.env` and fill in your API keys. The simplest split
setup is still `DEEPSEEK_API_KEY` for discussion and `OPENAI_API_KEY` for code.

If you want arbitrary OpenAI-compatible endpoints per route, you can also set:

- `LLM_DISCUSSION_API_KEY` + `LLM_DISCUSSION_BASE_URL`
- `LLM_CODE_API_KEY` + `LLM_CODE_BASE_URL`

When those route-specific variables are present, they override the legacy
OpenAI/DeepSeek fallback routing for that path.

For Google search, set `GOOGLE_CUSTOM_SEARCH_API_KEY` and
`GOOGLE_CUSTOM_SEARCH_ENGINE_ID`, then enable
`google_custom_search_enabled: true` in `config/settings.yaml`.
Search uses an API key plus Programmable Search Engine ID (`cx`) — not OAuth.

For future Gmail / Drive integration, keep a Desktop App OAuth client JSON in
`config/google_oauth_client.json` and let the token cache live in
`data/google/oauth_token.json`. Both paths are gitignored and should stay local.

---

## Architecture

AgentGolem is organised into 11 subsystems under `src/agentgolem/`:

```
config/      — Settings + secret management
logging/     — Structured logging, redaction, audit trail
runtime/     — State machine (AWAKE / ASLEEP / PAUSED), interrupts, main loop
identity/    — Soul + heartbeat managers
llm/         — LLM abstraction layer
memory/      — Graph models, schema, store, encoding, retrieval, mutations
trust/       — Bayesian trust, usefulness, quarantine, retention, contradiction
sleep/       — Graph walker, scheduler, consolidation engine
tools/       — Capability registry, approval gate, browser, email, Moltbook, Niscalajyoti
interaction/ — CLI, router, communication channels
dashboard/   — FastAPI app, REST API, audit replay, templates
```

The launcher (`run_golem.py`) orchestrates all seven agents, the interactive
console, the dashboard, and the shared message bus.

Cross-agent memory sharing lives under `data\shared_memory\`:
`exports\<agent>.sqlite` contains owner-written read-only projections, and
`mycelium.db` stores entanglement links between `(agent_id, node_id)` refs.

For detailed architecture, see **[docs/architecture.md](docs/architecture.md)**.

---

## Memory Graph Visualiser

Double-click **`visualize.bat`** or run manually:

```powershell
python tools/memory_visualizer.py                      # auto-detect data dir
python tools/memory_visualizer.py --data-dir E:\AgentGolem\Data
```

Opens <http://127.0.0.1:7777> with:
- Agent selector (tabs for each council member)
- Interactive force-directed graph (D3.js)
- Filter by node type, status, text search
- Click any node for full details, edges, sources, clusters
- Optional `🍄 Mycelium` overlay that adds read-only peer ghost nodes and dashed cross-agent entanglement links

---

## Reset Agents

Double-click **`reset.bat`** to wipe agent state and start fresh:

- **Full reset** — clears memory graphs, reading progress, logs, heartbeats
- **Soft reset** — clears progress & logs but keeps memory graphs intact

If the memory schema version changes, outdated `graph.db` files are rebuilt
automatically on startup. This EKG overhaul intentionally invalidates the older
flat short-claim graph format.

`soul.md` files (agent personality) are always preserved.

---

## Testing

```bash
pytest                           # Run all tests
pytest --cov=agentgolem          # With coverage
pytest -m "not integration"      # Skip integration tests
```

The test suite covers the full runtime, memory, trust, sleep, and tooling stack.

---

## Benchmarking

Run the benchmark tooling against either the robust offline preset or a live
memory snapshot audit:

```powershell
benchmark.bat
python -m agentgolem.benchmarks --preset robust
python -m agentgolem.benchmarks benchmarks
python -m agentgolem.benchmarks benchmarks --output data\benchmarks\latest_run.json --interpret
python -m agentgolem.benchmarks benchmarks --output data\benchmarks\gpt-5.4.json --label gpt-5.4
python -m agentgolem.benchmarks benchmarks --output data\benchmarks\claude-sonnet-4.6.json --label claude-sonnet-4.6
python -m agentgolem.benchmarks --live-data data --output data\benchmarks\live_memory.json --interpret
python -m agentgolem.benchmarks --live-data data\council_3 --interpret
python -m agentgolem.benchmarks.compare data\benchmarks\gpt-5.4.json data\benchmarks\claude-sonnet-4.6.json
```

`benchmark.bat` now runs the deterministic `robust` preset by default. That
preset currently includes:

- retrieval depth with `60` hard cases, larger candidate pools, multiple
  relevant items, adversarial near-duplicates, and a trust-blind
  `lexical_salience_no_trust` baseline;
- trust calibration depth with `120` imbalanced cases and a stronger
  `source_reliability_prior` baseline;
- deterministic error recovery depth with `60` cases covering HTTP failures,
  timeout-style failures, discovered URLs, guessed URLs, and near-match traps.

Reports now include bootstrap confidence intervals and delta-vs-baseline
summaries so you can see whether a gain looks robust or just noisy.

`--live-data` runs a read-only lifecycle audit over real `graph.db` snapshots.
It copies each live database to a temporary file before probing traversal paths,
so the audit does not bump `access_count` or mutate the running agents.

The older JSON suites in `benchmarks\` still work and are useful as smoke tests
or custom experiments, but they are no longer the default “real benchmark” path.

`agentgolem.benchmarks.compare` is for offline benchmark reports only; live
lifecycle audit reports use a different schema and are rejected there on
purpose.

Self-improvement experiments now build on these same evaluation commands. The
shared experiment ledger stores proposals, approvals, run history, and
forwarded council proposals under `data\experiments\`, and the dashboard
surfaces that state on the **Experiments** page.

For the suite format and planned extensions, see
**[docs/benchmarking.md](docs/benchmarking.md)**.

---

## Security

- All secrets live in `.env` (gitignored) and are handled via `pydantic-settings` `SecretStr`
- A `RedactionFilter` scrubs secret values from every log line
- External content enters through a trust pipeline before reaching canonical memory
- Sensitive actions require human approval gates
- Append-only audit trail records every mutation with source evidence
- Self-evolution requires unanimous agreement from all active council agents

For the full security model, see **[docs/safety-and-audit.md](docs/safety-and-audit.md)**.

---

## Documentation

| Document                                               | Contents                              |
|--------------------------------------------------------|---------------------------------------|
| [docs/operator-guide.md](docs/operator-guide.md)      | Installation, configuration, usage    |
| [docs/architecture.md](docs/architecture.md)          | Technical architecture & data flow    |
| [docs/benchmarking.md](docs/benchmarking.md)          | Offline benchmark harness and metrics |
| [docs/safety-and-audit.md](docs/safety-and-audit.md)  | Security, trust, audit, sandboxing    |
| [docs/AGENT_README.md](docs/AGENT_README.md)          | Agent-facing technical reference      |

---

## License

MIT — see `pyproject.toml` for details.
