# Architecture

Eyle is organized around a strict authority boundary:

> **Main owns meaning. Runtime owns physical truth and enforceable limits.**

The goal is to keep the semantic center small while allowing the physical body to grow.

## Cognitive model

```text
                              MEMORY GRAPH
                    learned/revisable knowledge
                           ▲              │
                      memory_delta        │ recall
                           │              ▼
REQUEST ────────────────► MAIN LLM ◄──────────── Observation / Material
                           │
                           ▼
                          ECC
                  ┌────────┼────────┐
               Explore    Build   Conclude
                  │        │        │
                  └────────┼────────┘
                           ▼
                         Runtime
                           │
                     Capability body
                           │
                          World
```

Memory and ECC are simultaneous. A cognition response may choose an ECC move and also update Memory.

## Authorities

### Main LLM

Main is the sole semantic authority. It decides:

- what the user means;
- what information may matter;
- what to inspect next;
- what an observation means;
- what to remember or revise;
- how knowledge relates;
- whether enough is known to conclude.

Runtime must not replace these decisions with semantic routers, hidden relevance scoring, graph-centrality heuristics, or a second planning LLM.

### Memory Graph

Memory is intrinsic learned state, not transcript history and not an ECC tool. It stores atomic learned representations and relations, while large source bodies remain external Material.

### ECC Core

ECC exposes only three universal movements:

1. **Explore** — read/observe/test/recall without lasting world mutation;
2. **Build** — request one lasting physical change through Runtime safeguards;
3. **Conclude** — return the answer when Main decides the request is sufficiently resolved.

### Runtime

Runtime owns mechanically decidable facts and restrictions:

- schemas and serialization boundaries;
- paths and resource identity;
- hashes/freshness;
- permissions and protected resources;
- Evidence/Material identities;
- Coverage and exact Frontiers;
- token/deadline accounting;
- confirmations;
- persistence;
- transactions and rollback;
- sandbox/process limits.

### Capability providers

Providers implement deterministic bodies. Core does not hardcode repository, language, robot, browser, network, or another domain-specific planner.

## Request and context boundary

`current_request` is the active user request. Raw historical transcript is not projected as a second memory system. Earlier information returns only when Main learned it into Memory and/or explicitly recalls it.

Recalled Memory is context to evaluate, not universal truth.

## Observation, Material, Coverage and Frontier

Capability results are objective runtime observations.

```text
Observation
├── Material   exact observed source/body
├── Coverage   what was physically materialized
└── Frontier   exact continuation after the current page
```

A page size is a transport/materialization choice, not a semantic ceiling. If more finite content exists, Runtime exposes a public `fr-*` Frontier and Main may continue repeatedly.

## Build lifecycle

A Build never concludes merely because a write call returned successfully.

```text
Main proposes Build
      ↓
Runtime dry-run / confirmation / transaction
      ↓
physical write
      ↓
post-write verification
      ↓
verified Observation + Material
      ↓
Main sees the real result
      ↓
learn / inspect / conclude
```

This prevents Main from learning only from intended mutations.

## Wire vs canonical cognition

Main emits a tolerant, simple JSON wire shape. Eyle deterministically normalizes safe representational aliases and then validates one strict internal canonical ECC envelope.

```text
Main wire
   ↓
syntax / safe alias recovery
   ↓
canonicalize_wire_response()
   ↓
{decision, memory_delta}
   ↓
strict semantic validation
```

Canonicalization may repair representation but must never invent missing meaning.

If semantic content is still incomplete, the error becomes runtime feedback for the same Main execution rather than a fatal transport failure.

## Adapter boundary

Eyle always talks to the local Adapter on port `8080`. The Adapter talks to the remote OpenAI-compatible provider.

The Adapter owns provider transport only. It does not implement ECC or Memory semantics. A formal handshake establishes mechanical compatibility before paid generation.

## Execution continuity

A confirmation pause belongs to the same logical execution. Persisted continuation state includes the logical execution ID, absolute deadline, generated-token fuse, provider usage/call ledger, request identity and terminal capability state.

On resume, process-local resources may be recreated, but logical budget and identity do not reset. Deadline is checked before a deferred write is applied.

## Design rule

Before adding state or logic to Core, ask:

1. Does this require interpretation? **Main should decide it.**
2. Is this an objective fact or mechanically enforceable constraint? **Runtime/provider should own it.**
3. Can Main already make the decision from normal observations and Memory? **Do not add another semantic coordinator.**
