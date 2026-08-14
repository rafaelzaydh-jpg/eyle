# Eyle

**Version:** 2.7.5 · **Schema:** 2.7.5-r1.5.3 · **Revision:** rev1.5.3-cognitive-task-memory

Eyle is a domain-neutral agency kernel built around one Main LLM, a mechanical Runtime and Host-injected Capability Providers.

> **Main owns meaning. Runtime owns mechanics. Providers own the world. Host chooses the body.**

## Rev1.5.3: Cognitive Task Memory

Rev1.5.3 evolves the existing Rev1.5.2 architecture instead of replacing it. Observation remains the canonical physical record, while the active AgentSession gains task-scoped cognitive memory: Main may select exact EvidenceSpans from `mat-*` Material, retain compact Findings and Conclusions, and let raw source bodies leave the prompt after they have been metabolized.

Physical Coverage no longer implies cognitive availability. When Main requests a previously observed source range, the owning Provider may rematerialize that exact range from canonical Observation without re-reading the external source. File projections also expose presentation completeness when a physically read range is only partially shown to Main, closing the replay/working-memory loop that could otherwise cause repeated cached reads. Persistent `memory.*` remains a separate provider for longitudinal memory across tasks.

## Rev1.5.2: Causal Effect Literacy

Rev1.5.2 keeps the Host-injected universal-provider architecture from Rev1.5.1 and closes the causal interpretation gap exposed by real sandbox testing. Main is now taught generically that capability success is not automatically task success: it must compare the active objective with the actual physical effect resource, operation, persistence and changed state. Providers may publish `establishes` / `does_not_establish` boundaries so weak or small models can understand what a capability can causally prove without Core knowing the domain.

`await_user` is clarified as a true blocked-work suspension, while `complete` also serves ordinary conversational turns. Runtime remains mechanically strict but semantically dumb: no prose classifier, keyword router, mandatory capability workflow or Direct/Observed/Effect phase is reintroduced. Persisted Session schema remains Rev1.5.1 because its shape did not change.

## Rev1.5.1: Host-Injected Universal Capabilities

Rev1.5.1 closes the provider boundary introduced in Rev1.5.0. Core has no default capability registry and Runtime service no longer discovers or imports the bundled workspace provider. A Host injects both the Registry and opaque provider context. The bundled Host chooses `standard + memory`; another product can choose PetBot, network, IoT or any other provider set without changing Core.

```text
User → Main → capability_calls / await_user / complete
                ↓
             Runtime
                ↓
       universal contracts
                ↓
       Host-injected Registry
          ↓       ↓       ↓
      workspace  memory  PetBot ...
```

Providers register **local** capability IDs; Registry publishes canonical `provider.capability` IDs automatically. Provider results are mechanically checked against their declared `observe | execute | mutate` effect class and the universal `{resource, operation, persistence, changed}` physical-effect contract. Providers/capabilities no longer import contracts from `eyle.core.*`; shared contracts live under `eyle/contracts/`.

Confirmation is supervision, not completion. After a confirmed capability executes, its Observation/Material/effect is recorded and returned to Main, which alone decides the next action. `await_user` likewise preserves the immutable original `request` while user answers are stored separately in authoritative `request_context`; `request + request_context` define the active task. `prior_conversation` remains background rather than task authority.

Investigation and Task deltas are optional fields rather than mandatory empty ceremony. Persistent cognitive Memory is its own `memory` provider, not part of the workspace/code provider. Transient OpenAI-compatible HTTP statuses retain retryability through backend translation instead of being flattened into a non-retryable generic HTTP error.

The Main prompt stays deliberately non-prescriptive: no keyword router, no Direct/Observed/Effect phase, no “analyze → inspect” rule. Capabilities describe themselves; when Main is unsure whether it possesses enough information to answer reliably, it is generally encouraged to observe before answering.

Historical sections below describe earlier revisions and may use contracts removed by Rev1.5.1 or refined by Rev1.5.2.

## Rev1.4.8: Completion Basis

Rev1.4.8 restores an explicit bridge between Main's terminal claims and Runtime reality without bringing Claim or a semantic verifier back. `complete` now declares `completion_mode=direct|observed|effect`, `grounding_ids` and `effect_ids`. `direct` is for answers that claim no current physical observation/action; `observed` requires current `mat-*` Material; `effect` requires one or more current `eff-*` physical-effect coordinates produced by executed Observation events. Runtime validates only the coordinates and already-created commitments; Main still owns semantic truth.

The Main prompt is intentionally more explanatory. Every physically available capability is exposed with purpose, effect class, inputs, returns, caveats and limits before selection, so weaker models do not have to infer tool meaning from tiny signatures. Cost-driven token pressure is removed: `agent.max_total_tokens`, cumulative `MAX_TOTAL_TOKENS_EXCEEDED` steering and turn/token-pressure shrinking of fresh results are gone. The physical per-call context window, deadline, sandbox/resource limits and context-window-only crop remain.

Historical sections below may use **Final** when describing pre-Rev1.4.7 behavior; in the current protocol that terminal action is named **Complete**.

## Rev1.4.6: Supported Final

Rev1.4.6 strengthens the meaning of the terminal `final` action without adding a verifier or mandatory Investigation. Final now means Main considers the requested work complete — not merely planned — and considers its claims supported by its current basis. `grounding_ids` names current `mat-*` Materials that directly support Final claims; `limitations` records relevant remaining gaps. Runtime still validates only shape, references and commitments, never semantic truth.

