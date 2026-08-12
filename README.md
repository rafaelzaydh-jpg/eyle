# Eyle

**Version:** 2.7.5 · **Schema:** 2.7.5-r1.3 · **Revision:** rev1.3-task-memory

Eyle is a supervised agent runtime built around one Main LLM, deterministic capabilities, canonical physical Observation, Main-owned Investigation/Tasks and optional Claim review.

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

`Investigation` remains an optional Main-owned notebook. Claim receives only Request, provisional Final, Main-selected physical grounding and compact Runtime facts. It does not inspect Investigation or the filesystem.

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

Rev1.3 exposes **16** deterministic capabilities:

`calculate`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `symbol_relations`, `continue_observation`, `find_symbol`, `read_file`, `run_command`, `memory_search`, `memory_store`, `run_tests`, `git_status`, `git_diff`.

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
max_total_tokens         90000
task_deadline_seconds 1800
```

There are no cumulative prompt/completion budgets and no fixed LLM-turn, LLM-call or tool-call quota. The 90k total-token fuse and deadline are physical runaway containment, not semantic stopping rules. Main sees remaining physical headroom in its factual operational view and still decides whether to continue, change approach or finish.

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
python -B -m pytest -q
python -B -m compileall -q eyle llm web main.py
python -B -m eyle.devtools.release_identity
node --check web/static/app.js
```

## Documentation

- [Architecture](docs/architecture.md)
- [Technical overview](docs/technical-overview.md)
- [Architectural direction](docs/architectural-direction.md)
- [Configuration](docs/configuration.md)
- [Benchmark/regression contract](docs/benchmark.md)
- [Publishing](docs/github-publishing.md)
- [Changelog](CHANGELOG.md)
