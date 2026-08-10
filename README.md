<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="760">
</p>

# Eyle

**Version:** 2.7.4 · **Schema:** 5.6 · **Revision:** rev5.6-grounded-outcomes-docker-backend

## Rev5.6 — Grounded Outcomes & Docker Backend

Rev5.6 keeps the canonical task-input and property-directed architecture of Rev5.5.5, then fixes the verification/execution boundary exposed by the next benchmarks: Claim grounding is no longer synonymous with EvidenceLedger IDs, non-retryable physical failures become terminal capability facts for the current job, `symbol_relations` understands common registration/binding structures and can project only the requested edge direction, and `run_command` uses a persistent Docker sandbox by default when Docker is available.

> **The Main LLM decides what must be done. Runtime decides what may physically happen. Claim independently challenges the result.**

### Semantic authority

```text
USER
 ↓
Main LLM
 ├─ chooses tools
 ├─ decides whether semantic debt exists
 └─ creates Investigation only when needed
        ↓
      Tools → Observation → Evidence
        ↓
      Main LLM → Final
        ↓
      Claim Review
        ├─ accepted → user
        └─ semantic debt → Main LLM
```

`Investigation=[]` is valid and means only that the Main LLM has declared no persistent semantic debt. Workspace access does not imply Investigation.

If the Main LLM declares a target, Runtime preserves that commitment mechanically: the target cannot disappear or silently change goal, `established` requires real Evidence, and an open target blocks Final acceptance. Runtime never invents a target.

Claim has one global semantic review path. It may report a missing material debt with `target_id=null`; only the Main LLM may decide to create a new Investigation target.


### Grounded outcomes

Claim verifies the provisional answer against typed grounding coordinates instead of forcing every conclusion through EvidenceLedger:

```text
request                     → canonical user task
answer:<anchor>             → bounded answer anchor
evidence:<id>               → citable source/tool Evidence
runtime:<fact>              → objective execution/runtime fact
investigation:<target>      → declared semantic debt
```

A material omission may be grounded by the request and answer; an external code fact normally needs source Evidence; a physical impossibility such as an unavailable sandbox may be grounded by Runtime Facts. `blocked` is a truthful material outcome when physical reality prevents execution. Runtime validates that cited coordinates exist; it does not decide which coordinates are semantically sufficient.

Non-retryable tool failures are recorded in `ExecutionContext.terminal_capabilities`. The capability disappears from the callable view for the rest of that job, preventing repeated attempts at a physical condition that cannot change during the execution.

### Progressive capabilities and general tools

The first Agent call no longer receives all expanded tool contracts. It receives `capability_index`: compact signatures plus purpose for unused tools. A tool can be called immediately from that index; no selector/router call exists. After the Main LLM actually requests a tool, later calls move it to `active_tools` with the expanded contract. Activation is derived from the DecisionLedger, not persisted as another state machine.

### Training budget

One user message/job has a hard physical envelope:

```text
Llama Server context per request  <= 32768
prompt attempts per message/job   <= 90000
generated output per message/job  <= 8000
physical total per message/job     <= 98000
```

Every backend attempt charges its full estimated prompt even when cached. Cache weighting remains diagnostic only. Turns/tools/calls/deadline remain independent fuses. Budget exhaustion fails the task; no progress-earned extension exists.

In default `self_check`, a Final with no Observation, Evidence, Investigation or WriteTransaction skips Claim because there is no grounded runtime state to audit. Explicit `verified` mode still verifies every Final. This is state-based, not a chat/simple-task classifier.


### Canonical state ownership

Rev5.6 applies the ObservationLedger rule across the runtime: **one factual responsibility, one canonical owner; histories, counters and prompt/UI views are derived.**

```text
ObservationLedger  → physical tool reality / replay / coverage
EvidenceLedger     → citable factual Evidence lifecycle
DecisionLedger     → decisions + deterministic rejection identity
LLMCallLedger      → logical LLM calls + provider attempts
WriteTransaction   → mutation lifecycle / validation / rollback
Investigation      → semantic debt declared by Main LLM
ClaimReview        → independent semantic audit
```

`ExecutionContext` owns run-scoped physical budgets/deadline and LLMCallLedger. Configuration is not mutated into execution state. There are no parallel `prompt_snapshots`/`llm_responses`, `decision_history` state, Session `tool_history`, or duplicated pending patch payloads.

### What Rev5.6 removed

