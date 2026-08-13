# Eyle

**Version:** 2.7.5 · **Schema:** 2.7.5-r1.4.1 · **Revision:** rev1.4.1-semantic-freedom

Eyle is a small universal agency kernel built around one Main LLM, deterministic capabilities, explicit physical Observation, Main-owned Investigation/Tasks, persistent bounded Memory and supervised mutation.

The Core principle is simple:

> **Give Main observable tools and it can do the work; Runtime constrains physical reality, not thought.**

## Rev1.4.1: Semantic Freedom

Rev1.4.1 keeps Rev1.4 Grounded Completion but removes strategy from the fixed model-facing surface. Main may answer directly, use capabilities, keep optional Investigation/Tasks, ask for blocking input or stop. Workspace state and tool availability are context, not work requests.

The fixed surface follows three rules:

- prompts describe authority, available paths and physical boundaries;
- capability text describes effect/input/output/boundary, not when Main should use it;
- Runtime feedback reports rejection/state/expectation facts and never prescribes the next semantic move.

Direct Final remains valid with empty Investigation/Tasks and with no tool use. If Main voluntarily creates a Task or Investigation, Rev1.4 commitment rules still apply.

## Rev1.4: Grounded Completion

Rev1.4 removes Claim completely. There is no second LLM review, no provisional Final, no Claim config/schema/session state, no Claim telemetry and no Main↔Claim retry loop.

Reliability moves into the work state Main already owns:

```text
Request
  ↓
Main
  ├─ Investigation: what must be understood?
  ├─ Tasks: what work has Main committed to complete?
  └─ Capabilities → Observation / Material
                    ↓
          grounded completion state
                    ↓
                  Final
```

Runtime does **not** decide whether a conclusion is semantically true. It mechanically enforces only commitments that Main explicitly created:

- an open Investigation blocks Final;
- an `established` Investigation must reference real `mat-*` Material;
- an open Task blocks Final;
- every Task declares `completion_criteria`;
- a completed parent Task cannot retain an open direct child;
- completed Tasks may record `grounding_ids` when their result depends on observed reality;
- Final must include the Material IDs already committed by established Investigations and grounded completed Tasks.

Conversational and genuinely single-step requests do not need Tasks or Investigation. Main may answer directly with Final.

This does not turn Runtime into a semantic grader. Main still decides what to investigate, what criteria mean, whether a Task is complete and what conclusions follow from Material. Runtime checks identity, existence, status transitions, referential integrity and declared grounding continuity.

## Task contract

A Task is intentional state: work Main explicitly decided to do.

```json
{
  "id": "task-example",
  "parent_id": null,
  "description": "Audit token use",
  "completion_criteria": [
    "Identify the dominant prompt-cost components",
    "Propose reductions without removing required capability"
  ],
  "status": "open",
  "result": "",
  "grounding_ids": []
}
```

Exact fields are:

```text
id
parent_id
description
completion_criteria
status: open | completed | dropped
result
grounding_ids
```

`completion_criteria` is declared before closure. Closed Tasks require a result. `grounding_ids` are optional, but when supplied they must reference real Material. Runtime never invents completion criteria or infers that tools automatically completed a Task.

## Investigation contract

Investigation is epistemic state: a question Main explicitly decided must be resolved before delivery.

An Investigation can remain `open`, become `established`, or be `dismissed`. `established` requires at least one real Material grounding. An open Investigation blocks Final because Main itself declared that unresolved knowledge necessary.

Investigation is not a planner and does not automatically expand Frontiers. Main decides what matters.

## Observation

Observation is physical reality returned by capabilities.

```text
Observation
├─ Material   stable mat-* coordinates over observed content
├─ Coverage   what was mechanically examined
└─ Frontier   what can still be continued/materialized
```

Coverage and Frontier describe physical reach, never semantic relevance. Opaque continuation state remains Runtime-private.

Rev1.4 keeps bounded projection: Runtime retains canonical Observation while Main receives fresh results, a bounded Material directory, committed/pinned coordinates and open Frontiers instead of the entire historical payload on every turn.

## Memory Kernel

The Rev1.3.6 Memory Kernel remains intact in Rev1.4 and stays semantically separate from Observation, Tasks and Investigation.

```text
Memory
├─ revisioned Memory Nodes
├─ region string
├─ tags
├─ relations
├─ opaque provenance
├─ atomic ChangeSets
├─ append-only history
└─ bounded MemoryView
   ├─ MemoryCoverage
   └─ MemoryFrontier
```

SQLite is the only physical store. Memory bodies are never injected automatically into Main's prompt. Main chooses when to activate memory.

The names `MemoryCoverage` and `MemoryFrontier` are intentionally distinct from Observation Coverage/Frontier. Similar mechanics do not yet justify a shared abstraction.

## Future capability-owned validators

Rev1.4 does **not** replace Claim with another universal verifier.

Some domains have objective or rubric-based checks that a capability can actually evaluate: tests, compile checks, migration dry-runs, rollback checks, permissions, reachability, schema integrity or a specialist safety review with explicit criteria.

The consolidated future direction is:

```text
Capability execution
       ↓
optional capability-owned validator
       ↓
physical validation result
       ↓
normal Observation
       ↓
Main reasons over it
```

Validators must remain domain/capability-owned. Core must not learn profession-specific checklists, and a validator must not become a second planner or universal judge. No validator framework is introduced in Rev1.4.

## Ownership laws

- **Main owns meaning:** interpretation, relevance, Investigation, Tasks, grounding choice, capability choice, recovery and stopping.
- **Runtime owns physical state:** schemas, permissions, persistence, transactions, sandboxing, continuation mechanics, time/token containment and referential integrity.
- **Capabilities own domain mechanics:** how to inspect or act on their domains and how their physical results are described.
- **Observation owns observed reality.**
- **Memory owns persistent cognitive state.**
- **Tasks own intentional commitments.**
- **Investigation owns explicit epistemic commitments.**

No Core component is allowed to infer semantic relevance merely because more physical data exists.

## Workspace and self-change boundary

The automatic work plane is `workspace/`. Eyle source is a separate protected self-source.

Main may inspect Eyle source and may experiment on a writable isolated sandbox copy, but no capability promotes modified self files back into the installation. Self-experiments may leave the sandbox only as an inert ZIP artifact for human review.

## Physical containment

Eyle keeps physical containment rather than semantic quotas:

- per-call model context ceiling;
- task-wide physical token fuse (`max_total_tokens`, capped at 90,000);
- task deadline;
- sandbox CPU/memory/process/output/filesystem bounds;
- strict structured-response validation;
- transactional writes and post-write verification.

There is no Claim reserve and no Claim call. There is no fixed semantic LLM-turn/tool-call stopping quota.

## Public capability philosophy

A capability owns its physical domain contract. Adding a new observational/action capability should not require capability-name branches in Main, Investigation, Tasks, Memory or generic Observation.

That is the intended universal-kernel boundary:

> **Domain power comes from tools. Core supplies agency, state, bounded reality and physical control.**

## Release policy

Rev1.4 is a clean break. It does not carry compatibility aliases or migrations for removed Claim/session/config contracts. Incompatible prior persisted state is rejected rather than silently adapted.

Historical revision details live in `CHANGELOG.md`. Current architecture is documented in `docs/architecture.md`, `docs/technical-overview.md`, `docs/memory-kernel.md` and `docs/verification.md`.