Executable/mutating capability results can now expose one normalized `physical_effect` record:

```text
target
persistence = call | job | persistent
real_workspace_changed
real_eyle_changed
```

`run_command` explicitly reports `target=isolated_snapshot`, `persistence=job`, and both real-source change flags as false. `run_tests` reports an isolated test sandbox; `export_sandbox_zip` reports persistent artifact creation; `memory_store` reports persistent Memory Kernel mutation. Real workspace changes remain exclusive to confirmed patch transactions. The physical effect is also retained in produced Material where applicable, so a later grounded Final can distinguish snapshot effects from real-source effects.

## Rev1.4.5: Self Source Identity

The model-facing `project` projection now states the physical identity of the running system:

```text
project.identity.running_instance → Eyle Root
project.identity.self_source      → eyle
project.sources.workspace.kind    → user_workspace
project.sources.eyle.kind         → running_eyle_root
```

`source=eyle` is therefore not an opaque tool enum: it is the inspectable source tree of the Eyle Root that is running the current instance. This exposes identity, not strategy; Main remains free to decide whether inspecting itself is relevant. Direct mutation of Eyle Root remains forbidden outside isolated self-sandbox experiments.

## Rev1.4.4: Supervised Continuation

Rev1.4.4 turns blocking human input into real suspended work. Main may return `await_user` with a question, reason and up to four Main-authored response options. Custom user input and cancel remain universally available at the Runtime/UI boundary. `await_user` is not Final: Runtime persists the complete open AgentSession, waits for a user resolution, then resumes the same canonical Request with Tasks, Investigations, Observation, Material and Memory continuity intact. Explicit cancel ends the suspended work.

The old clarification path that appended question/answer blocks into `session.request` is removed. Human resolutions are retained as bounded conversation context instead, so repeated supervision does not inflate or mutate the original Request. Cognitive `await_user` has no one-hour expiry; transactional `write_confirmation` keeps its separate confirmation TTL. Pending continuation schema advances to `2`, while Session and Queue remain `2.7.5-r1.4.3` because their physical shapes did not change.

The model-facing `project` projection now exposes distinct physical sources:

```text
project.sources.workspace → user work plane, including empty/nonempty state
project.sources.eyle      → installed Eyle source, read-only or isolated-sandbox access
```

An empty workspace therefore no longer implies that Eyle has no source available to inspect. The web client renders Main-authored options, a custom-response field and an explicit cancel control for active `await_user` gates.

## Rev1.4.3: Semantic Completion

Rev1.4.3 completes the meaning of the optional work-state contracts without adding another planner, reviewer or semantic gate. Investigation now carries `conclusion`: what Main concludes its selected Material establishes about that Investigation goal. An `established` Investigation requires both real `mat-*` grounding and a non-empty conclusion.

Task keeps its existing minimal shape. `completion_criteria` defines what Main chose to accomplish; `result` records what Main considers achieved against those criteria. Runtime verifies only shape, references and closure. It does not decide whether the conclusion is intellectually sufficient or whether the Task result truly satisfies the criteria.

```text
Task: what must I accomplish?
  ↓
Investigation: what must I understand?
  ↓
Observation: what did reality expose?
  ↓
Investigation.conclusion: what does that establish about the question?
  ↓
Task.result: what was achieved against the criteria?
  ↓
Final
```

Investigation and Task remain optional. Direct conversation and simple requests can still go straight to Final. Grounding from a `dismissed` Investigation is no longer completion grounding. Only open Investigation Material stays pinned in the prompt; once established, the `conclusion` carries semantic meaning while canonical Material remains in Runtime and its IDs still bind Final continuity. Session and Queue advance to `2.7.5-r1.4.3` because the persisted Investigation shape changed.

## Rev1.4.2: Epistemic Clarity

Rev1.4.2 keeps Semantic Freedom and adds one missing distinction: Main knows the epistemic role of each context source without being told which workflow to follow. Prior conversation and Memory are prior context; capabilities are available actions; Runtime Observation/Material is current observed physical state.

Main remains free to answer directly. Tools, Task and Investigation are never mandatory merely because they exist. When current reality matters and has not been observed, inspection is available; when existing context is sufficient, Final remains a normal path. Prior context, Memory, capability metadata and inference must not be presented as newly observed fact.

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

An Investigation can remain `open`, become `established`, or be `dismissed`. `established` requires at least one real Material grounding plus a non-empty `conclusion`. An open Investigation blocks Final because Main itself declared that unresolved knowledge necessary.

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

- per-call model context ceiling and safety margin;
- task deadline;
- sandbox CPU/memory/process/output/filesystem bounds;
- strict structured-response validation;
- transactional writes and post-write verification.

Token accounting is telemetry only; it does not shrink the model surface to save cost. There is no Claim reserve/call and no fixed semantic LLM-turn/tool-call stopping quota.

## Public capability philosophy

A capability owns its physical domain contract. Adding a new observational/action capability should not require capability-name branches in Main, Investigation, Tasks, Memory or generic Observation.

That is the intended universal-kernel boundary:

> **Domain power comes from tools. Core supplies agency, state, bounded reality and physical control.**

## Release policy

Rev1.4 is a clean break. It does not carry compatibility aliases or migrations for removed Claim/session/config contracts. Incompatible prior persisted state is rejected rather than silently adapted.

Historical revision details live in `CHANGELOG.md`. Current architecture is documented in `docs/architecture.md`, `docs/technical-overview.md`, `docs/memory-kernel.md` and `docs/verification.md`.
