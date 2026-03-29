# AgentGolem

**A persistent autonomous agent exploring consciousness, existence, and emotion.**

AgentGolem is a long-running AI agent with an evolving identity (`soul.md`),
graph-based long-term memory, Bayesian trust scoring, sleep/consolidation
cycles, and full auditability. Its ethical anchor is rooted in the teachings
of [Niscalajyoti](https://www.niscalajyoti.org/).

---

## Features

- **Evolving Identity** — Soul document that changes only through evidenced, versioned updates
- **Graph Memory** — Conceptual nodes, typed edges, memory clusters in SQLite
- **Bayesian Trust Model** — Odds-space updates with independence discount; per-source reliability
- **Usefulness Scoring** — Bump/penalize rules tied to actual retrieval value
- **Sleep / Default-Mode** — Bounded graph walks, merge proposals, contradiction surfacing
- **Tool Access** — Web browsing, email, Moltbook integration (all rate-limited, audited)
- **CLI + Dashboard** — Typer CLI for control; FastAPI + HTMX dashboard for monitoring
- **Full Auditability** — Append-only `audit.jsonl`; every mutation traced to source evidence
- **Human Interruptibility** — Operator can pause, resume, or override at any time
- **Approval Gates** — Sensitive actions (email send, Moltbook post) require human approval
- **Ethical Anchor** — Periodic ingestion of Niscalajyoti.org teachings with protected trust scores

---

## Quick Start

```powershell
git clone <repo-url>
cd AgentGolem
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1       # Windows
# source .venv/bin/activate         # Linux / macOS
pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your API keys
python -m agentgolem run            # Start the agent
python -m agentgolem status         # Check status
```

---

## CLI Commands

| Command                | Description                                        |
|------------------------|----------------------------------------------------|
| `run`                  | Start the agent main loop                          |
| `wake`                 | Transition agent to AWAKE mode                     |
| `sleep`                | Transition agent to ASLEEP (consolidation) mode    |
| `pause`                | Halt the agent; awaits further commands             |
| `resume`               | Resume agent (set to AWAKE)                        |
| `status`               | Show current mode, task, uptime                    |
| `inspect-soul`         | Display current `soul.md`                          |
| `inspect-heartbeat`    | Display current `heartbeat.md`                     |
| `inspect-logs`         | Show recent activity log entries                   |
| `inspect-memory`       | Browse memory graph nodes and edges                |
| `inspect-pending`      | List pending tasks and approval requests           |
| `approve <request-id>` | Approve a pending approval request                 |
| `deny <request-id>`    | Deny a pending approval request                    |
| `message <text>`       | Send a message to the agent's inbox                |

All commands are invoked as `python -m agentgolem <command>`.

---

## Dashboard

Start the local web dashboard:

```bash
uvicorn agentgolem.dashboard.app:create_dashboard_app --factory --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000/dashboard>.

**Pages:** Status · Soul · Heartbeat · Memory · Logs · Approvals

---

## Configuration

### `config/settings.yaml` — Non-secret settings

Key settings (see the file for defaults):

| Setting                        | Default              | Description                         |
|--------------------------------|----------------------|-------------------------------------|
| `data_dir`                     | `"data"`             | Runtime data directory              |
| `heartbeat_interval_hours`     | `6.0`               | Hours between heartbeat cycles      |
| `soul_update_min_confidence`   | `0.7`               | Min confidence to update soul       |
| `sleep_cycle_minutes`          | `5.0`               | Minutes between sleep cycles        |
| `sleep_max_nodes_per_cycle`    | `100`               | Max nodes visited per sleep cycle   |
| `sleep_max_time_ms`            | `5000`              | Time budget per sleep cycle (ms)    |
| `llm_provider`                 | `"openai"`           | LLM backend provider                |
| `llm_model`                    | `"gpt-5.4-mini"`    | Model name for LLM calls           |
| `log_level`                    | `"INFO"`             | Logging verbosity                   |
| `dry_run_mode`                 | `true`               | Dry-run all outbound communication  |
| `approval_required_actions`    | `[email_send, …]`   | Actions requiring human approval    |
| `niscalajyoti_revisit_hours`   | `168.0`             | Hours between anchor revisits       |
| `browser_rate_limit_per_minute`| `10`                | Max web requests per minute         |

### `.env` — Secrets (never committed)

Copy `.env.example` to `.env` and fill in real values. See `.env.example` for all keys.

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

For detailed architecture, see **[docs/architecture.md](docs/architecture.md)**.

---

## Testing

```bash
pytest                           # Run all tests
pytest --cov=agentgolem          # With coverage
pytest -m "not integration"      # Skip integration tests
```

The test suite contains 339+ tests across 32 test files.

---

## Security

- All secrets live in `.env` (gitignored) and are handled via `pydantic-settings` `SecretStr`
- A `RedactionFilter` scrubs secret values from every log line
- External content enters through a trust pipeline before reaching canonical memory
- Sensitive actions require human approval gates
- Append-only audit trail records every mutation with source evidence

For the full security model, see **[docs/safety-and-audit.md](docs/safety-and-audit.md)**.

---

## Documentation

| Document                                               | Contents                              |
|--------------------------------------------------------|---------------------------------------|
| [docs/operator-guide.md](docs/operator-guide.md)      | Installation, configuration, usage    |
| [docs/architecture.md](docs/architecture.md)          | Technical architecture & data flow    |
| [docs/safety-and-audit.md](docs/safety-and-audit.md)  | Security, trust, audit, sandboxing    |

---

## License

MIT — see `pyproject.toml` for details.
