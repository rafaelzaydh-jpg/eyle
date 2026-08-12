# Architecture — Eyle 2.7.5 Rev1.3

### Rev1.3 Task Memory


Rev1.3 introduces a separate Main-owned intentional state:

```text
Request
  ↓
Main
  ├─ Investigation  "what do I need to understand?"
  └─ Tasks          "what did I decide I need to do?"
       ↓
Runtime persists/validates structure
       ↓
Effects → Observation → Main
```

A Task is exactly `{id,parent_id,description,status,result}` with `status=open|completed|dropped`. `parent_id` forms a recursive acyclic tree but never imposes execution order. Main owns creation, revision and closure. Runtime does not infer semantic completion from tools, observations, child status or exit codes, and open Tasks do not gate Final. Closed Tasks require a concise result so Main's own conclusion survives the next turn.

Investigation and Tasks deliberately remain separate contracts. Similar persistence mechanics are not evidence that epistemic and intentional state are the same semantic object. No generic semantic-state framework is introduced in Rev1.3.

Coverage is now a mechanically enforced Runtime contract. Every non-empty Coverage value has exactly the universal physical fields `scope`, `examined`, `complete`, `boundaries`, with optional domain-owned `facts`; invalid dialects fail the capability contract instead of silently entering Observation.

Frontier snapshots are retained once and pagination detaches only the requested item slice. Continuation Coverage distinguishes `snapshot_exhausted` from `source_materialization_complete`, so an exhausted cursor cannot falsely claim that vanished/unreadable source reality was materialized.

Capability-specific normalization, public/model projections, containment lookup, resource-failure lookup, continuation and freshness are registry hooks. Generic dispatch does not branch on capability names. Registry effect metadata is singular: `observe`, `execute` or `mutate`.

## The whole system

> **Eyle constrains effects, not thought.**

```text
User
  ↓
Main
  ↕
Runtime
  ↓
Claim?
  ↓
User
```

Everything else is an internal responsibility, not another reasoning authority.

## Authority

### Main

Main is the sole task-semantic authority. It decides interpretation, relevance, capability choice, `Investigation`, `Tasks`, grounding use, recovery and stopping.

Core does not prescribe domain strategies such as which code-search tool to prefer, how many files to inspect, when a Frontier is relevant, or when Main should stop investigating.

### Runtime

Runtime is physical authority. It owns capability execution, strict schemas, permissions, sandboxing, transactions, workspace state and physical containment. Capability-specific observation identity belongs to the capability registry; Observation owns only generic physical registration.

Runtime may reject an impossible or unsafe effect. It does not turn a poor semantic choice into a task-level punishment when the effect itself can be safely rejected or cached.

### Claim

Claim is a small adversarial critic over a provisional Final when enabled. It returns only:

```text
{verdict: accept|challenge, issues: [...]}
```

Claim may challenge unsupported, contradicted, over-broad, omitted or internally inconsistent conclusions. It does not plan, select capabilities, prescribe a recovery strategy, rewrite the answer or mutate `Investigation` or `Tasks`. Its canonical output contains at most 3 independent blockers, at most 4 coordinates per blocker and a concise reason. These bounds constrain the protocol artifact, not the model's internal reasoning. Runtime derives enough output reservation from the bounded schema and permits only one protocol recovery after truncation/invalid structured output; repeated failure stays fail-closed.

## Investigation

`Investigation` is an optional Main-owned semantic notebook:

```text
{id, goal, status, grounding_ids, reason}
```

States remain `open`, `established`, or `dismissed`, but they are Main semantics rather than Runtime completion gates.

Runtime validates only physical/structural facts it can know without interpreting the task:

- IDs/shapes are legal;
- referenced `mat-*` material exists when supplied;
- grounding identity remains physically valid.

An open Investigation does not block Final or a valid write. A rejected Investigation update does not cancel an otherwise valid action in the same turn.

## Capability ownership

`ObjectiveScope` is the reference implementation style: mechanically resolve physical scope, report truthful coverage, and never infer semantic relevance. Rev1.2.2 applies that pattern to the full physical output contract.

Every public capability explicitly owns:

- execution;
- memoization identity policy (`signature` callable or explicit `None`);
- Material observation;
- Coverage projection;
- Frontier projection;
- source-specific continuation/freshness/rehydration when that physical domain needs it.

Agent and Observation do not branch on the public capability catalog. Adding a future network/device/database capability should not require teaching Core what that domain means.

## Observation

Observation is the canonical physical memory boundary between Runtime and Main.

```text
Observation
├─ material: mat-*
├─ Coverage
├─ Frontier: fr-*
└─ Runtime-private continuation state
```

### Material

`mat-*` entries preserve citable physical material and provenance through generic `locator + content_hash` identity plus an optional opaque `source_version`. Observation never interprets locator kinds or version semantics. A file hash, HTTP ETag, database row version or device generation can all be capability-owned `source_version` values. There is no SourceRecord/Evidence promotion layer.

