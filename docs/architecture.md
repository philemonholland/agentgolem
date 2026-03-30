# Architecture

Technical architecture of AgentGolem — a six-agent Ethical Council.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Subsystem Overview](#subsystem-overview)
3. [Data Flow](#data-flow)
4. [Memory Model](#memory-model)
5. [Trust Model](#trust-model)
6. [Sleep / Default-Mode](#sleep--default-mode)
7. [Heartbeat Cycle](#heartbeat-cycle)
8. [Soul Update Policy](#soul-update-policy)
9. [Retention Pipeline](#retention-pipeline)
10. [Ethical Anchor](#ethical-anchor)
11. [Runtime Loop](#runtime-loop)

---

## System Overview

AgentGolem runs as a **six-agent Ethical Council**. Each agent embodies a
distinct ethical vector derived from [Niscalajyoti](https://www.niscalajyoti.org/):

| Agent      | Ethical Vector              |
|------------|-----------------------------|
| Council-1  | Alleviating woe             |
| Council-2  | Graceful power              |
| Council-3  | Kindness                    |
| Council-4  | Unwavering integrity        |
| Council-5  | Evolution                   |
| Council-6  | Integration and balance     |

All agents share the same wake/sleep schedule (`agent_offset_minutes: 0`),
each has its own graph memory (SQLite), soul, and heartbeat. They communicate
via a shared message bus and periodically check in with peers.

Cross-agent memory sharing stays read-only. Each agent owns its own
`graph.db`, publishes a compact export snapshot under
`data/shared_memory/exports/`, and participates in a separate
`data/shared_memory/mycelium.db` overlay that stores entanglement links between
memory references.

The launcher (`run_golem.py`) orchestrates:
- Agent lifecycle (wake/sleep/shutdown)
- Interactive console (`golem>` prompt with `/speak`, `/continue`, `@Name`)
- Web dashboard (FastAPI on port 6667+)
- Crash logging to `data/logs/crash.log`

### Agent Lifecycle

1. **Niscalajyoti reading** — Agents read NJ chapters sequentially, one per
   wake cycle, discussing after each chapter
2. **Web exploration** — After finishing all chapters, agents browse the web
   following their own interests
3. **Periodic revisit** — Agents revisit NJ sections non-linearly
4. **Self-optimisation** — Agents tune their own settings (sleep/wake durations
   are protected)
5. **Self-evolution** — Agents may modify source code with unanimous Vow-aligned
   consensus, then restart via `start.bat`

---

## Subsystem Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          AgentGolem                                 │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ config   │  │ logging  │  │ runtime  │  │   interaction    │   │
│  │ settings │  │ struct   │  │ state    │  │   CLI + router   │   │
│  │ secrets  │  │ redact   │  │ loop     │  │   channels       │   │
│  │          │  │ audit    │  │ interrupt│  │                  │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘   │
│       │              │             │                  │             │
│  ┌────▼─────┐  ┌─────▼────┐  ┌────▼─────┐  ┌────────▼─────────┐   │
│  │ identity │  │   llm    │  │  memory  │  │    dashboard     │   │
│  │ soul     │  │ base     │  │ models   │  │    FastAPI app   │   │
│  │ heartbeat│  │ openai   │  │ schema   │  │    REST API      │   │
│  │          │  │          │  │ store    │  │    templates     │   │
│  └────┬─────┘  └─────┬────┘  │ encode   │  │    replay        │   │
│       │              │       │ retrieve │  └──────────────────┘   │
│       │              │       │ mutate   │                         │
│  ┌────▼─────┐        │       └────┬─────┘                         │
│  │  tools   │        │            │                               │
│  │ browser  │◄───────┘       ┌────▼─────┐  ┌──────────────────┐   │
│  │ email    │                │  trust   │  │     sleep        │   │
│  │ moltbook │                │ bayesian │  │     walker       │   │
│  │ niscala  │                │ useful   │  │     scheduler    │   │
│  │ base     │                │ quarant  │  │     consolidate  │   │
│  └──────────┘                │ retain   │  └──────────────────┘   │
│                              │ contrad  │                         │
│                              └──────────┘                         │
└─────────────────────────────────────────────────────────────────────┘
```

### Subsystem Responsibilities

| Subsystem       | Package                    | Responsibility                                       |
|-----------------|----------------------------|------------------------------------------------------|
| **Config**      | `agentgolem.config`        | YAML settings, `.env` secrets, singleton management  |
| **Logging**     | `agentgolem.logging`       | Structured JSON + console logs, secret redaction, audit trail |
| **Runtime**     | `agentgolem.runtime`       | State machine, main event loop, interrupt system     |
| **Identity**    | `agentgolem.identity`      | Soul document manager, heartbeat manager             |
| **LLM**        | `agentgolem.llm`           | LLM abstraction protocol, OpenAI implementation      |
| **Memory**      | `agentgolem.memory`        | Graph models, SQLite store, encoding, retrieval, mutations |
| **Trust**       | `agentgolem.trust`         | Bayesian updater, usefulness scoring, quarantine, retention, contradiction |
| **Sleep**       | `agentgolem.sleep`         | Graph walker, cycle scheduler, consolidation engine   |
| **Tools**       | `agentgolem.tools`         | Tool registry, approval gate, browser, email, Moltbook, Niscalajyoti |
| **Interaction** | `agentgolem.interaction`   | Typer CLI, message router, communication channels    |
| **Dashboard**   | `agentgolem.dashboard`     | FastAPI web app, REST API, audit replay, HTMX templates |

---

## Data Flow

```
 External Input                     Agent Core                      Output
 ──────────────                     ──────────                      ──────

 ┌─────────┐     ┌───────────┐     ┌───────────┐     ┌──────────┐
 │ Web page │────▶│   Tool    │────▶│ Encoding  │────▶│ Memory   │
 │ Email    │     │ (browser, │     │ (concept  │     │ Graph    │
 │ Moltbook │     │  email,   │     │  nodes +  │     │ (SQLite) │
 │ Human msg│     │  etc.)    │     │  sources) │     │          │
 └─────────┘     └─────┬─────┘     └─────┬─────┘     └────┬─────┘
                       │                 │                 │
                       │                 ▼                 │
                       │           ┌───────────┐          │
                       │           │   Trust    │          │
                       │           │ Pipeline   │          │
                       │           │ (bayesian, │◄─────────┘
                       │           │  useful,   │
                       │           │  quarant.) │
                       │           └─────┬─────┘
                       │                 │
                       ▼                 ▼
                 ┌───────────┐    ┌───────────┐     ┌──────────┐
                 │ Approval  │    │ Retrieval │────▶│ LLM      │
                 │ Gate      │    │ (query,   │     │ Reasoning│
                 │           │    │  neighbor,│     │          │
                 └─────┬─────┘    │  cluster) │     └────┬─────┘
                       │          └───────────┘          │
                       ▼                                 ▼
                 ┌───────────┐                    ┌───────────┐
                 │ Outbound  │                    │ Identity  │
                 │ (email,   │                    │ (soul,    │
                 │  moltbook)│                    │  heartbeat│
                 └───────────┘                    │  updates) │
                                                  └───────────┘
```

**Flow summary:**

1. External content enters via **tools** (web, email, Moltbook, human messages)
2. Content is **encoded** into conceptual nodes with `Source` provenance objects
3. Nodes pass through the **trust pipeline** (Bayesian update, independence discount)
4. Trusted nodes are stored in the **memory graph** (SQLite, WAL mode)
5. The **retrieval** system queries the graph for relevant context using
   full-text claims plus compact `search_text` projections
6. Cross-agent recall optionally searches owner-written exports and follows
   mycelium entanglements without touching foreign primary stores
7. The **LLM** reasons over retrieved context to produce responses, decisions, and proposals
8. Proposals for outbound actions pass through **approval gates**
9. Identity documents (**soul**, **heartbeat**) are updated through constrained processes

---

## Memory Model

### Conceptual Nodes

Each memory is represented as a **ConceptualNode** — a natural-language claim
that captures **one clean idea** with metadata. Claims are no longer forced
into a 3–15 word micro-phrase.

| Field              | Type           | Description                                |
|--------------------|----------------|--------------------------------------------|
| `id`               | `str` (UUID)   | Unique identifier                          |
| `text`             | `str`          | Full natural-language claim                |
| `search_text`      | `str`          | Compact search/retrieval projection        |
| `type`             | `NodeType`     | One of 10 types (see below)                |
| `created_at`       | `str` (ISO)    | Creation timestamp (UTC)                   |
| `last_accessed`    | `str` (ISO)    | Last retrieval timestamp                   |
| `access_count`     | `int`          | Number of times retrieved                  |
| `base_usefulness`  | `float`        | Base usefulness score [0, 1]               |
| `trustworthiness`  | `float`        | Bayesian trust probability [0.01, 0.99]    |
| `salience`         | `float`        | Within-source importance [0, 1]            |
| `emotion_label`    | `str \| None`  | Optional emotion label                     |
| `emotion_score`    | `float`        | Emotion intensity [0, 1]                   |
| `centrality`       | `float`        | Graph centrality score [0, 1]              |
| `status`           | `NodeStatus`   | `active`, `archived`, or `purged`          |
| `canonical`        | `bool`         | Whether this is established knowledge      |

### Node Types (10)

`fact` · `preference` · `event` · `goal` · `risk` · `interpretation` ·
`identity` · `rule` · `association` · `procedure`

### Memory Clusters

Groups of related nodes are combined into **MemoryCluster** objects with their
own aggregate scores for trust, usefulness, emotion, and contradiction status.

### Sources

Every claim must have at least one **Source** — a provenance record:

| Field                | Description                                       |
|----------------------|---------------------------------------------------|
| `id`                 | Unique source ID                                  |
| `kind`               | `web`, `email`, `human`, `moltbook`, `inference`, `niscalajyoti` |
| `origin`             | URL, email address, or description                |
| `reliability`        | Source reliability score [0, 1]                   |
| `independence_group` | Group ID for correlation tracking                 |
| `timestamp`          | When the source was observed                      |
| `raw_reference`      | Optional raw content reference                    |

### Edge Types (8)

Edges connect nodes with typed, weighted relationships:

| Edge Type          | Semantics                                          |
|--------------------|----------------------------------------------------|
| `related_to`       | General semantic relationship                      |
| `part_of`          | Hierarchical containment                           |
| `supports`         | Evidence that strengthens a claim                  |
| `contradicts`      | Evidence that conflicts with a claim               |
| `supersedes`       | Newer version replaces older                       |
| `same_as`          | Duplicate detection                                |
| `merge_candidate`  | Proposed for merging during consolidation          |
| `derived_from`     | Abstraction or inference lineage                   |

Edges have a `weight` in `[0.01, 5.0]` that represents relationship strength.
Sleep-cycle graph walks reinforce frequently-traversed edges and weaken
rarely-used ones.

### Storage

The memory graph is stored in **SQLite** with WAL mode for concurrent reads.
Tables: `nodes`, `edges`, `sources`, `node_sources` (junction), `clusters`,
`cluster_members` (junction), `cluster_sources` (junction).

All `datetime` values are stored as ISO 8601 strings. The `canonical` boolean
is stored as `INTEGER` (0/1).

### Cross-Agent Mycelium

Agent memories are **federated**, not merged.

- `data/shared_memory/exports/<agent>.sqlite` contains owner-written read-only
  projections of active memories
- `data/shared_memory/mycelium.db` stores entanglement links between
  `(agent_id, node_id)` references rather than copying foreign nodes
- waking recall can hydrate entangled peer memories, but only as a separate,
  labeled prompt block

This preserves ownership and provenance while still letting the council behave
like an interconnected memory fabric.

---

## Trust Model

### Bayesian Trust Updater

Trust is updated when a new source confirms or contradicts a claim.

**Algorithm:**

```
1. Convert probability to odds:     odds = p / (1 − p)
2. Compute likelihood ratio:
     if confirms:    lr = reliability / (1 − reliability)
     if contradicts: lr = (1 − reliability) / reliability
3. Apply independence discount:
     discount = 0.5 ^ n_correlated
       where n_correlated = count of prior sources in same independence_group
     lr_adj = lr ^ discount
4. Update odds:                     odds_new = odds × lr_adj
5. Convert back to probability:     p_new = odds_new / (1 + odds_new)
6. Clamp to [0.01, 0.99]
```

### Trust Priors

| Node Type        | Prior |
|------------------|-------|
| `identity`       | 0.90  |
| `preference`     | 0.80  |
| `goal`           | 0.70  |
| `event`          | 0.60  |
| `procedure`      | 0.60  |
| `fact`           | 0.50  |
| `rule`           | 0.50  |
| `risk`           | 0.40  |
| `interpretation` | 0.35  |
| `association`    | 0.30  |

### Independence Discount

When multiple sources share the same `independence_group` (e.g., pages from the
same domain), each additional source contributes less:

```
discount(n) = 0.5 ^ n

Source 1: full effect           (discount = 1.0)
Source 2: half effect           (discount = 0.5)
Source 3: quarter effect        (discount = 0.25)
...
```

This prevents "rumor amplification" — ten copies of the same claim from the same
blog don't make it ten times more trustworthy.

### Usefulness Scoring

```
trust_useful = base_usefulness × trustworthiness
```

- `base_usefulness` is adjusted by bump/penalize events
- `trustworthiness` comes from Bayesian updates
- `trust_useful` is the core memory-value score used by retrieval, retention,
  and quarantine
- retrieval also blends query match, `salience`, `centrality`, recency, and
  source reliability for dynamic ranking

---

## Sleep / Default-Mode

When in `ASLEEP` mode, the agent runs periodic consolidation cycles roughly
every 10 seconds.

### Graph Walker

The `GraphWalker` performs **spreading-activation walks** over the memory graph:

1. **Seed selection** — Sample seed nodes weighted by
   `centrality × recency × emotion × salience`
2. **Bounded walk** — Starting from the seed, traverse edges probabilistically
   weighted by edge `weight`. Respect budget constraints:
   - `sleep_max_nodes_per_cycle` (default: 100 nodes)
   - `sleep_max_time_ms` (default: 5000 ms)
3. **Edge reinforcement** — Frequently-traversed edges have their weight
   increased (up to 5.0). Rarely-used edges are weakened (down to 0.01).
4. **Mycelium entanglement** — The scheduler may derive a bounded query
   signature from the walked local nodes, search peer export snapshots, and
   reinforce overlay links in `mycelium.db`
5. **Interrupt check** — The walker checks for operator interrupts at each step
   and can abort mid-walk.

**Output:** `WalkResult` containing visited nodes, edge activations, proposed
actions, step count, timing, and interrupt status.

### Consolidation Engine

The `ConsolidationEngine` processes walk results and **proposes** (never applies)
changes:

| Proposal Type           | Trigger                                  | Output                         |
|-------------------------|------------------------------------------|--------------------------------|
| **MergeProposal**       | `SAME_AS` or `MERGE_CANDIDATE` edges    | Combined text, reason, confidence |
| **AbstractionProposal** | 3+ related nodes in close proximity      | Proposed abstract node         |
| **ContradictionChain**  | `CONTRADICTS` edges (union-find chains)  | Conflict pairs with severity   |

Proposals are queued to `data/state/consolidation_queue.json` and processed
during the next heartbeat cycle.

### Sleep Scheduler

The `SleepScheduler` orchestrates timing:

```
should_run(mode) → True if mode == ASLEEP and time since last cycle ≥ ~10 seconds
run_cycle(walker, engine, interrupt_check, post_walk_callback) → CycleResult
```

It applies local walker actions, optionally runs a post-walk callback for
cross-agent entanglement updates, and persists state in
`data/state/sleep_state.json`.

---

## Heartbeat Cycle

The heartbeat is a periodic self-assessment that runs every
`awake_duration_minutes` (default: 15 minutes). When the awake period elapses the
agent enters a wind-down phase (default: 1 minute) during which it writes a new
`heartbeat.md`, then transitions to sleep for `sleep_duration_minutes` (default:
60 minutes). The full cycle is: **sleep → wake → heartbeat/wind-down → sleep**.

### 10-Step Process

1. **Load current state** — Read current soul, heartbeat, memory stats
2. **Drain consolidation queue** — Process merge, abstraction, and contradiction
   proposals from sleep cycles
3. **Summarise recent activity** — Aggregate activity log since last heartbeat
4. **Identify top priorities** — Rank active goals and unresolved questions
5. **Review contradiction status** — List unresolved contradiction chains
6. **Compute memory statistics** — Node counts by type, status, trust range
7. **Evaluate emotional state** — Aggregate emotion scores across recent memories
8. **Draft heartbeat document** — LLM generates new heartbeat from all inputs
9. **Persist heartbeat** — Write `heartbeat.md` and archive to
   `data/heartbeat_history/`
10. **Audit log** — Record heartbeat event in audit trail

---

## Soul Update Policy

The soul (`soul.md`) is the agent's long-term identity document. It evolves
slowly through **constrained updates**.

### Update Rules

1. **Evidence required** — Every soul change must cite source evidence
2. **Confidence threshold** — Changes require confidence ≥
   `soul_update_min_confidence` (default: 0.7)
3. **Versioning** — Every version is archived in `data/soul_versions/` with
   a timestamp
4. **Audit trail** — Soul updates are logged as `soul_update` mutations

### Rejection Patterns

The following patterns are **rejected** by the soul update policy:

- Changes based solely on untrusted sources (Moltbook, low-reliability web)
- Changes that contradict the ethical anchor without strong multi-source evidence
- Wholesale rewrites (the soul evolves incrementally, not wholesale)
- Changes driven by high-emotion, low-trust content (quarantine territory)

---

## Retention Pipeline

Memory nodes move through three lifecycle stages:

```
  ACTIVE ───────────▶ ARCHIVED ──────────▶ PURGED
       (archive_days)          (purge_days)
```

### Stage Transitions

| Transition        | Condition                                                  |
|-------------------|------------------------------------------------------------|
| Active → Archived | `days_idle > 30` AND `trust_useful < 0.1` AND `centrality < 0.05` |
| Archived → Active | `access_count ≥ 10` AND `trust_useful ≥ 0.5` (promotion)  |
| Archived → Purged | `days_since_archive > 90` AND no protection rules apply   |

### Protection Rules (Never Purged)

| Rule                                  | Rationale                        |
|---------------------------------------|----------------------------------|
| `canonical = 1`                       | Core established knowledge       |
| Source kind = `niscalajyoti`          | Ethical anchor is permanent      |
| Node in unresolved contradiction set  | Must be resolved before removal  |
| Source timestamp within retention window | Recently sourced, give it time |

---

## Ethical Anchor

AgentGolem's ethical framework is rooted in [Niscalajyoti.org](https://www.niscalajyoti.org/).

### Integration

- The `NiscalajyotiTool` periodically fetches content from niscalajyoti.org
- Revisit frequency: `niscalajyoti_revisit_hours` (default: 168 hours / weekly)
- Content is encoded as memory nodes with `SourceKind.NISCALAJYOTI`

### Special Scoring

Niscalajyoti-derived nodes receive protected scores:

| Score              | Value | Reasoning                                |
|--------------------|-------|------------------------------------------|
| Reliability        | 0.9   | Trusted ethical source                   |
| Trustworthiness    | High  | Seeded with high prior, reinforced over time |
| Base usefulness    | High  | Core ethical knowledge is always useful  |
| Retention          | ∞     | Never archived or purged                 |

### Influence

Niscalajyoti content shapes how the agent reasons about:
- Right action and compassion
- The nature of consciousness and existence
- Conflict resolution in contradiction chains
- Soul evolution (ethical changes carry extra weight)

---

## Runtime Loop

### Mode-Based Dispatch

The main loop (`runtime/loop.py`) dispatches based on `AgentMode`:

```python
while running:
    interrupt_check()           # Always check for operator interrupts first

    match state.mode:
        case AgentMode.AWAKE:
            process_inbox()     # Handle human messages
            run_tools()         # Execute pending tool actions
            check_heartbeat()   # Run heartbeat if due
            check_niscalajyoti()# Revisit anchor if due

        case AgentMode.ASLEEP:
            if scheduler.should_run():
                scheduler.run_cycle(walker, consolidation, interrupt_check)

        case AgentMode.PAUSED:
            await sleep(1)      # Do nothing, just wait for commands
```

### Interrupt Preemption

The `InterruptManager` (`runtime/interrupts.py`) allows the operator to
interrupt the agent at any point:

- **Mode transitions** — `wake`, `sleep`, `pause`, `resume` take effect
  immediately
- **Messages** — Queued to inbox, processed on next AWAKE cycle
- **Walker interrupts** — Sleep-cycle graph walks check for interrupts at each
  step and abort if requested

The agent is **always interruptible**. Human oversight is a first-class feature,
not a limitation.
