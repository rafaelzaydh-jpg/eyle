# Eyle Rev4.12.1 architecture

## Active path

```text
web / CLI
→ runtime service
→ AgentSession
→ LLM decision
↔ phase-specific deterministic tools / live workspace / external memory on demand
→ response-quality validation
→ answer or supervised write

completed job
→ persisted runtime result
→ on-demand observable history
→ expandable web panel
```

There is one reasoning loop. No semantic router, Mission Interpreter, Scout, Finalizer agent, recovery agent or automatic project-memory injection sits in front of the model.

## Responsibility split

### LLM

The model owns:

- natural-language understanding;
- deciding which observation is relevant to the current task;
- optional planning;
- choosing tools;
- code generation;
- explaining conclusions.

### Tools

Tools own deterministic observations or actions:

- source reads/search/symbol lookup/tree inventory;
- arithmetic with calculator evidence;
- repository measurements and token estimates;
- objective architecture signals;
- test execution, including focused pytest investigation;
- read-only Git working-tree and diff inspection;
- dry-run and write operations;
- external memory search/store.

`inspect_project` reports signals, never an `important=true` judgment. Relevance remains task-dependent and belongs to the LLM.

### Runtime

The runtime owns executable boundaries:

- safe paths and scan/read limits;
- phase-specific tool availability;
- task deadline and call/token budgets;
- dry-run and explicit confirmation;
- transactional apply and rollback;
- post-write `compileall`;
- test detection/execution;
- exact reread and promised create/delete validation;
- factual evidence ledger and explicit finding limits;
- persistent queue, cancellation and telemetry.

## Phase machine

Normal writes do not rely on the global turn limit as the anti-loop mechanism:

- `write_investigate` — discover/read required sources;
- `write_prepare` — fill genuinely missing source and prefer the transaction;
- `write_patch_only` — reads disappear; only the patch proposal may complete the task;
- `write_patch_retry` — one bounded correction after rejected dry-run;
- `analysis_answer_only` — tools close and the model answers from current evidence.

Equivalent fresh reads are rejected through semantic coverage. Consecutive no-progress turns force completion or fail with a specific runtime code.

## Context model

The session retains only what helps the current task:

- original request;
- compact stable task anchor;
- optional plan;
- latest tool result;
- bounded relevant source snippets;
- compact evidence index;
- phase/progress counters;
- pending supervised write state when required.

Full conversation history is not replayed on every turn. External project memory is not injected automatically.

## Factual response ledger

Project facts, confirmed bugs and contextual risks must map to real evidence. Preferred claims reference visible non-heading sentences by 1-based index, avoiding a second copy of the answer. The ledger is internal execution metadata; the user receives natural prose.

## Post-write verification

```text
apply
→ compile changed Python files
→ detect/run tests
→ rollback on failure
→ reread through workspace tool
→ exact full-output verification
→ confirm create/delete promises
→ verified or partial-verification conclusion
```

A failed confirmed write preserves bounded real diagnostic output and rollback state for follow-up questions.

## Rev4.12.1 observable history

The job result already contains deterministic execution metadata. Rev4.12 turns a sanitized subset into an on-demand API/UI view.

Visible:

- job status/timing;
- turns and runtime phase;
- per-call LLM usage metadata and latency;
- aggregate prompt/cached/new/effective/output token counts;
- called tools with bounded safe arguments and summarized results;
- compile/test/reread/rollback stages;
- failure codes;
- accepted/rejected decision type per turn and validation reason.

Never visible through this surface:

- chain-of-thought;
- raw system/user prompts sent to the model;
- raw model decisions/responses;
- source contents or evidence snippets;
- evidence hashes;
- external-memory bodies.

The web client requests `/jobs/<id>/history` only when the user expands **histórico**, so normal polling stays small. Decision records contain only protocol outcomes (`tool`, `final`, rejection code, etc.), never hidden reasoning or model prose.

## Design-history rule

Removed architectures and the reason they were removed are recorded in [`UPDATE_HISTORY.md`](../UPDATE_HISTORY.md). A removed mechanism should not be reintroduced without a concrete current failure, a test/metric proving it, and an explanation of what changed enough to prevent the old failure mode.