### Coverage

Coverage answers only: **what did this capability physically examine or establish?**

The canonical shape is:

```text
scope       declared physical scope
examined    objective measured portion
complete    whether that scope was exhausted
boundaries  optional physical exclusions/limits
facts       optional capability-owned physical facts
```

Coverage never decides whether the user request is semantically satisfied. Scan completeness and model materialization completeness are separate facts.

### Frontier

Frontier means more objective reality remains physically accessible. Main receives stable `fr-*` coordinates and freely decides whether continuation matters.

Opaque handles/cursors remain Runtime-private. Large continuation payloads are stored once in immutable Runtime-private snapshots; each Frontier cursor references the shared snapshot instead of duplicating its payload. The snapshot is garbage-collected after its final handle is consumed.

`continue_observation(frontier=...)` resolves the cursor mechanically and delegates page materialization back to the source capability, so Observation never needs domain-specific continuation logic.

## Replay is memoization

If Main requests an observation already covered at the current workspace epoch, Runtime may return the canonical cached Observation instead of executing the same physical work again.

Replay is telemetry, not semantic debt. It does not create a duplicate Observation and does not trigger a specialized `OBSERVATION_REPLAY_LOOP` task failure. Ordinary physical fuses still contain pathological repetition.

## Operational self-observation

Runtime derives a bounded `operational_feedback` projection from canonical DecisionLedger, Observation, Claim outcome and ExecutionContext facts. It can expose recent accepted/rejected actions, the last challenge, Main-selected grounding IDs, available Material IDs, replay-only preflights, executed observations, open Frontiers, workspace epoch and remaining physical token budget.

This projection is factual only. Runtime does not label a sequence as a semantic loop, choose a recovery strategy, or force stopping. Main decides whether already-observed Material is sufficient, whether another capability is useful, or whether to return a Final with honest limitations.

## Capability failures

Capability validation/execution failures are physical results returned to Main whenever the task can safely continue. One invalid sibling in a multi-call batch does not cancel valid independent siblings.

Runtime blocks the unsafe/impossible effect; Main decides the next semantic action.

## Context and physical containment

The deployment llama-server has one hard per-call context ceiling:

```text
context_window_tokens = 38000
```

Runtime reserves room for model output and safety overhead before compiling each request. There is no separate cumulative prompt-token or completion-token budget in Core.

Task-wide containment is deliberately limited to:

```text
max_total_tokens       90000
task_deadline_seconds 1800
```

Rev1.3 has no fixed LLM-turn, LLM-call or tool-call quota. Each additional semantic turn is allowed until the physical token/deadline envelope is exhausted. The 90k task fuse is deliberately much closer than the experimental 1M Rev1.2.x value.

Canonical state may outlive one model call. Main-facing views are bounded physical projections; open Frontiers and referenced grounding remain addressable without exposing private handles.

## Writes

Real workspace mutation has one supervised physical path:

```text
Main patches
  ↓
Runtime dry-run
  ↓
WriteTransaction
  ↓
user confirmation
  ↓
apply
  ↓
validation
  ↓
commit result or rollback
```

`run_command` operates on a sandbox copy and cannot authorize real workspace mutation.


## Sandbox execution

`run_command` keeps the semantic contract unchanged while Runtime selects a strong physical backend. `backend=auto` prefers Microsandbox, then Docker, then Bubblewrap. Microsandbox is a per-job embedded microVM laboratory: Eyle creates one disposable writable workspace snapshot and keeps the VM/rootfs alive across commands in the same physical job. Linux/macOS bind-mount the disposable snapshot at `/workspace` for speed. Native Windows deliberately avoids the 0.6.8 virtio-fs bind path and stages the snapshot into VM-private `/workspace` using `Sandbox.fs.copy_from_host`. The real workspace is never a writable guest mount.

Unrestricted Microsandbox commands use the canonical `public` network profile via `Network.from_profiles("public")`. Supervised tests use Microsandbox only when explicitly configured with a test-capable OCI image; that path creates a separate one-off microVM and uses `Network.none()` when `bloquear_rede=true`. Runtime applies VM vCPU/memory settings and command timeout/resource rlimits. A runtime/virtualization failure after Microsandbox is selected is reported as that physical failure rather than silently switching execution environment mid-attempt.

## Service infrastructure

Queue, worker, persistence, history, progress and telemetry host the agent. They are infrastructure, not reasoning authorities.

## Clean break

2.7.5 Rev1.3 uses strict current Session, queue and project-memory schemas. It does not migrate removed Core contracts or preserve semantic gates from previous revisions. Git and `CHANGELOG.md` retain history.


### Microsandbox 0.6.8 API closure

The Runtime targets the pinned Python SDK contract directly: it bootstraps the local runtime with `is_installed()`/`await install()`, uses `Network.from_profiles("public")` for ordinary `run_command`, and `Network.none()` only for explicitly network-isolated supervised execution. The removed/historical `Network.public_only()` helper is not part of the active integration.
