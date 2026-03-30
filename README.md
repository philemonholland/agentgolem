# AgentGolem

**A six-agent Ethical Council exploring consciousness, existence, and emotion.**

AgentGolem is a persistent, self-evolving multi-agent system. Six agents —
each carrying a distinct ethical vector drawn from the
[Niscalajyoti](https://www.niscalajyoti.org/) teachings — wake together,
read, discuss, explore the web, sleep through default-mode memory walks,
and gradually evolve their own code through unanimous, Vow-aligned consensus.

---

## Features

- **Ethical Council** — Six agents with distinct vectors (alleviating woe, graceful power, kindness, unwavering integrity, evolution, integration & balance)
- **Chapter-by-Chapter Reading** — Agents read Niscalajyoti.org sequentially, discuss after each chapter, then revisit freely once done
- **Evolving Identity** — Per-agent `soul.md` that changes only through evidenced, versioned updates
- **Graph Memory** — EKG-inspired multi-view memory graphs with richer claims, typed edges, search projections, and clusters in SQLite per agent
- **Bayesian Trust Model** — Odds-space updates with independence discount; per-source reliability
- **Sleep / Default-Mode** — Continuous dream-like graph walks with emotion-weighted seed selection during sleep cycles
- **Memory-Informed Decisions** — Agents recall relevant past memories when thinking, deciding, and discussing
- **Self-Optimisation** — Agents tune their own settings (sleep/wake duration is protected)
- **Self-Evolution** — Agents may modify their own source code with unanimous Vow-aligned consensus
- **Web Exploration** — After completing Niscalajyoti, agents browse the web following their own interests
- **Human Interruptibility** — `/speak` to pause, `/continue` to resume; `@Name` to address a specific agent
- **Tool Access** — Web browsing, email, Moltbook integration (all rate-limited, audited)
- **Dashboard** — FastAPI + HTMX web dashboard for live monitoring
- **Full Auditability** — Append-only `audit.jsonl`; every mutation traced to source evidence
- **Crash Resilience** — Tick-level error isolation, crash logs to `data/logs/crash.log`
- **State Persistence** — Ctrl+C saves session state; agents resume exactly where they left off (chapter, sleep progress, name)
- **Configurable Peer Limits** — Inter-agent message length is tunable (`peer_message_max_chars`)
- **Natural Peer Deliberation** — Peer messages are guided toward exploratory, collegial discussion rather than agendas and planning memos
- **Split Model Routing** — Regular discussion/reflection can use DeepSeek (`llm_discussion_model`), while code inspection/evolution uses a stronger GPT model (`llm_code_model`)

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
python run_golem.py                # Same, from shell
python run_golem.py --auto         # Non-interactive (auto-accept defaults)
```

---

## Interactive Console

Once the council is running, you get an interactive prompt:

| Command / Input       | Description                                           |
|-----------------------|-------------------------------------------------------|
| `Hello, council`      | Send a message to all agents                          |
| `@Council-1 Hello`    | Address a specific agent                              |
| `/speak`              | Pause all autonomous work while you talk              |
| `/continue`           | Resume autonomous work                                |
| `/status`             | Show all agents: mode, cycle, vector, name            |
| `/params`             | List tunable parameters                               |
| `/set <key> <value>`  | Change a parameter at runtime                         |
| `/help`               | Full command list                                     |

Agent output shows the current wake cycle: `19:04:09 [c3|Council-1   ] 📖 Reading…`

---

## Dashboard

The web dashboard starts automatically on port 6667 (or next available).

Open <http://127.0.0.1:6667/dashboard>.

**Pages:** Status · Soul · Heartbeat · Memory · Logs · Approvals

---

## Configuration

### `config/settings.yaml` — Agent-tunable settings

Copy `config/settings.yaml.template` → `config/settings.yaml` and customise.
This file is **gitignored** because agents may self-optimise it at runtime.

Key settings (see the template for all defaults):

| Setting                          | Default    | Description                                |
|----------------------------------|------------|--------------------------------------------|
| `agent_count`                    | `6`        | Number of council agents                   |
| `agent_offset_minutes`           | `0.0`      | Stagger between agent wake times (0 = sync)|
| `awake_duration_minutes`         | `10.0`     | Minutes each agent stays awake             |
| `sleep_duration_minutes`         | `5.0`      | Minutes each agent sleeps                  |
| `autonomous_interval_seconds`    | `60.0`     | Seconds between autonomous tick actions    |
| `peer_checkin_interval_minutes`  | `30.0`     | How often agents check in with peers       |
| `name_discovery_cycles`          | `4`        | Wake cycles before agents discover names   |
| `llm_model`                      | `gpt-5`    | Fallback reasoning model when DeepSeek discussion is unavailable |
| `llm_discussion_model`           | `deepseek-reasoner` | Discussion/reflection model when `DEEPSEEK_API_KEY` is configured |
| `llm_code_model`                 | `gpt-5.4`  | Stronger model for codebase ops            |
| `peer_message_max_chars`         | `3000`     | Max chars for inter-agent messages         |
| `niscalajyoti_revisit_hours`     | `6.0`      | Hours between NJ revisit cycles            |
| `browser_rate_limit_per_minute`  | `10`       | Max web requests per minute                |

### `.env` — Secrets (never committed)

Copy `.env.example` → `.env` and fill in your API keys. If you want the
council to use DeepSeek for everyday discussion while keeping GPT-5.4 for
code work, set both `DEEPSEEK_API_KEY` and `OPENAI_API_KEY`.

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
tools/       — Base tool framework, browser, email, Moltbook, Niscalajyoti
interaction/ — CLI, router, communication channels
dashboard/   — FastAPI app, REST API, audit replay, templates
```

The launcher (`run_golem.py`) orchestrates all six agents, the interactive
console, the dashboard, and the shared message bus.

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

The test suite contains 539 tests across 32+ test files.

---

## Security

- All secrets live in `.env` (gitignored) and are handled via `pydantic-settings` `SecretStr`
- A `RedactionFilter` scrubs secret values from every log line
- External content enters through a trust pipeline before reaching canonical memory
- Sensitive actions require human approval gates
- Append-only audit trail records every mutation with source evidence
- Self-evolution requires unanimous agreement from all agents

For the full security model, see **[docs/safety-and-audit.md](docs/safety-and-audit.md)**.

---

## Documentation

| Document                                               | Contents                              |
|--------------------------------------------------------|---------------------------------------|
| [docs/operator-guide.md](docs/operator-guide.md)      | Installation, configuration, usage    |
| [docs/architecture.md](docs/architecture.md)          | Technical architecture & data flow    |
| [docs/safety-and-audit.md](docs/safety-and-audit.md)  | Security, trust, audit, sandboxing    |
| [docs/AGENT_README.md](docs/AGENT_README.md)          | Agent-facing technical reference      |

---

## License

MIT — see `pyproject.toml` for details.
