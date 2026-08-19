# Architecture — Rev4.0.0

Eyle is a single-agent runtime built around one ownership rule:

> **Every component does only what it exists to do.**

The architecture separates semantic authority from deterministic execution so that the model decides meaning while software enforces everything that can be established mechanically.


## Rev4 cognitive-surface boundary

Rev4 adds `AgentSession.active_task_id` and `cognitive_surface`. The Task ID is selected only by Main and resolves by exact Memory ID. `cognitive_surface` is physical protocol state (`navigation|explore|build`), not a semantic phase.

```text
Navigation --Main:explorar--> Explore Surface (observe/execute only)
Navigation --Main:construir--> Build Surface   (mutate only)
Navigation --Main:concluir--> response
Explore/Build --return--> Navigation
```

Runtime chooses no movement: it only materializes the surface corresponding to Main's explicit prior ECC decision. See `docs/task-anchored-cognitive-surfaces.md`.

## Component ownership

| Component | Responsibility | Must not become |
|---|---|---|
| **Main** | meaning, relevance, investigation, sufficiency, learning intent | hidden deterministic planner |
| **Core** | Eyle-specific cognition/session contracts | provider transport or domain-specific tool implementation |
| **Runtime** | physical truth, execution invariants, persistence, accounting, transactions | semantic ranker |
| **Memory Graph** | durable/revisable graph state | automatic prompt router |
| **Capability provider** | concrete observation/mutation mechanics | second reasoning agent |
| **Adapter** | provider connection and mechanical wire conformance | Eyle semantic parser/planner |
| **Service / UI** | conversation, jobs, worker lifecycle, interactions | source of execution truth |

Historical compatibility is not a permanent branch in the active runtime. When persisted data needs a safe transition, migration happens through an explicit tool.

## End-to-end request lifecycle

```text
User / API / Web UI
        │
        ▼
     Service
        │
        ├─ record conversation fact
        └─ create/resume job
        │
        ▼
ContextMaterializer
        │
        ├─ Runtime/environment facts
        ├─ recent native-role conversation
        ├─ current task mechanics
        ├─ latest observations/effects
        ├─ explicit Memory activation
        ├─ feedback/frontiers
        └─ current_request exactly once, last
        │
        ▼
      Main cognition
        │
        ▼
LLM client ─► local Adapter ─► configured DeepSeek model
        │
        ▼
ECC decision + memory_delta
        │                 │
        │                 └────────► Memory Graph v12
        ▼
     Runtime
        │
        ▼
Capability Registry
        │
        ├─ observation
        ├─ isolated execution
        └─ confirmed persistent mutation
        │
        ▼
Observation / Material / Evidence / effects
        │
        └──────────────► next cognition or Conclude
```

The model is not trusted to assert that a physical action happened. Only Runtime observations establish that fact.

## Conversation chronology

Service owns message recording and conversation identity.

`ContextMaterializer` decides only how much of recent conversation physically fits the configured token budget. It does not rank messages by semantic relevance.

Provider transport preserves conversation as native `user` and `assistant` roles. Runtime state is materialized first, recent conversation follows, and the active `current_request` is emitted **exactly once** as the final provider `user` message.

This ordering preserves causal conversation structure without adding a topic router.

Diagnostics expose:

- `conversation_messages_materialized`;
- `conversation_messages_omitted`;
- `older_history_available`.

Omitted history is not equivalent to deleted or unreachable history.

## Self identity

Eyle is the running agent.

Ordinary references such as "you", "Eyle", "your code", "your runtime", "your Memory", or "this agent" refer to the running Eyle instance unless the conversation explicitly establishes another referent.

Two physical source surfaces are distinct:

- `source="eyle"` — the source tree of the Eyle instance currently running;
- `source="workspace"` — the user's project.

Runtime does not use keyword routing to choose between them; Main owns that meaning.

Self/internal analysis is grounded in observable source, logs, Runtime state, persisted facts, and capability results. Hidden chain-of-thought is not an introspection surface.

## ECC

ECC has exactly three movements:

1. **Explore** — obtain information without persistent world mutation.
2. **Build** — request a persistent physical change through Runtime safeguards.
3. **Conclude** — return the user-facing answer.

The current response wire is flat and current-only. Eyle supplies its JSON Schema to the Adapter; the Adapter communicates and validates that representation but does not decide ECC semantics.

Memory is an independent sidecar:

```text
valid ECC + invalid memory_delta
        =
execute ECC + record Memory rejection
```

A Memory parse/storage problem cannot invalidate an already valid Explore, Build, or Conclude decision.

## Adapter boundary

The bundled Adapter is outside Eyle cognition.

It owns only:

- provider connection/authentication;
- fixed DeepSeek transport translation;
- delivery of the caller-supplied JSON Schema;
- safe mechanical JSON extraction/recovery;
- validation against that same schema;
- one isolated format-only repair;
- transport/usage telemetry.

The repair receives only:

```text
schema
+ previous candidate
+ validation errors
```

It does not replay Eyle conversation, Memory, Task, tools, or Runtime state.

`finish_reason=length` is truncation, not a format defect, and therefore does not trigger format repair.

If the single repair still leaves an invalid current-wire candidate, Eyle keeps its Session, Task state, observations, and prior physical progress. Core may ask Main for one fresh current decision. Mere syntactic validity does not count as execution progress.

The Adapter does not own:

- ECC meaning;
- Memory meaning;
- Task state;
- tool selection;
- planning;
- semantic relevance;
- Eyle's provider-token ledger;
- capability negotiation.

## Runtime execution progress

Runtime tracks only valid Eyle execution progress.

Progress is mechanical evidence such as:

- a novel Runtime result;
- a physical effect/progress fact;
- a Task-state transition.

