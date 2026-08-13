# Eyle

**Version:** 2.7.5 · **Schema:** 2.7.5-r1.3.4 · **Revision:** rev1.3.4-fresh-claim-token-cleanup

Eyle is a supervised agent runtime built around one Main LLM, deterministic capabilities, canonical physical Observation, Main-owned Investigation/Tasks and optional independent Claim review.

## Rev1.3.4: Fresh Claim & Token Cleanup

Rev1.3.4 restores Claim to its intended role: a fresh delivery-gate call, not a second participant in Main's investigation. In default `fresh` mode it reuses Main's transport/model but starts with no Main conversation/history and receives only the original Request, the Candidate Final and Main-selected observed Material. `verified` may use a distinct verifier transport/model. Claim can only `accept` or `challenge`; it cannot plan, call capabilities, mutate Investigation/Tasks or rewrite the answer.

The fixed 12k Claim reserve, Claim anchors/runtime-fact packet, hard 3-issue/4-ref/160-character quotas, `operational_feedback` projection, empty `task_state` projection and duplicated Investigation/Task JSON-schema variants are removed. Main's fixed system prompt is compacted. A first semantic challenge returns the complete blocker set to Main for one revision; a second semantic challenge fails explicitly as `CLAIM_CHALLENGE_UNRESOLVED` instead of creating an unbounded Main↔Claim loop. Claim still retains one protocol retry for malformed/truncated structured output.

## Rev1.3.3: Ownership Cleanup

Rev1.3.3 is a surgical cleanup revision: Runtime diagnostics now live in Runtime, write-transaction state lives with transaction mechanics, dead compatibility-shaped telemetry/config is removed, and unused Core/model projection helpers are deleted. The canonical capability result is now exactly status + `observations` + `coverage` + `frontiers`; opaque continuation handles remain Runtime-private. It adds no planner, no compatibility bridge and no new semantic authority.

## Rev1.3.2: Bounded Context Projection

Rev1.3.2 keeps the Rev1.3.1 workspace/self boundary intact and closes the token-economy failure exposed by long self-analysis runs. Canonical Observation still retains complete physical state, while Main receives an incremental projection: fresh tool-result deltas, fresh Observation rows, Investigation-pinned Material coordinates, a tiny Material recency tail, and every still-open Frontier. Cached observations no longer rematerialize full prior payloads; replay returns coordinates plus short recall excerpts.

Rev1.3.2 historically introduced a fixed `claim_reserve_tokens` budget. Rev1.3.4 removes that reservation: Claim now fits its fresh review packet against the actual physical budget remaining after a Candidate Final exists.

## Rev1.3.1: Workspace/Self Boundary Closure

Rev1.3.1 makes `workspace/` the only automatically writable work plane even when it is empty. Eyle source is a separate `source=eyle` observation/self-sandbox source: Main may inspect it, may experiment on an isolated writable copy, and may export that copy only as a non-overwriting ZIP artifact. No modified self-source files can be promoted back into the running installation. Task Memory from Rev1.3 remains unchanged except for clearer activation guidance on multi-action work.

## Rev1.3: Task Memory

> **Eyle constrains effects, not thought.**

Rev1.3 adds the smallest intentional-memory contract needed for longitudinal work inside one AgentSession. The physical architecture from Rev1.2.3.2.2 remains intact; this revision addresses the separate problem that knowing what happened does not by itself preserve what Main has decided is still left to do.

### Tasks

`Task` is Main-owned semantic state with exactly five fields:

```text
id
parent_id
description
status: open | completed | dropped
result
```

Tasks are recursive through `parent_id`, so one task may expand into thousands of subtasks without introducing Epic/Milestone/Step taxonomies. Tree position is composition, not execution order. Main may revisit any branch, revise a task, or work across branches by issuing the updates it considers necessary.

Main alone decides whether a task is created, completed or dropped. Runtime validates only structure and persistence: exact shape, stable IDs, existing parents and an acyclic parent graph. Runtime never closes a parent because its children are closed, never maps `exit 0` to semantic completion, and never blocks Final because an open task exists. Closed tasks retain a concise `result`, preserving what Main says was accomplished or why work was abandoned.

`Investigation` remains separate and epistemic: **what am I trying to understand?** `Task` is intentional: **what did I decide I need to do?** Observation remains physical: **what did reality show?** Claim remains a lateral critic and cannot mutate either Investigation or Tasks.

The structured Main envelope is now:

```text
{action, investigation_updates, task_updates}
```

`AgentSession.tasks` is canonical persisted state. Omitted task IDs remain unchanged across turns. Accepted/rejected task mutations are recorded in DecisionLedger for observability, but DecisionLedger does not own task meaning.

