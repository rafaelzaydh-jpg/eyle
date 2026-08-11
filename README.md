<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="760">
</p>

# Eyle

**Eyle is a source-available coding agent built around explicit semantic authority, deterministic runtime controls, grounded evidence, and supervised project mutation.**

**Version:** 2.7.4 · **Schema:** 5.7.1 · **Revision:** rev5.7.1-directed-observation-context-projection

Eyle is designed for repository analysis, code investigation, command execution in an isolated sandbox, evidence-grounded answers, and confirmation-gated source changes. The Main LLM owns interpretation and strategy; the Runtime owns physical execution, state, safety and budgets; Claim Review challenges grounded delivery without becoming a second planner.

```text
USER
 ↓
Main LLM                 semantic authority
 ↓
Capabilities             deterministic observation/execution
 ↓
Observation → Evidence   canonical factual state
 ↓
Main LLM → Final
 ↓
Claim Review             semantic challenge
 ↓
USER
```

## Design principles

- **One semantic authority.** The Main LLM decides what the request means, what must be established, which tools to use, and when investigation is sufficient.
- **Runtime does not invent semantics.** It validates schemas, executes capabilities, enforces physical boundaries, preserves canonical state, and rejects invalid operations deterministically.
- **World state is not model context.** Canonical ledgers may remain complete while each model call receives only a bounded projection of the state needed for the current turn.
- **Evidence remains grounded.** Source observations, runtime facts and write outcomes remain addressable instead of being reduced to free-form model memory.
- **Writes have one controlled path.** Real project mutation uses a confirmation-gated `WriteTransaction` with dry-run, verification and rollback.
- **No hidden compatibility layer.** Rev5.7.1 accepts only its current config/session/queue/project-memory schemas.

## Directed code observation

Eyle can ask structural questions about a Python project without forcing the Main LLM to reconstruct the repository one symbol at a time.

`symbol_relations(query="reachability")` can search from explicit roots or objective Python entrypoint signals and return a complete root-to-symbol path when one is structurally established.

```text
main.py::<module>
→ main.py::main
→ ...
→ llm/structured.py::parse_claim_review_response
```

Capability results use a common observation envelope:

```text
status / ok / executed / changed / error_code / retryable
observations[]
coverage
frontiers[]
handles[]
detail
```

- `coverage` describes the objective scope/completeness reported by the capability.
- `frontier` identifies an objective continuation boundary not materialized in the current observation.
- `handle` is an opaque continuation reference that can be expanded without replaying the entire observation.

These fields are optional for simple capabilities. They do not make every tool a graph tool, and they do not tell the Main LLM whether further exploration is semantically necessary.

## Investigation and grounded delivery

Persistent semantic debt is represented by an optional Investigation Contract created by the Main LLM:

```json
{
  "id": "T1",
  "goal": "Establish whether the module participates in active runtime flow",
  "status": "open",
  "evidence_ids": [],
  "reason": ""
}
```

Statuses are `open`, `established`, and `dismissed`. Runtime preserves identity and structural invariants but never creates an Investigation target on its own.

Final delivery is explicit about supporting Evidence:

```json
{
  "answer": "...",
  "limitations": [],
  "evidence_ids": ["ev-..."]
}
```

Claim Review can ground its verdict against typed coordinates:

```text
request
answer:<anchor>
evidence:<id>
runtime:<fact>
investigation:<target>
```

Runtime verifies that referenced coordinates exist and are fresh where required. Semantic sufficiency remains a model judgment.

## Context projection

The Runtime preserves canonical state while keeping repeated prompt material bounded.

The Main prompt receives, among other current-turn state:

- the active request and Investigation state;
- Investigation-pinned plus recent Evidence navigation;
- pinned plus recent Observation navigation;
- bounded current tool-result deltas;
- full contracts for only the two most recently requested distinct tools;
- a compact `capability_index` for the remaining callable tools.

Older tools remain callable. There is no Tool Selector, semantic router, task classifier, or persisted activation state.

## Canonical state ownership

```text
ObservationLedger  → physical tool reality, replay and coverage
EvidenceLedger     → citable Evidence lifecycle and freshness
DecisionLedger     → runtime decisions and deterministic rejections
LLMCallLedger      → logical model calls and provider attempts
WriteTransaction   → mutation, validation and rollback lifecycle
Investigation      → semantic debt declared by Main LLM
ClaimReview        → semantic audit
```

Histories, counters, prompt views and UI summaries are projections of these owners, not parallel sources of truth.

## Tools

Eyle exposes 18 deterministic public tools:

`calculate`, `agent_info`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `symbol_relations`, `expand_observation`, `find_symbol`, `read_file`, `run_command`, `memory_search`, `memory_store`, `run_tests`, `execution_trace`, `git_status`, `git_diff`.

Project writes are not public tools. The Main LLM emits the canonical `patches` action and Runtime owns dry-run, confirmation, application, verification and rollback.

## Sandbox and project safety

`run_command` executes inside a strong writable project snapshot. `backend=auto` prefers Docker and falls back to Bubblewrap. The real workspace is never mounted read-write into the unrestricted command environment.

The sandbox may use the network, install packages and toolchains, compile code, and modify its disposable snapshot. This protects the real workspace from direct mutation; it does **not** make source visible inside a network-enabled sandbox confidential. See [SECURITY.md](SECURITY.md) for the full boundary.

If no strong backend is available, unrestricted command execution fails closed with `SANDBOX_UNAVAILABLE` instead of falling back to a trusted local process.

## Physical inference limits

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

These are physical containment limits. They do not decide whether an investigation is semantically complete.

## Run

```bash
python -m pip install -r requirements.lock
python main.py status
python main.py perguntar "Analyze the project"
python main.py serve
```

Development:

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q
```

Release verification:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q eyle llm main.py
python -m pytest -q
node --check web/static/app.js
```

## Documentation

- [Architecture](docs/architecture.md) — current runtime contracts and authority boundaries.
- [Technical overview](docs/technical-overview.md) — execution loop, ledgers, projection and grounding.
- [Architectural direction](docs/architectural-direction.md) — future design goals; not a claim of current capabilities.
- [Configuration](docs/configuration.md) — strict current configuration and physical fuses.
- [Benchmark contract](docs/benchmark.md) — regression and efficiency baselines.
- [Publishing](docs/github-publishing.md) — release packaging checks.
- [Changelog](CHANGELOG.md) — historical release changes.
- [Português](README.pt-BR.md)

## License

Eyle is **source-available, not open-source software**. See [LICENSE.md](LICENSE.md).
