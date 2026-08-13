# Technical overview — Eyle 2.7.5 Rev1.4.3

## Grounded Completion

Rev1.4.3 adds no planner/router or semantic reviewer. It keeps the small model-facing surface and completes optional semantic state: Investigation records what Main established from grounding, while Task result records what Main considers achieved against its criteria.

Rev1.4 removes the Claim subsystem and makes Main the only LLM in the normal decision loop.

```text
Main call
  ↓
optional tool calls
  ↓
Observation
  ↓
Main updates Investigation / Tasks
  ↓
Final preflight
  ├─ open commitments? → reject to Main
  ├─ required committed Material missing? → reject to Main
  └─ structurally valid → accept and deliver
```

There is no provisional Final or second LLM review.

## Task state

Tasks are recursive Main-owned intentional commitments. Each Task has exactly:

```text
id
parent_id
description
completion_criteria
status
result
grounding_ids
```

Runtime validates IDs, parent references, cycles, closure shape, Material references and that a completed Task has no open direct child. Any remaining open Task rejects Final as `FINAL_COMMITMENTS_OPEN`.

## Investigation state

Investigation is a Main-owned epistemic commitment. An open Investigation blocks Final. `established` requires real Material grounding plus a non-empty `conclusion` stating what Main established about its goal. `dismissed` records an explicit semantic decision to stop pursuing it.

## Final grounding continuity

`validate_final()` accepts a mechanically computed `required_grounding_ids` set. It checks that every committed Material coordinate appears in Final `grounding_ids`.

Runtime does not decide whether those materials semantically prove the answer. It prevents Main from silently dropping its own declared evidence during synthesis.

Once an Investigation is established, its raw Material is no longer pinned merely for that Investigation. The Main-authored `conclusion` is the compact semantic state; canonical Material stays in Runtime and grounding IDs remain available for Final continuity.

## Observation projection

Canonical Observation remains Runtime-owned while the prompt receives bounded projections:

- `latest_capability_results` for fresh physical results;
- `runtime_observations` for compact observed-state coordinates;
- `current_material` for selected Material coordinates;
- open Frontiers/committed coordinates as needed.

Coverage and Frontier remain physical facts. Main decides whether continuation matters.

## Memory Kernel

Persistent cognitive memory uses SQLite with revisioned Memory Nodes, tags, relations, atomic ChangeSets, append-only events and bounded navigation.

`MemoryCoverage`/`MemoryFrontier` intentionally remain distinct from Observation Coverage/Frontier. Memory content is loaded only through explicit memory tools; store size does not imply prompt size.

## LLM transport

Main uses the configured OpenAI-compatible/Ollama transport through the structured `agent` profile. Provider variability stays behind adapters. Structured parsing is fail-closed.

Rev1.4 deletes the old `claim_verifier` structured profile and Claim transport path.

## Physical containment

Runtime enforces per-call context, task-wide physical token fuse, task deadline and sandbox/resource limits. There is no Claim reserve or fixed Claim output budget.

## Future validators

Capability-owned validators are a documented future extension, not an active framework. A validator must evaluate explicit criteria that its domain can actually inspect and return results through normal Observation. A specialist LLM may be one implementation when supplied with explicit criteria, but it is not a universal judge or second Main.
