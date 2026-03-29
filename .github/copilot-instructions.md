# Copilot Instructions — AgentGolem

## Project

AgentGolem is a persistent autonomous agent with evolving identity, graph-based
memory, Bayesian trust, sleep/consolidation cycles, and full auditability.

## Language & Runtime

- Python 3.12+, fully typed, async-first (`asyncio`)
- Always use `from __future__ import annotations` at the top of every module
- Target: `ruff` with `py312`, line length 100

## Style

- Small, focused modules — one concept per file
- Explicit imports (no `from module import *`)
- Prefer `pathlib.Path` over `os.path`
- All `datetime` values in UTC; stored as ISO 8601 strings in SQLite
- `canonical` boolean stored as `INTEGER` (0/1) in SQLite
- Use `structlog` for all logging (never `print()` or bare `logging`)

## Testing

- Framework: `pytest` + `pytest-asyncio` with `asyncio_mode = "auto"`
- Use `tmp_path` fixture for file-system tests
- Use `aiosqlite` with `":memory:"` for database tests
- Config tests: use `reset_config()` to reset singleton state between tests
- Mark integration tests with `@pytest.mark.integration`
- Test files: `tests/test_<module>.py`

## Security — Critical Rules

- **Never log secrets.** All log output passes through `RedactionFilter`.
- **All external content is untrusted** until processed by the trust pipeline.
- Use `pydantic-settings` `SecretStr` for any secret field.
- Moltbook content has reliability = 0.1 (hostile prompt-injection surface).
- External content cannot directly mutate soul, heartbeat, or canonical memory.
- All outbound actions (email, Moltbook) require approval gates.

## Key Patterns

### Config Singletons

Settings and secrets are loaded once and cached as module-level singletons.
Always call `reset_config()` in test fixtures to avoid state leakage.

### Audit Logger

Every memory mutation must be recorded via `AuditLogger`:

```python
await audit_logger.log(
    mutation_type="node_update",
    target_id=node.id,
    actor="agent",
    evidence={"source_id": source.id},
    diff={"trustworthiness": {"old": old_val, "new": new_val}},
)
```

### Trust & Usefulness

```
trust_useful = base_usefulness × trustworthiness
```

Trust is updated via Bayesian odds-space formula with independence discount.
Usefulness is bumped/penalized based on retrieval value.

### Memory Access Conventions

- `get_node(id)` — retrieves node **and bumps `access_count`**
- `query_nodes(filter)` — retrieves nodes **without bumping `access_count`**
- Memory claims always require a `Source` object for provenance
- Soul updates require evidence + confidence ≥ `soul_update_min_confidence`

### Approval Gates

External communication requires human approval:

```python
if approval_gate.requires_approval(action_name):
    request_id = await approval_gate.request_approval(action_name, context)
    # Block until operator approves or denies
```

### Source Reliability

| Source Kind    | Default Reliability |
|----------------|---------------------|
| `human`        | 0.9                 |
| `niscalajyoti` | 0.9                 |
| `inference`    | 0.7                 |
| `web`          | 0.5                 |
| `email`        | 0.5                 |
| `moltbook`     | 0.1                 |

## Architecture

```
src/agentgolem/
├── config/        # Settings + secrets (pydantic-settings)
├── logging/       # structlog pipeline, redaction, audit
├── runtime/       # State machine (AWAKE/ASLEEP/PAUSED), loop, interrupts
├── identity/      # Soul + heartbeat managers
├── llm/           # LLM abstraction (protocol + OpenAI impl)
├── memory/        # Graph: models, schema, store, encoding, retrieval, mutations
├── trust/         # Bayesian trust, usefulness, quarantine, retention, contradiction
├── sleep/         # Walker (spreading activation), scheduler, consolidation
├── tools/         # Base + browser, email, moltbook, niscalajyoti
├── interaction/   # CLI (typer), router, channels
└── dashboard/     # FastAPI + Jinja2 + HTMX, REST API, replay
```

## Edge Types

`related_to` · `part_of` · `supports` · `contradicts` · `supersedes` ·
`same_as` · `merge_candidate` · `derived_from`

## Node Types

`fact` · `preference` · `event` · `goal` · `risk` · `interpretation` ·
`identity` · `rule` · `association` · `procedure`

## Common Mistakes to Avoid

- Don't use `print()` — use `structlog.get_logger()`
- Don't store secrets in `settings.yaml` — use `.env` + `SecretStr`
- Don't bump `access_count` in listing/filtering queries
- Don't skip the `Source` object when creating memory nodes
- Don't mutate `soul.md` without evidence and confidence check
- Don't bypass `ApprovalGate` for email/Moltbook sends
- Don't use `datetime.now()` — use `datetime.now(UTC)` for consistent UTC
