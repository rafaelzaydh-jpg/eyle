# Architecture — Eyle 2.7.5 Rev1.4.3

## 1. Core thesis

Eyle is a universal agency kernel whose domain power comes from capabilities.

```text
Request
  ↓
Main ───────── semantic authority
  ↓
Capabilities ─ physical domain operations
  ↓
Observation ─ observed reality
  ↓
Main state ─ Investigation / Tasks / Memory activation
  ↓
Grounded Completion
  ↓
Final
```

Runtime owns physical/contract authority only. It does not decide semantic relevance, truth, strategy or whether more available information is important.

## 2. Rev1.4 Grounded Completion

### 2.1 Rev1.4.3 Semantic Completion

Rev1.4.3 closes the semantic gap between evidence collection and intentional completion while preserving Main authority. The optional structures now form a complete cognitive chain when Main chooses to use them:

```text
Task.description / completion_criteria
            ↓
Investigation.goal
            ↓
Observation / Material
            ↓
Investigation.conclusion
            ↓
Task.result
            ↓
Final
```

`Investigation.conclusion` states what Main believes its selected grounding establishes about the Investigation goal. Runtime requires a non-empty conclusion plus real Material before accepting `status=established`, but does not judge whether the conclusion is correct or sufficient.

`Task.result` remains the semantic closure of `completion_criteria`: what Main considers achieved against the criteria it created. Runtime requires a non-empty result for closed Tasks and preserves the criteria/result state, but does not grade semantic adequacy.

Dismissed Investigation grounding is not completion grounding. Only open Investigation Material is pinned while epistemic work is unresolved. Once established, `conclusion` becomes the semantic working-state compression; canonical Material remains in Runtime and established grounding IDs still contribute to Final continuity.

Session and Queue use schema `2.7.5-r1.4.3` because Investigation gained persisted `conclusion`. There is no migration alias.

### 2.2 Rev1.4.2 Epistemic Clarity

Rev1.4.2 changes guidance, not semantic ownership. Main is not required to create Task/Investigation or use a capability merely because one is available. Direct Final is a normal path. Fixed model-facing text is limited to:

1. semantic/physical authority boundaries;
2. available action paths;
3. capability contracts and physical limits;
4. factual Runtime rejection/state notices.

Runtime never converts ambient workspace state, an empty workspace, a tool result or an available capability into a semantic obligation. Once Main explicitly creates a commitment, Rev1.4 Grounded Completion enforces that declared contract mechanically.


Claim is removed completely.

There is no:

- `claim_review.py`;
- Claim LLM call;
- Claim structured-response profile;
- Claim configuration;
- Claim session/history/telemetry state;
- provisional Final;
- Main↔Claim correction loop.

Reliability instead uses explicit Main-owned commitments.

### 2.3 Investigation

Investigation answers: **what did Main explicitly decide it must understand?**

- `open`: unresolved epistemic commitment; blocks Final.
- `established`: resolved with one or more real `mat-*` Material IDs and a non-empty semantic `conclusion`.
- `dismissed`: Main explicitly decides the question no longer needs resolution.

Runtime validates structure, Material existence and presence of the conclusion. Main owns what the conclusion means and whether it answers the epistemic goal.

### 2.4 Tasks

Task answers: **what work did Main explicitly commit to perform?**

Exact state:

```text
id
parent_id
description
completion_criteria[]
status: open | completed | dropped
result
grounding_ids[]
```

Rules:

- every Task declares at least one completion criterion;
- closed Tasks require a result;
- grounding IDs, when present, must reference real Material;
- a completed Task cannot retain an open direct child;
- any open Task blocks Final.

Runtime never infers Task completion from tool execution or from children. Main owns completion semantics.

### 2.5 Final continuity

Before accepting Final, Runtime mechanically gathers Material IDs committed by:

- established Investigations;
- completed Tasks that record grounding.

Those IDs must appear in Final `grounding_ids`. Missing committed coordinates reject the Final as `FINAL_REQUIRED_GROUNDING_MISSING` and return factual correction feedback to Main.

This is continuity, not truth grading. Runtime proves only that evidence Main explicitly committed did not disappear during synthesis.

### 2.6 Direct Final remains valid

If Main creates no Task and no Investigation, there is no commitment to close. Conversational or simple requests can return Final directly.

## 3. Observation

Observation is the generic physical boundary:

```text
Observation
├─ Material
├─ Coverage
└─ Frontier
```

Material carries domain-neutral identity/content coordinates. Coverage says what was physically examined. Frontier says what can still be continued. Frontier is availability, never an instruction to continue.

Canonical Observation remains Runtime state. Main receives bounded/delta projections so prompt size does not grow proportionally with all previously observed payloads.

## 4. Memory Kernel

Rev1.3.6 introduced persistent cognitive memory and Rev1.4 keeps it unchanged.

Memory is not Observation:

- Observation: what external reality showed.
- Memory: what has been learned, decided, recorded or intentionally persisted.

Memory uses three implementation owners:

- `memory.py`: small public Core surface;
- `memory_store.py`: SQLite, revisions, relations, atomic ChangeSets and append-only history;
- `memory_navigation.py`: bounded activation, `MemoryCoverage`, `MemoryFrontier`, private continuation state.

No shared generic Coverage/Frontier abstraction is extracted with Observation yet.

## 5. Capabilities

Capabilities own domain mechanics. Core should not learn that code analysis needs files, databases need schemas or networks need routes.

The public registry owns execution plus physical metadata/projection/Observation hooks. Main chooses which capability matters semantically.

## 6. Future validation direction

Some capabilities may later own validators with actual criteria. Examples:

- code: compile/tests/static checks;
- database: dry-run/integrity/rollback/permissions;
- network: reachability/config invariants;
- security: explicit policy/rubric, optionally evaluated by a specialist fresh LLM.

A validator result should return through normal Observation so Main reasons over physical findings. Core receives no universal validator semantics and no replacement Claim gate is introduced in Rev1.4.

## 7. Mutation and safety

Writes are transactional and operate on the dedicated workspace. Post-write verification may compile/test and rollback physical changes when configured.

Eyle source is protected. Self-analysis is observational; self-modification experiments occur only in an isolated writable sandbox copy and may be exported only as an inert ZIP artifact.

## 8. Physical budgets

Runtime uses physical containment:

- model context limit;
- task-wide 90k maximum physical token fuse;
- deadline;
- sandbox limits;
- structured-response fail-closed validation.

There is no standing downstream/Claim token reserve and no Claim request.

## 9. Architectural law

> **The model owns meaning. Runtime owns physical state. Capabilities own domain mechanics.**

A universal abstraction is added only after multiple real implementations prove the same invariant. Similar names or shapes alone are insufficient.
