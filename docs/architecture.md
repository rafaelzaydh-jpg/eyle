# Architecture — Eyle 2.7.5 Rev1.5.1

## Core thesis

> **Main owns meaning. Runtime owns mechanics. Providers own the world. Host chooses the body.**

```text
User
  ↓
Main
  ├─ capability_calls
  ├─ await_user
  └─ complete
        ↓
      Runtime
        ↓
 Universal Contracts
        ↓
 Host-injected Registry
   ↓       ↓       ↓
workspace memory  PetBot ...
```

Core is intentionally domain-neutral. It does not know what code, files, routers, feeders, cameras or devices mean.

## Host

A Host assembles the physical body of one Eyle product:

```text
Host
├─ CapabilityRegistry
└─ provider_context factory
```

Core requires an explicitly injected Registry. There is no global `default_registry()` and no implicit standard provider. The bundled distribution chooses the `standard` workspace provider plus the independent `memory` provider in `eyle/host.py`. Alternative products may construct a different Host without editing Core.

`runtime/service.py` consumes the Host generically. It does not import `standard`, `standard_impl` or project discovery.

## Main

Main is the sole semantic authority. It receives:

- immutable original `request`;
- authoritative `request_context` produced by user answers while the active task is suspended/refined;
- `prior_conversation` as bounded conversational background;
- provider-supplied environment description;
- current Observation/Material/effects;
- Runtime feedback;
- self-described available capability contracts;
- optional Main-owned Investigation/Task state.

Main chooses exactly one action per turn: `capability_calls`, `await_user`, or `complete`.

Capabilities are resources, not workflow stages. Runtime never routes by words such as “analyze”, “verify”, “code”, “router” or “PetBot”.

## Active request continuity

The original request is retained immutably for provenance. A response to `await_user` is not ordinary background; Runtime stores it mechanically in `request_context`:

```text
request              immutable origin
request_context      authoritative refinements/resolutions of this active task
prior_conversation   conversational background
```

Main interprets `request + request_context` together. Runtime never summarizes or semantically rewrites them.

## Runtime

Runtime owns mechanically provable responsibilities only:

- structured-response/schema validation;
- explicit Registry plumbing;
- capability argument validation and dispatch;
- confirmation persistence/resumption;
- capability-result/effect coherence;
- Observation/Material/effect identity;
- Coverage/Frontier mechanics;
- budgets, deadlines and context containment;
- canonical session persistence;
- provider-private runtime-state lifetime and cleanup;
- deterministic retry/transport mechanics.

Runtime does not decide semantic relevance or whether evidence intellectually proves a claim.

## Universal contracts

Shared physical contracts live below both Core and Providers:

```text
eyle/contracts/
├─ capability.py
└─ observation.py
```

Providers and `eyle/capabilities/` must not import `eyle.core.*`.

## Capability Registry

A provider registers local IDs:

```text
Provider("petbot", {"status": ..., "dispense_food": ...})
```

Registry publishes:

```text
petbot.status
petbot.dispense_food
```

Two providers may safely own the same local name because identity is namespaced mechanically.

Every model-visible capability contract includes provider, purpose, inputs, returns, caveats, limits, effect class and explicit confirmation requirement.

## Effect coherence

Every canonical capability result has `executed`, `changed` and optional `physical_effect`.

Registry mechanically enforces at least:

- `changed=true` requires `executed=true`;
- `physical_effect.changed` must agree with result `changed`;
- `observe` cannot report mutation/physical effect;
- `execute` cannot report `changed=true` world mutation;
- `mutate` with `changed=true` must report a valid physical effect.

This is contract enforcement, not semantic interpretation.

## Confirmation

For `confirmation=required`:

```text
Main requests capability
→ Provider prepares
→ Runtime persists pending confirmation
→ user confirms
→ Provider executes
→ Runtime records Observation/effect
→ Main receives result
→ Main decides next action
```

Provider confirmation never supplies the terminal user answer.

## Observation and effects

Observation remains the generic physical boundary:

```text
Observation
├─ Material (mat-*)
├─ Coverage
└─ Frontier (fr-*)
```

Physical effects use:

```json
{"resource":"petbot.feeder","operation":"dispense","persistence":"persistent","changed":true}
```

Executed effects receive `eff-*`. Persistent changed effects advance domain-neutral `reality_epoch`.

## Memory

Persistent cognitive Memory is an independent provider:

```text
memory.search
memory.store
```

It can be installed alongside a PetBot, network or workspace Host. Memory is context, not proof that current external reality still matches remembered information.

## Optional Investigation and Tasks

`investigation_updates` and `task_updates` are optional top-level Main deltas. They are omitted when unused. If Main voluntarily creates an open commitment, Runtime mechanically prevents terminal completion until Main closes/drops it according to its declared contract.

## Clean-break boundary

Rev1.5.1 deliberately does not restore:

- `default_registry()` / global provider mutation;
- special `patches` action;
- `tool_calls` protocol;
- `completion_mode` taxonomy;
- keyword/task routers;
- Core-owned workspace/Git/sandbox/Memory mechanics;
- provider `confirmation_message` terminal responses;
- mandatory empty Investigation/Task updates.