Rev1.3 also renames the old operational `AgentSession.task_id` to `execution_id`; that identifier always meant the physical run/job reference and is no longer allowed to collide conceptually with semantic Tasks. This is a clean break: Rev1.2.x sessions/config/queue/project-memory state are rejected rather than migrated.

The release intentionally does **not** add a Planner, scheduler, focus queue, task priority system, generic cognitive ledger, Memory Kernel, embeddings, tags or automatic convergence gate. Task Memory is the minimum experiment.

The Rev1.2.3.2.2 Microsandbox closure remains active: `backend=auto` resolves Microsandbox → Docker → Bubblewrap; native Windows stages the disposable workspace through guest filesystem copy, while Linux/macOS may use the disposable bind-mounted snapshot. The real workspace is never authorized by sandbox mutation.

The architecture remains deliberately small:

```text
User
  ↓
Main
  ↕
Runtime / capabilities
  ↓
Observation
  ↓
Claim?
  ↓
User
```

### Material + Coverage + Frontier

```text
Capability
├─ observations → Material candidates
├─ coverage     → what physical scope was examined
└─ frontiers    → what objective reality remains accessible
        ↓
Observation
├─ mat-*  generic physical material
├─ Coverage canonical physical map
└─ fr-*   public continuation refs
          ↓
   Runtime-private snapshot + cursor
```

A file is only one locator kind. Future network, database, device, HTTP or sensor capabilities can emit their own locator/version semantics without changing Observation.

A Frontier does not duplicate its source payload. One immutable private snapshot may back many lightweight continuation cursors and is garbage-collected when no cursor remains.

Coverage and Frontier are intentionally orthogonal: a capability can completely examine its declared search scope while still exposing a Frontier because only part of the resulting material has been projected to Main.

### Capability independence

Adding a new observational capability should require its registry/implementation and its own tests — not branches in Agent, Observation, Claim, Investigation or DecisionLedger. The registry is the domain owner; Core consumes the canonical physical envelope generically.

### Investigation and Claim

`Investigation` remains an optional Main-owned notebook. Claim receives only the original Request, Candidate Final and Main-selected observed Material. It does not receive Investigation, Tasks, Main history, Runtime event history or filesystem access.

## Core rules

- Main owns semantics.
- Runtime owns physical effects.
- Claim challenges conclusions; it does not plan.
- Observation owns physical material once.
- Coverage is physical completeness, never semantic sufficiency.
- Frontier is available continuation, never an instruction.
- Invalid/unsafe effects are blocked; safe independent work may continue.
- Git/`CHANGELOG.md` retain history instead of compatibility bridges in current Core.

## Public capabilities

Rev1.3.2 exposes **17** deterministic capabilities:

`calculate`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `symbol_relations`, `continue_observation`, `find_symbol`, `read_file`, `run_command`, `export_sandbox_zip`, `memory_search`, `memory_store`, `run_tests`, `git_status`, `git_diff`.

Execution trace remains internal diagnostics. Real workspace writes are not a public tool; Main emits patches and Runtime owns dry-run, confirmation, apply, verification and rollback.

## Sandboxed execution

`run_command` executes only inside a disposable copied workspace under a strong sandbox backend. `auto` prefers Microsandbox, then Docker, then Bubblewrap. The Microsandbox backend is an embedded per-job microVM laboratory; sandbox mutation never modifies or authorizes the real workspace. On Windows, Microsandbox currently depends on Windows Hypervisor Platform (WHP).

See [SECURITY.md](SECURITY.md).

## Physical containment

The only tight model-window constraint of this deployment is the llama-server ceiling:

```text
context_window_tokens = 38000   # hard per model call
```

The remaining task-wide containment is deliberately minimal:

```text
max_total_tokens       90000
task_deadline_seconds    1800
```

There are no cumulative prompt/completion quotas, no fixed LLM-turn/tool-call quota and no standing Claim reserve. The 90k total-token fuse and deadline remain physical runaway containment. Once Main produces a Candidate Final, Claim uses only the physical headroom that actually remains; lack of enough review headroom fails closed instead of pre-starving Main.

## Run

**Runtime:** Python 3.11+

```bash
python -m pip install -r requirements.lock
python main.py status
python main.py perguntar "Analyze the project"
python main.py serve
```

Development verification:

```bash
python -m pip install -r requirements-dev.lock
python -B -m eyle.devtools.release_identity
python -B -m pytest -q
python -B -m compileall -q eyle llm web main.py
node --check web/static/app.js
```

Run release-identity validation on a clean extracted tree; remove generated `__pycache__`/`.pytest_cache` state before packaging.

## Documentation

- [Architecture](docs/architecture.md)
- [Technical overview](docs/technical-overview.md)
- [Architectural direction](docs/architectural-direction.md)
- [Configuration](docs/configuration.md)
- [Benchmark/regression contract](docs/benchmark.md)
- [Publishing](docs/github-publishing.md)
- [Changelog](CHANGELOG.md)
