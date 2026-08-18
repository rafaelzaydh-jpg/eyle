# Architecture — Rev3.7.2

Rev3.7.2 keeps one authority rule and one implementation rule:

> **Main owns meaning. Runtime owns physical truth. One responsibility has one canonical implementation path.**

Historical compatibility is handled, when needed, by an explicit migration tool. It is not a permanent branch in the active runtime.

## Cognitive path

```text
request
  │
  ▼
Service ── conversation facts ──► Deterministic ContextMaterializer
                                      │
                                      ▼
                                     Main
                              semantic authority
                                      │
                         ┌────────────┴────────────┐
                         ▼                         ▼
                        ECC                   memory_delta
                 Explore/Build/Conclude            │
                         │                          ▼
                         ▼                    Memory Graph v12
                      Runtime                  independent sidecar
                         │
                         ▼
                  provider/world effects
```

Main decides meaning, relevance, investigation, learning and sufficiency. Runtime may use IDs, scope, timestamps, budgets, source identity, revisions, hashes and persisted cursors because those are mechanically decidable facts.

The runtime must not add a semantic ranker, Active Projection, `memory_focus`, HOT/WARM/COLD tier, hidden working set, embedding-selected prompt, semantic context LLM or intermediate planner.

## Canonical component ownership

### Conversation

Service owns physical message recording and atomic conversation snapshots. `ContextMaterializer` decides only how much of the current conversation physically fits the configured token budget.

Recent continuity is automatic because current conversation membership/order is physical. Older content is omitted from the packet without becoming unreachable.

### Memory

Memory Graph v12 is the only runtime graph schema. Its dimensions are separate:

- `scope`: physical reachability;
- `domain`: `chat`, `task`, `eyle`, `knowledge`;
- `context_key`: optional physical context identity.

Main authors semantic Memory. Runtime may ingest Chat-domain message facts because role/message/conversation/order are physical.

Memory is an ECC sidecar: parsing or storage rejection of `memory_delta` never invalidates an already valid ECC decision.

### Context

`ContextMaterializer` is the only normal prompt-materialization path. It receives physical identifiers/budgets and emits a bounded packet containing the current request, recent conversation, task mechanics, incremental observations, explicit Memory activation, feedback and frontiers.

It has no relevance score or semantic topic classifier.

### Standard provider

`eyle.providers.standard` is the single bundled provider package. Registry, tools, contracts, workspace transactions and sandbox promotion live in that package. Core/Host consumes its canonical Registry directly.

There is no `eyle.providers.standard_impl` or dynamic re-export facade.

### Adapter

The local Adapter is transport-only. Eyle sends the canonical local request; the Adapter translates it to the configured upstream provider. Public local output-cap input is `max_completion_tokens`. Provider-specific `max_tokens` exists only in the upstream body built inside the Adapter.

## ECC

ECC has exactly three movements:

1. Explore;
2. Build;
3. Conclude.

Wire JSON is mechanically canonicalized before strict local validation. Representation recovery may normalize safe aliases, but it may not invent semantic meaning.

Structured protocol failure is repaired within the same execution. Repetition of the same protocol fingerprint is bounded; repair packets do not need to rematerialize expensive observations merely to serialize an already-attempted decision.

## Evidence, Material, Coverage and Frontier

Capability results create physical observations. Large results preserve exact source bodies as Material/Evidence and expose Coverage plus an exact Frontier for omitted finite content.

Paging reduces materialization, not reachability.

This invariant also applies to conversation and Memory:

```text
not materialized now != inaccessible
```

## Execution accounting

One user-message execution carries one provider-token ledger across normal cognition, continuation and protocol repair. Provider usage is the accounting authority.

The current defaults are:

```text
50000 context tokens / physical call
150000 provider tokens / logical execution
```

There is no generated-token fuse or cognitive wall-clock deadline.

## Persistence and migrations

Active runtime readers are current-schema only:

- configuration: Rev3.7.2 identity;
- Session: Rev3.7.2 schema;
- pending interaction: v13;
- execution continuity: v3;
- Memory Graph: v12.

The only retained historical conversion needed for this release is the explicit one-shot Memory Graph v11→v12 devtool. It runs outside the normal request/runtime path.

## Build safety

Build remains Runtime-guarded:

```text
Main proposes mutation
        ↓
dry-run / exact proposal
        ↓
physical confirmation when required
        ↓
transaction / rollback
        ↓
post-write observation
        ↓
Main sees actual result
```

Semantic choice and physical confirmation share a UI interaction shape but not authority. Only Runtime confirmation authorizes an exact physical mutation.

## Design test

Before adding another layer:

1. If it interprets meaning, it belongs to Main.
2. If it enforces/measures physical state, it belongs to Runtime/provider.
3. If an existing canonical component already owns the responsibility, extend that component rather than create an alternate path.
4. If a compatibility branch is needed only to read historical state, prefer a one-shot migration.
5. If an optimization makes reachable knowledge unreachable, reject it.
