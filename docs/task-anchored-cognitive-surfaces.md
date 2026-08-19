# Task-Anchored Cognitive Surfaces — Rev4.0.0

Rev4 changes **prompt materialization**, not Eyle's semantic authority.

The problem addressed is that Rev3 materialized more cognitive surface than a call needed: the combined ECC/tool surface, conversation, Runtime state and other context were repeatedly available even after Main had already selected the nature of the next cognition.

Rev4 preserves these invariants:

1. **Main owns meaning.** Main decides relevance, sufficiency, Task meaning, the active Task, and whether to explore, build or conclude.
2. **Runtime never chooses an ECC movement from meaning.** It may materialize a protocol surface only because Main explicitly selected that movement.
3. **Persisted state may be complete; cognitive surfaces must be purpose-bounded.**
4. **ECC selects the surface; the surface never selects ECC.**

## Existing Memory/Task architecture is reused

Memory Graph v12 remains one graph with canonical domains:

```text
chat
task
eyle
knowledge
```

No Task database, TaskFrame, planner domain or scratchpad was added.

A Task remains an ordinary Memory node with `kind=task`, `domain=task`, revision history and the existing lifecycle:

```text
active | blocked | resolved | cancelled
```

Rev4 adds only an explicit Session binding:

```text
AgentSession.active_task_id
```

Main alone creates/revises/binds/replaces/unbinds this ID. Runtime validates the exact node mechanically and persists the binding. There is no task search, ranking, “latest task” fallback or semantic matching.

When an active Task is bound, Runtime may project only that exact node as `active_task`:

```json
{
  "id": "mem-...",
  "available": true,
  "revision": 3,
  "state": "active",
  "state_revision": 1,
  "content": "..."
}
```

No neighbors/relations are expanded automatically. This exact-ID projection is execution state, not hidden Memory retrieval.

Task content remains Main-authored free text. Rev4 deliberately does not introduce an objective/subtask/next-step schema and does not use Task as chain-of-thought or a planner scratchpad.

## Protocol surfaces

ECC remains exactly:

```text
explorar
construir
concluir
```

`navigation`, `explore`, and `build` are protocol surfaces, not new semantic phases.

### Navigation

Navigation is the only surface that selects an ECC movement. It receives the current request, bounded conversation, exact active Task when present, compact Runtime state and a compact operation directory. It does **not** receive detailed capability schemas.

A trivial request can still complete in one Main cognition:

```text
Navigation -> concluir
```

No Task is required.

### Explore

`Navigation -> explorar` selects the Explore surface. Explore receives only observe/execute capabilities. Main may batch independent operations. Runtime executes only the requested batch and returns physical facts.

Explore can either request another Explore batch or return control:

```text
Explore -> return_to_ecc -> Navigation
```

It cannot mutate or conclude directly.

### Build

`Navigation -> construir` selects the Build surface. Build receives only mutate capabilities and may request one Runtime-controlled mutation attempt. After the physical result, control returns to Navigation.

Build cannot explore or conclude directly.

This does not constrain Main semantically: Main can move from Explore to Build or Conclude by returning to Navigation and making that ECC choice.

## Sidecars

`memory_delta` remains independent semantic persistence. Rev4 adds optional `task_binding`:

```json
{"action":"bind","ref":"mem-..."}
```

or a same-call `@key` created by `memory_delta`, or:

```json
{"action":"unbind"}
```

A malformed Memory/Task sidecar does not by itself invalidate an otherwise valid primary cognition. Runtime never invents a binding.

## Recoverable continuity

Rev3.7.8 remains the physical continuity base. Rev4 checkpoints additionally persist:

```text
active_task_id
cognitive_surface
```

`cognitive_surface` says which current protocol must be rehydrated; it is not semantic intent. A resumed Explore surface may immediately return to Navigation.

Resource-revision binding, stable provider identity, singular recoverable checkpoint storage, `reality_epoch`, Evidence, Observation Ledger, Frontiers, budget and execution-progress state remain unchanged.

## Context minimization policy

Rev4.0.0 minimizes **capability surface** first. It deliberately keeps `current_request` on specialized surfaces and does not aggressively remove conversation/runtime fields without evidence.

Further context removal is benchmark-gated. Runtime must never infer relevance to shrink the prompt.

## Explicit non-goals

Rev4 does not add:

- a TaskFrame parallel to Memory Task;
- a new Task DB or Memory domain;
- a fourth ECC movement;
- semantic phase routing;
- automatic objective/subtask decomposition;
- automatic Memory relevance/ranking;
- automatic Task selection;
- wandering detection;
- Evidence usefulness scoring;
- automatic Frontier consumption;
- an autonomous tool loop.

## Mechanical telemetry

Execution usage exposes protocol counts and binding transitions:

```text
navigation_calls
explore_calls
build_calls
task_bind_count
surface_transitions
```

Existing token-component accounting remains available for before/after measurement.

## Acceptance invariants

The current release must prove:

- trivial `Navigation -> concluir` is one cognition and creates no Task;
- Runtime never auto-selects `active_task_id`;
- active Task projection is exact-ID only;
- Task binding survives Session/checkpoint serialization;
- Explore schemas/catalogs expose no mutate capability;
- Build schemas/catalogs expose no observe/execute catalog;
- one surface cannot switch capability family by itself;
- Build returns to Navigation after a mutation attempt;
- Memory Graph domains remain `chat|task|eyle|knowledge`;
- Rev3.7.8 recoverable-continuity tests remain green;
- no production `ecc` monolithic structured profile remains.

The governing rule is:

```text
Main owns meaning.
Runtime owns physical truth.
Persisted state may be complete.
Cognitive surfaces must be minimal.
ECC chooses the surface.
The surface never chooses ECC.
```