- `workspace_scope`; physical reads/writes are observable from tools and patches;
- `final.evidence_ids` / `answer_evidence_ids`; Investigation owns target Evidence and Claim sees runtime Evidence;
- lexical `request_policy` and the parallel Claim `findings[]` subsystem;
- generic `AGENT_NO_PROGRESS`; only deterministic repeated rejected/replay decisions are fused;
- parallel `relevant_sources` / `visible_source_ranges`; ObservationLedger owns observation identity and coverage;
- persisted Claim follow-up copies; follow-up is derived from the canonical Claim Review;
- duplicate post-write tool reread; deterministic full-output verification remains;
- public `read_range`; `read_file` accepts optional `line_start` / `line_end`;
- Agent-side tool-class sets; the executable `TOOLS` registry owns availability and Evidence production;
- `INVESTIGATION_REQUIRED` and workspace→Investigation coupling;
- semantic task router / lexical fast paths;
- task classes such as simple/complex/utility/analysis;
- semantic phase scheduler (`analysis_*`, `write_*` steering);
- Progress Earned Authority and “+4 tools” extensions;
- specialized Claim/Gaps/Findings recovery lanes;
- obsolete Investigation snapshot helpers and dead session state;
- Final-as-string and other superseded runtime interfaces;
- session, queue and project-memory migration bridges;
- legacy tool argument aliases and mixed internal record aliases;
- index-based prompt/response correlation fallback;
- structured-output capability negotiation/cache, `json_object`/prompt downgrade and structural repair calls;
- automatic `finish_reason=length` re-call and Agent-specific transport retry policy;
- Claim/Gaps recovery identity fields (`claim.id`, `claim.kind`, `semantic_gap.id`, signatures);
- revision-specific tests that existed only to keep deleted APIs alive.

### No backward compatibility

Rev5.6 has one canonical runtime contract. Previous Eyle session, queue, project-memory and configuration schemas are not resumed or migrated. Incompatible persisted state fails explicitly.

Current schemas:

```text
config/session/queue/project-memory → 5.6
```

Current transport portability is explicit, not a compatibility layer: structured Agent/Claim calls require strict JSON Schema. OpenAI-compatible and Ollama transports are supported only when they honor that canonical structured mechanism. The Python search fallback when `rg` is unavailable remains current operational portability.

## Runtime responsibilities

Runtime remains deliberately non-semantic. It owns:

- deterministic tool validation/execution;
- Evidence identity, hashes and freshness;
- Observation Ledger and physical replay protection;
- workspace epoch;
- path/security boundaries;
- write dry-run, confirmation, transaction, verification and rollback;
- persistent queue/memory schemas;
- token/tool/turn/deadline **physical fuses**;
- sanitized execution trace and telemetry.

Physical limits never decide whether a semantic investigation is complete.

## Main LLM responsibilities

The Main LLM owns:

- understanding the request;
- deciding what observable fact would settle a material property;
- choosing tools;
- deciding whether persistent semantic debt exists;
- creating/updating Investigation targets;
- deciding Evidence relevance/sufficiency;
- deciding when to stop investigating;
- proposing writes;
- producing the user-facing Final.

## Investigation Contract

Canonical target shape:

```json
{
  "id": "T1",
  "goal": "Establish whether the module participates in active runtime flow",
  "status": "open",
  "evidence_ids": [],
  "reason": ""
}
```

Statuses: `open`, `established`, `dismissed`.

`reason` is the Main LLM's semantic argument, never factual authority. Evidence remains the factual substrate.

## Tools

Eyle exposes 17 deterministic public tools:

`calculate`, `agent_info`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `symbol_relations`, `find_symbol`, `read_file`, `run_command`, `memory_search`, `memory_store`, `run_tests`, `execution_trace`, `git_status`, `git_diff`.

Writes are not public tools. The Main LLM emits one canonical `patches` transaction and Runtime owns dry-run/confirmation/apply/verification/rollback.

## Docker-first sandbox backend

`run_command` is free to create/delete files, install packages, download dependencies, compile and execute arbitrary commands inside its disposable job sandbox. `backend=auto` prefers Docker and falls back to Bubblewrap. Docker uses one persistent container per job (default image `python:3.12-slim`, pulled on demand when missing), so package/toolchain installation and root-filesystem changes survive later `run_command` calls in the same job.

The real workspace is never mounted read-write. Runtime first creates a sanitized snapshot, mounts only that copy as `/workspace`, omits protected secrets, and destroys the container/snapshot at job end. If no strong backend is available, `run_command` returns a non-retryable `SANDBOX_UNAVAILABLE` Runtime Fact instead of falling back to a trusted local process.

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

- [Architecture](docs/architecture.md)
- [Technical overview](docs/technical-overview.md)
- [Configuration](docs/configuration.md)
- [Benchmark](docs/benchmark.md)
- [Publishing](docs/github-publishing.md)
- [Historical changelog](CHANGELOG.md)
- [Português](README.pt-BR.md)

## License

Eyle is **source-available, not open-source software**. See [LICENSE.md](LICENSE.md).
