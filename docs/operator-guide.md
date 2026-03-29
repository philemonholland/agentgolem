# Operator Guide

This guide covers everything needed to install, configure, run, and monitor AgentGolem.

---

## Table of Contents

1. [Installation](#installation)
2. [Configuration](#configuration)
3. [Starting the Agent](#starting-the-agent)
4. [Agent Modes](#agent-modes)
5. [CLI Reference](#cli-reference)
6. [Dashboard](#dashboard)
7. [Monitoring](#monitoring)
8. [Controlling the Agent](#controlling-the-agent)
9. [Approval Workflow](#approval-workflow)
10. [Troubleshooting](#troubleshooting)

---

## Installation

### Prerequisites

- **Python 3.12+** (verify with `python --version`)
- **Git**
- Internet access for initial dependency install

### Steps

```powershell
# 1. Clone the repository
git clone <repo-url>
cd AgentGolem

# 2. Create and activate a virtual environment
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1       # Windows PowerShell
# source .venv/bin/activate         # Linux / macOS

# 3. Install the package with dev dependencies
pip install -e ".[dev]"

# 4. Copy and edit secrets
cp .env.example .env
# Open .env in your editor and fill in real API keys
```

After installation you should be able to run:

```bash
python run_golem.py --help
```

---

## Configuration

### `.env` — Secret Keys

The `.env` file holds all sensitive credentials. It is **never committed** to version
control (listed in `.gitignore`). Copy `.env.example` as a starting point.

| Variable              | Required | Description                                  |
|-----------------------|----------|----------------------------------------------|
| `OPENAI_API_KEY`      | Yes      | API key for the OpenAI LLM provider          |
| `OPENAI_BASE_URL`     | No       | Override base URL (default: OpenAI endpoint)  |
| `EMAIL_SMTP_HOST`     | No*      | SMTP server hostname                          |
| `EMAIL_SMTP_PORT`     | No*      | SMTP server port (typically 587)              |
| `EMAIL_SMTP_USER`     | No*      | SMTP username / sender address                |
| `EMAIL_SMTP_PASSWORD` | No*      | SMTP password                                 |
| `EMAIL_IMAP_HOST`     | No*      | IMAP server hostname                          |
| `EMAIL_IMAP_USER`     | No*      | IMAP username                                 |
| `EMAIL_IMAP_PASSWORD` | No*      | IMAP password                                 |
| `MOLTBOOK_API_KEY`    | No*      | API key for Moltbook integration              |
| `MOLTBOOK_BASE_URL`   | No*      | Moltbook API base URL                         |

\* Required only if the corresponding feature is enabled in `settings.yaml`.

### `config/settings.yaml` — Agent-Tunable Settings

Copy `config/settings.yaml.template` → `config/settings.yaml` and customise.
This file is **gitignored** because agents may self-optimise it at runtime
(sleep/wake durations are protected and cannot be changed by agents).

```yaml
data_dir: E:\AgentGolem\Data          # Runtime data location

# --- Council ---
agent_count: 6                         # Number of council agents
agent_offset_minutes: 0.0              # Stagger between agent wake times (0 = sync)
autonomous_interval_seconds: 15.0      # Seconds between autonomous tick actions
name_discovery_cycles: 4               # Wake cycles before agents discover names
peer_checkin_interval_minutes: 10.0    # How often agents check in with peers

# --- Identity ---
awake_duration_minutes: 10.0           # Minutes each agent stays awake
sleep_duration_minutes: 5.0            # Minutes each agent sleeps
wind_down_minutes: 2.0                 # Grace period before sleeping
soul_update_min_confidence: 0.7        # Minimum confidence to accept a soul change

# --- Sleep / Default-Mode ---
sleep_cycle_minutes: 5.0               # Minutes between consolidation cycles
sleep_max_nodes_per_cycle: 1000        # Node budget per cycle
sleep_max_time_ms: 5000                # Wall-clock budget per cycle (ms)

# --- LLM ---
llm_provider: "openai"                 # LLM backend (currently only openai)
llm_model: "gpt-5"                     # Model name for completions

# --- Logging ---
log_level: "INFO"                      # DEBUG | INFO | WARNING | ERROR

# --- Communication ---
email_enabled: false                   # Enable email tool
moltbook_enabled: false                # Enable Moltbook tool
dry_run_mode: false                    # If true, outbound actions are simulated
approval_required_actions:             # Actions that need human approval
  - email_send
  - moltbook_send

# --- Niscalajyoti Ethical Anchor ---
niscalajyoti_revisit_hours: 6.0        # Hours between NJ revisit cycles

# --- Retention ---
retention_archive_days: 5              # Days before archiving inactive nodes
retention_purge_days: 30               # Days before purging archived nodes
retention_min_trust_useful: 0.1
retention_min_centrality: 0.05
retention_promote_min_accesses: 10
retention_promote_min_trust_useful: 0.5

# --- Quarantine ---
quarantine_emotion_threshold: 0.7
quarantine_trust_useful_threshold: 0.3

# --- Web Browsing ---
browser_rate_limit_per_minute: 10
browser_timeout_seconds: 20
```

---

## Starting the Council

```powershell
start.bat                              # Interactive launch (recommended)
python run_golem.py                    # Same, from shell
python run_golem.py --auto             # Non-interactive (auto-accept defaults)
```

The launcher starts all six Ethical Council agents, the interactive console,
and the web dashboard. Each agent enters **AWAKE** mode and begins reading
Niscalajyoti chapter-by-chapter, discussing with peers, and exploring.

---

## Agent Modes

| Mode       | Behaviour                                                        |
|------------|------------------------------------------------------------------|
| **AWAKE**  | Actively reading, discussing, exploring, responding to messages  |
| **ASLEEP** | Running default-mode network — graph walks, merge proposals, contradiction surfacing |
| **PAUSED** | Halted. No processing. Awaits operator commands.                 |

**Legal transitions:**

```
AWAKE  ↔  ASLEEP
AWAKE  →  PAUSED
ASLEEP →  PAUSED
PAUSED →  AWAKE
PAUSED →  ASLEEP
```

---

## Interactive Console

Once the council is running, you interact through the `golem>` prompt:

| Command / Input       | Description                                           |
|-----------------------|-------------------------------------------------------|
| `Hello, council`      | Send a message to all agents                          |
| `@Council-1 Hello`    | Address a specific agent by name                      |
| `/speak`              | Pause all autonomous work while you talk              |
| `/continue`           | Resume autonomous work                                |
| `/status`             | Show all agents: mode, cycle, vector, name            |
| `/params`             | List all tunable parameters                           |
| `/set <key> <value>`  | Change a parameter at runtime                         |
| `/help`               | Full command list                                     |
| `/quit`               | Shut down the council                                 |

Agent output shows the current wake cycle:
```
19:04:09 [c3|Council-1   ] 📖 Reading Niscalajyoti chapter 5/27…
```

> **Note:** The legacy `python -m agentgolem <command>` CLI still exists for
> single-agent use but is not the recommended way to run the council.

---

## Dashboard

The dashboard starts automatically alongside the council on port **6667**
(or next available if in use).

Open <http://127.0.0.1:6667/dashboard> in your browser.

### Pages

| Page          | URL                             | Description                              |
|---------------|---------------------------------|------------------------------------------|
| **Status**    | `/dashboard`                    | Runtime mode, current task, uptime       |
| **Soul**      | `/dashboard/soul`               | Soul contents and version history        |
| **Heartbeat** | `/dashboard/heartbeat`          | Heartbeat contents and history           |
| **Memory**    | `/dashboard/memory`             | Browse nodes, clusters, filter by type/trust |
| **Logs**      | `/dashboard/logs`               | Search activity and audit logs           |
| **Approvals** | `/dashboard/approvals`          | View and act on pending approvals        |

### API Endpoints

The dashboard exposes a JSON API under `/api/`. Key endpoints:

- `GET  /api/status` — agent mode, task, uptime
- `POST /api/agent/wake` / `sleep` / `pause` / `resume` — mode control
- `POST /api/agent/message` — send message
- `GET  /api/soul` — soul content
- `GET  /api/soul/history` — version list
- `GET  /api/heartbeat` — heartbeat content and schedule
- `GET  /api/logs?type=activity&limit=100&q=search` — log search
- `GET  /api/memory/nodes` — list/filter nodes
- `GET  /api/memory/nodes/{id}` — node detail + edges + sources
- `GET  /api/memory/clusters` — list clusters
- `GET  /api/memory/clusters/{id}` — cluster detail
- `GET  /api/memory/stats` — graph statistics
- `GET  /api/approvals` — pending approvals
- `POST /api/approvals/{id}/approve` / `deny` — resolve approvals

---

## Monitoring

### Check Agent Status

Use `/status` in the interactive console, or via API:

```bash
curl http://127.0.0.1:6667/api/status
```

### Inspect Soul Evolution

Soul versions are stored in `data/soul_versions/`. Each version is timestamped.

```bash
python -m agentgolem inspect-soul
```

Via the dashboard at `/dashboard/soul` you can view the current soul and browse
the full version history.

### Inspect Heartbeat History

Heartbeat snapshots are saved in `data/heartbeat_history/`.

```bash
python -m agentgolem inspect-heartbeat
```

The dashboard at `/dashboard/heartbeat` shows the current heartbeat, when the
next one is due, and recent history.

### Search Logs

Two log streams exist:

- **`data/logs/activity.jsonl`** — Operational log (actions, errors, info)
- **`data/logs/audit.jsonl`** — Mutation audit trail (memory changes, approvals)

```bash
python -m agentgolem inspect-logs
# Or search via API:
curl "http://127.0.0.1:8000/api/logs?type=audit&q=contradiction&limit=50"
```

The dashboard at `/dashboard/logs` provides a searchable UI for both streams.

### Explore Memory

```bash
python -m agentgolem inspect-memory
# Or via API:
curl "http://127.0.0.1:8000/api/memory/stats"
curl "http://127.0.0.1:8000/api/memory/nodes?type=fact&trust_min=0.5&limit=20"
```

The dashboard at `/dashboard/memory` lets you filter nodes by type, status, and
trust range, and drill into individual nodes to see their edges and sources.

---

## Controlling the Council

### Pause / Resume Autonomous Work

Use `/speak` to pause all autonomous agent work while you talk. The agents
will still respond to your direct messages but won't take autonomous actions.
Use `/continue` to resume.

### Wake / Sleep / Pause

Mode transitions happen automatically on the configured schedule. You can
also use `/set awake_duration_minutes 20` to change durations at runtime.

Or via the dashboard Status page (buttons) or API:

```bash
curl -X POST http://127.0.0.1:6667/api/agent/pause
```

### Send a Message

Type directly at the `golem>` prompt, or use `@AgentName message` to target
a specific agent.

Messages are queued to the agent's inbox and processed during the next AWAKE
cycle.

---

## Approval Workflow

Certain actions require explicit human approval before execution:

| Action          | Default Approval Required |
|-----------------|--------------------------|
| `email_send`    | Yes                      |
| `moltbook_send` | Yes                      |

When the agent wants to perform an approval-gated action:

1. A JSON file is created in `data/approvals/` with request details.
2. The agent blocks on that action until the request is resolved.
3. The operator approves or denies via CLI or dashboard.

### Approve or Deny

```bash
# CLI
python -m agentgolem inspect-pending    # See pending requests
python -m agentgolem approve <request-id>
python -m agentgolem deny <request-id>

# API
curl -X POST "http://127.0.0.1:8000/api/approvals/<request-id>/approve?reason=Looks+good"
curl -X POST "http://127.0.0.1:8000/api/approvals/<request-id>/deny?reason=Too+risky"
```

Approval and denial events are recorded in the audit trail.

---

## Troubleshooting

### Missing `.env` file

```
FileNotFoundError: .env
```

**Fix:** Copy `.env.example` to `.env` and fill in your API keys.

### SQLite database locked

```
sqlite3.OperationalError: database is locked
```

**Fix:** Ensure only one agent process is running. AgentGolem uses WAL mode for
better concurrency, but only one writer is supported at a time. Stop any
duplicate processes.

### Agent won't start — missing dependencies

```
ModuleNotFoundError: No module named 'agentgolem'
```

**Fix:** Make sure you installed in editable mode: `pip install -e ".[dev]"`

### Dashboard not loading

**Fix:** The dashboard starts automatically with the council on port 6667+.
If it's not loading, check that the council is running and try the next port
(6668, 6669, etc.) in case of port conflicts.

### LLM API errors / timeouts

**Fix:** Check that `OPENAI_API_KEY` in `.env` is valid. Check `OPENAI_BASE_URL`
if using a custom endpoint. Review `data/logs/activity.jsonl` for error details.

### Approval requests piling up

**Fix:** Run `python -m agentgolem inspect-pending` regularly or monitor the
dashboard Approvals page. Consider adjusting `approval_required_actions` in
`settings.yaml` if certain actions don't need gating.

### Log files growing large

Activity and audit logs are append-only JSONL files. Rotate or archive them
periodically:

```powershell
# Example: archive old logs
Move-Item data\logs\activity.jsonl data\logs\activity.jsonl.bak
Move-Item data\logs\audit.jsonl data\logs\audit.jsonl.bak
```

The agent will create new log files on the next write.
