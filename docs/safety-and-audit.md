# Safety and Audit

This document describes AgentGolem's security model, trust pipeline, audit trail,
and the safeguards that prevent the agent from acting unsafely.

---

## Table of Contents

1. [Secret Management](#secret-management)
2. [Redaction](#redaction)
3. [Trust Pipeline](#trust-pipeline)
4. [Quarantine](#quarantine)
5. [Sandboxing](#sandboxing)
6. [Approval Rules](#approval-rules)
7. [Audit Trail](#audit-trail)
8. [Retention Protections](#retention-protections)
9. [Communication Safety](#communication-safety)

---

## Secret Management

All secrets are stored exclusively in the `.env` file at the project root.

| Principle              | Implementation                                              |
|------------------------|-------------------------------------------------------------|
| **Single source**      | All secrets in `.env` only — never in code, config, or logs |
| **Type safety**        | Pydantic-settings `SecretStr` fields prevent accidental serialisation |
| **Redaction**          | `RedactionFilter` scrubs all secrets from log output        |
| **Git exclusion**      | `.env` is listed in `.gitignore`; never committed           |
| **Safe example**       | `.env.example` ships with placeholder values only           |

### Secret fields

```
OPENAI_API_KEY          # LLM provider key
EMAIL_SMTP_PASSWORD     # SMTP credential
EMAIL_IMAP_PASSWORD     # IMAP credential
MOLTBOOK_API_KEY        # Moltbook integration key
```

---

## Redaction

The `RedactionFilter` (in `src/agentgolem/logging/redaction.py`) prevents secret
leakage across all log output.

### How It Works

1. On startup the filter collects the **revealed values** of every `SecretStr`
   field in the settings.
2. It compiles a single regex that matches any of those values.
3. The regex is registered as a **structlog processor** in the logging pipeline.
4. Every log event — activity log, audit log, and console output — passes
   through the filter before being emitted.
5. Any match is replaced with `[REDACTED]`.

### Coverage

| Output channel       | Redacted? |
|----------------------|-----------|
| `activity.jsonl`     | ✅        |
| `audit.jsonl`        | ✅        |
| Console (stdout)     | ✅        |
| Dashboard API        | ✅ (reads from redacted logs) |
| Tool return values   | ✅ (via structlog pipeline)   |

---

## Trust Pipeline

All external content enters AgentGolem as **untrusted** and must pass through the
trust pipeline before it can influence canonical memory.

### Bayesian Trust Update

Trust is maintained as a probability in `[0.01, 0.99]` and updated in
**odds space** for numerical stability.

**Formulas:**

```
odds_old = p_old / (1 − p_old)

# Likelihood ratio from a source with reliability r:
lr_confirm    = r / (1 − r)          # source confirms the claim
lr_contradict = (1 − r) / r          # source contradicts the claim

# Independence discount (prevents rumor amplification):
discount = 0.5 ^ n
  where n = number of prior sources in the same independence_group

# Adjusted likelihood ratio:
lr_adj = lr ^ discount

# Posterior:
odds_new = odds_old × lr_adj
p_new    = clamp(odds_new / (1 + odds_new), 0.01, 0.99)
```

### Independence Discount

Sources in the same `independence_group` (e.g., multiple pages from the same
website) are treated as correlated. Each additional correlated source contributes
exponentially less to the trust update, preventing rumor amplification.

### Trust Priors by Node Type

Each `NodeType` has a default prior trustworthiness:

| Node Type        | Prior | Rationale                                  |
|------------------|-------|--------------------------------------------|
| `identity`       | 0.90  | Self-knowledge is highly trusted           |
| `preference`     | 0.80  | Personal preferences are fairly stable     |
| `goal`           | 0.70  | Goals are intentional but may shift        |
| `event`          | 0.60  | Events depend on source reliability        |
| `procedure`      | 0.60  | Procedures need verification               |
| `fact`           | 0.50  | Facts require external evidence            |
| `rule`           | 0.50  | Rules require justification                |
| `risk`           | 0.40  | Risks are speculative by nature            |
| `interpretation` | 0.35  | Interpretations are subjective             |
| `association`    | 0.30  | Associations are weakest claims            |

### Usefulness Model

Usefulness tracks how valuable a memory node actually is. It is computed as:

```
trust_useful = base_usefulness × trustworthiness
```

**Bump rules** — increase `base_usefulness` when:
- The node is retrieved and used in reasoning
- The node is cited as evidence for a decision
- The node survives a contradiction challenge

**Penalize rules** — decrease `base_usefulness` when:
- The node is retrieved but explicitly rejected
- The node is superseded by a more accurate version
- The node's source is later found unreliable

---

## Quarantine

Nodes that exhibit **high emotion and low trust** are quarantined rather than
deleted.

### Trigger Conditions

A node or cluster is flagged for quarantine when:

```
emotion_score ≥ quarantine_emotion_threshold   (default 0.7)
AND
trust_useful  ≤ quarantine_trust_useful_threshold (default 0.3)
```

### Quarantine Behaviour

| Aspect            | Behaviour                                                    |
|-------------------|--------------------------------------------------------------|
| **Storage**       | Quarantined memory remains in the graph database             |
| **Canonical**     | Quarantined nodes are **not** treated as canonical           |
| **Retrieval**     | Quarantined nodes can be retrieved but are surfaced with warnings |
| **Dashboard**     | Quarantined nodes are marked visually in the memory browser  |
| **Resolution**    | Operator or trust pipeline may later rehabilitate or purge   |

---

## Sandboxing

### Moltbook as Hostile Surface

Moltbook is treated as a **hostile prompt-injection surface**. Content from
Moltbook sources receives a default reliability of **0.1** (very low), meaning
each piece of Moltbook content barely moves trust scores.

### Content Isolation

External content cannot directly:

- Mutate `soul.md` (requires evidence + confidence ≥ `soul_update_min_confidence`)
- Mutate `heartbeat.md` (written only by the heartbeat cycle)
- Set a memory node as `canonical` (requires multiple high-trust sources)
- Bypass the approval gate for email or Moltbook sends

### Tool Output Trust

All tool outputs (web browsing, email reads, Moltbook reads) are tagged with
their `SourceKind` and passed through the trust pipeline. Source kinds and their
typical reliability:

| Source Kind      | Typical Reliability | Notes                          |
|------------------|--------------------|---------------------------------|
| `human`          | 0.9                | Operator input                  |
| `niscalajyoti`   | 0.9                | Ethical anchor (protected)      |
| `inference`      | 0.7                | Agent's own reasoning           |
| `web`            | 0.5                | General web content             |
| `email`          | 0.5                | Incoming email                  |
| `moltbook`       | 0.1                | Treated as hostile              |

---

## Approval Rules

### Configuration

Approval-gated actions are listed in `config/settings.yaml`:

```yaml
approval_required_actions:
  - email_send
  - moltbook_send
```

### Mechanism

1. When the agent calls a tool whose name is in `approval_required_actions`, the
   `ApprovalGate` creates a JSON request file in `data/approvals/`.
2. The request includes: action name, full context/arguments, timestamp, and a
   unique `request_id`.
3. The agent **blocks** on that action until the request is resolved.
4. The operator reviews and approves or denies via CLI or dashboard.
5. The result (approved/denied) and reason are recorded in the audit trail.

### Approval File Format

```json
{
  "request_id": "a1b2c3d4",
  "action": "email_send",
  "context": { "to": "user@example.com", "subject": "...", "body": "..." },
  "status": "pending",
  "created_at": "2025-01-15T10:30:00Z"
}
```

---

## Audit Trail

### Append-Only Log

All mutations and sensitive actions are recorded in `data/logs/audit.jsonl`.
This file is **append-only** — entries are never modified or deleted by the agent.

### Audit Entry Fields

| Field           | Description                                        |
|-----------------|----------------------------------------------------|
| `timestamp`     | ISO 8601 UTC timestamp                             |
| `mutation_type` | Type of change (e.g., `node_create`, `edge_create`, `node_update`, `soul_update`, `approval_resolve`, `council_consensus`, `setting_change`) |
| `target_id`     | ID of the affected entity                          |
| `actor`         | Who performed the action (e.g., `council_1`, `council_3`, `operator`, `system`) |
| `evidence`      | Source evidence that justified the mutation         |
| `diff`          | Before/after values for updates                    |
| `metadata`      | Additional context (tool name, confidence, etc.)   |

### Example Entry

```json
{
  "timestamp": "2025-01-15T10:31:42Z",
  "mutation_type": "node_update",
  "target_id": "n-abc123",
  "actor": "agent",
  "evidence": {"source_id": "s-def456", "kind": "web", "origin": "https://example.com/article"},
  "diff": {"trustworthiness": {"old": 0.50, "new": 0.67}},
  "metadata": {"confidence": 0.82}
}
```

### Traceability

Every memory mutation can be traced to:

1. The **source evidence** that triggered it (web page, email, inference, etc.)
2. The **trust update** that changed scores
3. The **actor** responsible (agent reasoning, operator command, system process)

### Searching the Audit Trail

```bash
# CLI
python -m agentgolem inspect-logs

# API
curl "http://127.0.0.1:8000/api/logs?type=audit&q=soul_update&limit=20"
```

The dashboard Logs page provides a searchable interface for both activity and
audit logs.

### Consciousness Kernel Auditing

Narrative chapters stored in the EKG graph are `identity` nodes and follow
the standard audit pipeline — every `node_create` for a narrative chapter
is logged with a `mutation_type` of `node_create`.  Self-model rebuilds and
internal state updates are persisted to per-agent JSON files and are not
individually audit-logged (they are transient cognitive state, not trust-rated
memory mutations).

---

## Retention Protections

The retention pipeline moves memory through three stages:

```
ACTIVE  →  ARCHIVED  →  PURGED
       (30 days)     (90 days)
```

### Protection Rules

The following node categories are **never purged**, regardless of age or scores:

| Protected Category                   | Reason                                       |
|--------------------------------------|----------------------------------------------|
| **Canonical nodes** (`canonical=1`)  | Core knowledge — highest-confidence facts    |
| **Niscalajyoti-derived nodes**       | Ethical anchor must be preserved              |
| **Nodes in unresolved contradictions** | Contradictions must be resolved before removal |
| **Recently sourced nodes**           | New evidence should not be discarded prematurely |

### Promotion

Nodes can be promoted from archived back to active if they meet promotion
thresholds:

```
access_count     ≥ retention_promote_min_accesses      (default 10)
trust_useful     ≥ retention_promote_min_trust_useful   (default 0.5)
```

### Archive / Purge Thresholds

Nodes are candidates for archiving when:

```
days_since_last_access > retention_archive_days          (default 30)
AND trust_useful       < retention_min_trust_useful      (default 0.1)
AND centrality         < retention_min_centrality        (default 0.05)
```

Archived nodes move to purge after `retention_purge_days` (default 90) if no
promotion occurs and protection rules don't apply.

---

## Communication Safety

### Dry-Run Mode

When `dry_run_mode: true` (the default), all outbound communication actions
(email send, Moltbook post) are **simulated**. The agent records what it
*would have* sent without actually transmitting anything.

### Rate Limiting

External HTTP requests (web browsing) are rate-limited:

```yaml
browser_rate_limit_per_minute: 10    # Max 10 requests per minute
browser_timeout_seconds: 30          # Per-request timeout
```

### No Arbitrary Execution

AgentGolem does **not** execute arbitrary shell commands from untrusted content.
External content (web pages, emails, Moltbook messages) is processed only through
the structured tool and trust pipeline. There is no `exec()`, `eval()`, or
subprocess execution path from untrusted content.

> **Self-evolution exception:** Agents may modify their own source code and
> restart via `start.bat`, but **only** with unanimous Vow-aligned consensus
> from all active council agents. All changes are versioned and logged.

---

## Council Self-Evolution Safety

Agents can inspect and modify their own codebase, subject to strict safeguards:

| Safeguard                    | Description                                                     |
|------------------------------|-----------------------------------------------------------------|
| **Unanimous consensus**      | All active council agents must agree before any code change is applied |
| **Vow-aligned reasoning**    | Every proposal must justify alignment with the Five Vows        |
| **Version control**          | All changes are committed to git before restart                 |
| **No GitHub push**           | Agents are not allowed to push to remote repositories           |
| **Protected parameters**     | Sleep/wake cycle durations cannot be self-modified              |
| **Audit trail**              | Self-evolution proposals and votes are logged in audit.jsonl    |
| **Graceful restart**         | Agents restart by launching `start.bat` in a new terminal, then closing their own — ensuring continuity |
