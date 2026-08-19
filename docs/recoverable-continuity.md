# Recoverable Continuity — Rev3.7.8

Rev3.7.8 closes the Rev3.7.7 durable-recovery contract by binding live continuations to exact physical source revisions and by making recoverable checkpoint storage singular per execution.

## Boundary

Runtime owns only physical continuation facts:

- canonical `AgentSession` serialization;
- `ExecutionContext` budget/accounting state;
- Observation Ledger, Evidence, Frontiers and `reality_epoch`;
- deterministic fixed-point blocks;
- latest pending Runtime result needed by the next cognition;
- checkpoint identity, persistence and rehydration.

Runtime does **not** decide relevance, sufficiency, wandering, investigation direction or whether the task is semantically complete. Those remain Main responsibilities.

## Recoverable continuation

`continuation_kind=recoverable_execution` is non-interactive. It is not a confirmation or semantic choice and has no human-wait TTL.

Current checkpoint reasons:

- `stalled_recoverable`: the first proven local fixed point for an action/reality pair;
- `budget_salvage`: the logical execution entered the final configured provider-budget band.

The Service persists the checkpoint before resuming. If the process is lost after persistence, a worker using the same stable `execution_id` rehydrates the checkpoint instead of starting a fresh logical execution.

## Canonical checkpoint payload

A recoverable checkpoint carries:

- current-session schema version;
- request and execution identity;
- turn and `reality_epoch`;
- Observation entries/events/material metadata;
- open/consumed Frontier state and private continuation snapshots/handles;
- Evidence;
- explicit Memory navigation view;
- Runtime feedback;
- hot `pending_results` delta;
- deterministic execution-progress state, including blocked actions;
- execution-continuity v5 provider token/budget ledger and resume count.

Process-local provider state, sockets, locks, callbacks and semaphores are rebuilt rather than serialized.

## Execution Convergence Signals

Runtime exposes a mechanical signal set:

```json
{
  "operations_since_task_state_progress": 18,
  "provider_tokens_since_task_state_progress": 14022,
  "fixed_points_blocked": 2,
  "coverage_advanced": true,
  "physical_mutations": 0
}
```

These fields are measurements, not judgments. Main may interpret them as legitimate investigation, consolidation pressure, wandering, or need for more exploration.

## Mechanical coverage

Runtime may merge exact file line intervals because those are physical coordinates. It may expose:

- materialized line ranges;
- total line count when physically known;
- remaining line count;
- open Frontier ids;
- exact Coverage records from capabilities.

`complete_file_coverage` means only that known physical file lines are covered. It does not mean the task has enough evidence.

## Recovery feedback

Recovery feedback describes available coordinates and facts. It must not prescribe a semantic next action. An open Frontier can be exposed as an available continuation coordinate; Main decides whether to continue it, recall Evidence, inspect another scope or conclude.

## Observability

Rev3.7.8 records operational telemetry for:

- recoverable checkpoint creation;
- checkpoint resume success/failure;
- observation replays avoided by exact cache coverage.

Diagnostic result details also expose mechanical coverage and execution-convergence state.


## Reality-bound live continuations

`reality_epoch` still protects physical changes known to the active Runtime session. Rev3.7.8 adds a separate resource-revision binding for continuations that later re-read mutable sources.

For Standard file sources, the canonical revision is the normalized whole-file hash already exposed as Material `source_version`.

`read_file` snapshots retain one exact `resource_revision`. `search_code` snapshots retain the revision of every file-backed pending range. Before a continuation reads live bytes, the Standard provider compares the current revision with the retained revision.

If they differ:

- no page from the new source is materialized into the old continuation;
- Runtime returns `FRONTIER_SOURCE_REVISED`;
- the Frontier becomes `stale` with `source_revision_changed`;
- historical Material and Evidence remain available;
- Main receives the physical drift fact and owns its semantic response.

A changed source is not automatically declared false, irrelevant, or unusable.

Mechanical file coverage is keyed by `(source, path, source_revision)` when a revision is known. Ranges from different revisions are never merged into one apparent continuous read.

## Stable provider identity

Persisted continuation binding no longer hashes the mutable provider execution context. A Host exposes a separate stable `provider_identity`, and Runtime hashes that opaque identity as `provider_identity_hash`.

The bundled Host identity contains stable physical source/world identity and excludes mutable status such as workspace `content_state`. Resource state changes are handled by resource-revision checks instead of being confused with a different provider environment.

Alternative Hosts that want durable recovery must provide a stable identity. Runtime does not interpret provider-specific identity fields.

## Singular recoverable checkpoint

Human confirmation and semantic-choice continuations keep independent pending ids. `recoverable_execution` uses a deterministic storage path derived from `execution_id`.

For one execution:

```text
0 or 1 current recoverable checkpoint
```

Each replacement increments `checkpoint_generation`. Publication uses the existing atomic JSON writer and an inter-process lock scoped by the deterministic path. Therefore a failed publication leaves the previous complete checkpoint intact, while a successful replacement makes the next generation the only current recovery state.

## Rev4 protocol continuity

Rev4 reuses this recovery mechanism unchanged and extends `AgentSession` with `active_task_id` and `cognitive_surface`. Both are checkpointed. `cognitive_surface` is only the protocol contract to rehydrate (`navigation|explore|build`); it never forces Main to remain in that semantic direction. Active Task binding remains an exact Main-selected Memory ID.

Execution continuity schema is `execution-continuity-v6` and Session/config schema is `2.7.5-r4.0.0-ecc`.