Already-observed/replayed results are not new progress.

A repeated deterministic action/result fixed point is a **local recoverable navigation state**, not a terminal task failure. The first proven no-progress state blocks that exact action signature for the current reality epoch. If Main repeats it, Runtime returns `ECC_FIXED_POINT_BLOCKED` without physically executing/replaying the capability again.

The Session, Observation Ledger, Evidence and open Frontiers remain alive. Main decides whether to continue an available Frontier, recall existing Evidence, refine physical scope, choose another operation, or conclude. Genuine observable/physical/Task progress clears the local block.

There is deliberately no `MAX_TURNS` semantic ceiling. A long investigation may continue while it keeps producing new observable information and remains inside physical provider/context budgets.

## Memory Graph v12

Memory Graph v12 is the only active runtime graph schema.

Its identity dimensions are separate:

```text
scope        physical reachability
domain       chat | task | eyle | knowledge
context_key  optional physical context identity
```

Main authors semantic Memory. Runtime may persist mechanically knowable chat facts such as message identity, role, conversation ID, and ordering.

Memory is not automatically projected into every prompt. Explicit activation is the authority for Memory body materialization.

Activated bodies appear once through `memory_view`; operation observations carry compact activation IDs/counts/frontier metadata rather than duplicating bodies.

See [`memory-kernel.md`](memory-kernel.md).

## Context materialization

`ContextMaterializer` is the only normal prompt-materialization path.

It uses physical budgets and identities, not semantic scores. It can materialize:

- runtime environment;
- current task mechanics;
- recent conversation;
- explicit Memory view;
- latest observations;
- runtime effects;
- feedback;
- exploration/frontier metadata.

There is no:

- semantic relevance ranker;
- embedding-selected prompt;
- Active Projection;
- `memory_focus`;
- HOT/WARM/COLD Memory tier;
- hidden working set;
- second context-planning LLM.

## Capability providers

Capability providers expose deterministic mechanics through the Registry.

A capability declares an effect class:

- `observe`;
- `execute`;
- `mutate`.

ECC maps observation/execution mechanics to Explore and persistent mutation mechanics to Build.

Provider availability is not evidence that a capability executed. Only a Runtime result creates an observation.

The bundled Standard provider lives at:

```text
eyle.providers.standard
```

There is no active `standard_impl` compatibility package or dynamic facade.

## Material, Evidence, Coverage, and Frontier

Large or paged physical results must remain reachable without forcing their full bodies into every cognition.

- **Observation** — compact result of a physical operation.
- **Material (`mat-*`)** — exact physical source/body retained for inspection.
- **Evidence** — provenance-bearing support for an observed fact.
- **Coverage** — what portion of a finite source was actually materialized.
- **Frontier (`fr-*`)** — exact continuation handle for the remainder.

The invariant is:

```text
not materialized now != inaccessible
```

Paging is a presentation mechanism, not a semantic knowledge limit.

Continuations that later re-read mutable sources are revision-bound by the provider. `reality_epoch` tracks physical changes known to the live execution; an exact resource revision additionally detects external drift across checkpoint/restart. Runtime reports the drift and marks the affected Frontier stale without reinterpreting historical Evidence.

## Provider and execution accounting

One user-message execution carries one provider-token ledger across normal cognition, continuation, and a fresh wire retry.

Provider-reported usage is the accounting authority.

Current defaults:

```text
50000 context tokens / physical LLM call
150000 provider-accounted tokens / user-message execution
```

These are physical safety/accounting limits. They are not a fixed number of cognition turns.

## Build safety

Persistent changes follow a different path from observations:

```text
Main proposes mutation
        │
        ▼
dry-run / exact proposal
        │
        ▼
physical confirmation when required
        │
        ▼
transaction / rollback boundary
        │
        ▼
post-write observation
        │
        ▼
Main sees the actual result
```

The model cannot waive confirmation, protected-resource policy, sandbox restrictions, hash/freshness checks, or rollback requirements through natural language.

## Persistence and migration policy

Active readers are current-schema only.

Current identities include:

- configuration: `2.7.5-r4.0.0-ecc`;
- Session: `2.7.5-r4.0.0-ecc`;
- execution continuity: `execution-continuity-v6`;
- Memory Graph: v12.

The retained historical Memory conversion is an explicit one-shot v11 → v12 devtool. It is not imported into the normal Runtime path.

## Architecture design test

Before adding another layer, ask:

1. **Does it interpret meaning?** It belongs to Main.
2. **Does it enforce or measure physical state?** It belongs to Runtime or a capability provider.
3. **Does it only adapt provider transport/representation?** It belongs to the Adapter.
4. **Does a canonical component already own this responsibility?** Extend that component instead of creating a parallel path.
5. **Is the code only needed for historical state?** Prefer an explicit migration tool.
6. **Does an optimization make reachable information unreachable?** Reject it.
7. **Does a helper duplicate the same state/body/contract in more than one prompt location?** Remove the duplicate authority.

## Provider wire vs. Eyle execution

These are intentionally separate failure domains:

```text
provider candidate
  -> Adapter mechanical JSON recovery
  -> validate caller-supplied schema
  -> at most one isolated format repair
  -> candidate + telemetry returned to Eyle
```

versus:

```text
valid Eyle action
  -> Runtime executes
  -> same result again -> NO_PROGRESS + block exact action
  -> same blocked action again -> ECC_FIXED_POINT_BLOCKED (no physical re-execution)
  -> continue/recall/refine/other/conclude -> task remains recoverable
```

Adapter does not repair Eyle execution. Runtime does not repair provider syntax.


See `docs/recoverable-continuity.md` for the Rev3.7.8 durable recovery and convergence-signal contract.
