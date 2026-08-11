<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="760">
</p>

# Eyle

**Eyle is a source-available coding agent for repository investigation, evidence-grounded answers, sandboxed execution, and supervised source changes.**

**Version:** 2.7.4 · **Schema:** 5.8 · **Revision:** rev5.8-objective-projection-evidence-admission

Eyle is useful when the answer is not in one file or one grep result. It can trace execution across modules, inspect contracts and data flow, determine whether a symbol is structurally reachable from real entrypoints, distinguish established absence from unresolved static uncertainty, run commands/tests in an isolated project snapshot, and apply confirmed patches through a rollback-capable write transaction.

## What Eyle is for

Typical tasks include:

- **Repository investigation** — find where behavior is implemented and reconstruct the path that reaches it.
- **Execution-path analysis** — establish whether a function, parser, handler, policy or verifier is structurally reachable from a productive entrypoint.
- **Contract and compatibility audits** — locate normalization boundaries, duplicate accepted shapes, aliases, fallback routes and compatibility code.
- **Change-impact analysis** — follow imports, calls, callbacks and structural relationships before changing code.
- **Grounded technical answers** — keep observations and supporting Evidence addressable instead of relying only on model memory.
- **Sandboxed engineering work** — run tests, builds, package installation and arbitrary commands against a disposable writable snapshot rather than the real workspace.
- **Supervised code changes** — dry-run patches, require confirmation, apply through one canonical transaction, verify, and roll back on failure.

Eyle is not intended to replace source control, a test suite, or static analysis with an LLM guess. Its architecture is built so deterministic capabilities do mechanical work over the repository while the Main LLM owns semantic interpretation.

## Why this architecture exists

Large repositories contain much more state than should be copied into every model prompt. Eyle keeps canonical runtime state separate from the model-facing working set:

```text
repository / runtime reality
        ↓
deterministic capabilities
        ↓
Observation → SourceRecords / Coverage / Frontier / Handle
        ↓
bounded objective projection
        ↓
Main LLM
        ↓
explicit Evidence admission → grounded Final
        ↓
Claim Review
```

The Main LLM decides what the request means, what must be established, which capability to use, and when the evidence is semantically sufficient. The Runtime owns execution, schemas, state, replay, safety and physical budgets. Claim Review challenges the grounded delivery without becoming a second planner.

## Measured repository investigation

A repeated message-contract investigation provides a concrete example of why these boundaries matter. An earlier run became trapped around an incorrectly hidden central source file and failed after **20 turns, 29 physical tools and 92,448 estimated physical tokens**. With the corrected Rev5.7.7 boundary, the same class of investigation completed in **4 turns, 8 tools and 32,479 estimated physical tokens**, with one successful Claim Review.

Rev5.8 then reran the same request after introducing Objective Projection and explicit Evidence admission. It still completed in **4 turns and 8 tools**, but the epistemic state changed materially: **52 SourceRecords were objectively materialized, only 2 were promoted to Evidence, both were cited by Claim, and structurally unreferenced Evidence fell from 41 to 0**. The run used **33,747 estimated physical tokens** — about 3.9% more than Rev5.7.7 — while preserving complete declared search coverage, bounded projections and continuation handles.

That small token increase is not treated as a regression by itself. Eyle optimizes for **truthful, grounded, navigable information first**. A few additional model tokens are acceptable when they preserve objective coverage/provenance and give Main an explicit path to materialize more reality if the current projection is insufficient. Token optimization should remove duplicated state, unnecessary inference cycles and irrelevant retransmission; it must not hide uncertainty or discard objective continuation merely to improve a counter.

See [docs/benchmark.md](docs/benchmark.md) for the measured runs, limitations and regression contract. Benchmark numbers are observations, not semantic quotas: Eyle does not impose arbitrary “N tools per task” or “minimum token” rules.

## Directed code observation

For Python structural questions, Main can ask the repository a property-shaped query instead of reconstructing the graph one neighbor at a time:

```text
symbol_relations(
  symbol="_conversation_history",
  query="reachability"
)
```

A positive result can materialize the complete path from a detected entrypoint to the target with edge coordinates. A negative result can expose unresolved physical/static boundaries rather than pretending that “not found” means impossible.

Capability results can carry:

```text
observations[]
coverage
frontiers[]
handles[]
```

`Coverage` describes what the capability objectively examined or established. A `Frontier` describes an unresolved continuation boundary. A `Handle` allows later materialization without keeping the entire observed space hot in model context. These concepts do not tell the Main LLM what is semantically relevant; they make the physical limits of an observation explicit.

## Objective projection and Evidence admission

Rev5.8 separates **what a capability objectively materialized** from **what Main decided is proof**. A capability may exhaust a literal search, AST relation or graph property over a large repository, then deterministically group/page that result. It may not rank which facts are semantically relevant. Materialized `src-*` SourceRecords become `ev-src-*` Evidence only when Main explicitly selects them in Investigation or Final grounding.

For bounded searches, `coverage_complete` describes the searched scope while `projection_complete` describes whether every objective result is inline. Omitted objective ranges remain addressable through opaque handles; they are not silently discarded or semantically filtered.

```text
ObservationLedger   → physical tool reality, replay and coverage
SourceRecordLedger  → objectively materialized citable source records
EvidenceLedger      → Main-admitted Evidence lifecycle and freshness
DecisionLedger     → runtime decisions and deterministic rejections
LLMCallLedger      → logical model calls and provider attempts
WriteTransaction   → mutation, verification and rollback
Investigation      → semantic debt declared by Main
ClaimReview        → grounded semantic audit
```

Canonical ledgers can remain complete while each model call receives only a bounded hot projection: the active request, current Investigation, recent/pinned navigation, current tool deltas and full contracts for only the two most recently requested tools. Older tools remain callable through a compact capability index.

This is how repository size is pushed toward deterministic machine work instead of repeated LLM context materialization.

## One Core contract, flexible adapters

> **Compatibility inside the Core is suspicious. Compatibility behind adapters/capabilities is desirable.**

Core persistence and runtime contracts use one exact current representation. External variability belongs behind boundaries that normalize into that representation. Current examples include OpenAI-compatible/Ollama provider transport and Docker/Bubblewrap environment portability.

Conversation messages entering Core use the canonical shape:

```json
{"role": "user", "content": "..."}
```

Provider/environment diversity must not turn into aliases, dual-read contracts or historical payload tolerance inside AgentSession.

## Protected resources

Eyle does not guess that ordinary source code is secret because it contains names such as `token`, `password` or `api_key`. Normal source remains readable, searchable, structurally analyzable and available to sandbox snapshots.

Only explicit credential/private-key resources are content-restricted. Physical aliases are resolved so symlink/hard-link paths cannot bypass the same boundary. The existence of a protected resource may remain observable while its content is excluded. Public keys, certificates, generic public PEM material and `.env` templates remain readable.

See [SECURITY.md](SECURITY.md) for the exact boundary.

## Tools

Eyle exposes 18 deterministic public tools:

`calculate`, `agent_info`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `symbol_relations`, `expand_observation`, `find_symbol`, `read_file`, `run_command`, `memory_search`, `memory_store`, `run_tests`, `execution_trace`, `git_status`, `git_diff`.

Real project writes are not public tools. Main emits canonical patches and Runtime owns dry-run, confirmation, application, verification and rollback.

## Physical containment

Default job-level fuses:

```text
max_llm_turns          24
max_tool_calls         64
max_llm_calls          32
max_prompt_tokens      90000
max_completion_tokens  8000
max_total_tokens       98000
task_deadline_seconds  1800
backend context window <= 32768
```

These are containment limits, not semantic stopping rules.

## Run

```bash
python -m pip install -r requirements.lock
python main.py status
python main.py perguntar "Analyze the project"
python main.py serve
```

Development and release verification:

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q
python -m eyle.devtools.release_identity
python -m compileall -q eyle llm main.py
node --check web/static/app.js
```

## Documentation

- [Benchmark](docs/benchmark.md) — measured behavior, regression cases and known efficiency headroom.
- [Architecture](docs/architecture.md) — current authority, state and execution contracts.
- [Technical overview](docs/technical-overview.md) — the runtime pipeline and why it scales better than repeated full-context reconstruction.
- [Configuration](docs/configuration.md) — current exact configuration and physical fuses.
- [Architectural direction](docs/architectural-direction.md) — future design goals, not current product claims.
- [Publishing](docs/github-publishing.md) — release packaging and verification.
- [Changelog](CHANGELOG.md) — public releases and pre-public engineering history.

## License

Eyle is **source-available, not open-source software**. See [LICENSE.md](LICENSE.md).
