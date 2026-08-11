# Eyle Rev5.8 architecture

This document describes the **current runtime architecture**. Future design goals are kept separately in [architectural-direction.md](architectural-direction.md) so planned abstractions are not confused with shipped behavior.

## Authority boundary

```text
Main LLM = semantic authority
Runtime  = physical/structural authority
Claim    = semantic challenge
```

The Main LLM decides request meaning, tool choice, semantic debt, Evidence sufficiency, stopping, writes and Final content. Runtime never classifies intent or creates semantic debt. Claim Review challenges provisional delivery but cannot call tools, mutate state or become a second planner.

## Epistemic cost policy

Eyle treats token efficiency as subordinate to epistemic integrity. Runtime may compact, group and page objective state, but it must not improve a token metric by deciding that unseen facts are semantically irrelevant.

The invariant is:

```text
truthful reality > grounding > material satisfaction > presentation > raw token minimization
```

For objective capability results:

- `coverage_complete` describes whether the declared physical/objective scope was examined;
- `projection_complete` describes whether every objective result is currently materialized inline;
- `projection_complete=false` must remain visible and, when continuation is possible, expose a Handle;
- Main alone decides whether the current projection is semantically sufficient or whether a Handle should be expanded.

A few additional prompt tokens are acceptable when they preserve provenance, Coverage boundaries or an explicit continuation path. Runtime optimization should preferentially remove **duplication, repeated model calls and redundant hot-state retransmission**, not objective facts or uncertainty.

## Canonical state ownership

```text
physical tool reality       → ObservationLedger
objective citable material  → SourceRecordLedger
semantically admitted proof → EvidenceLedger
semantic debt               → Investigation
runtime decisions/rejects   → DecisionLedger
LLM calls/provider attempts → LLMCallLedger (ExecutionContext)
workspace mutation          → WriteTransaction
semantic audit              → ClaimReview
```

A history, counter, summary, prompt view or diagnostic is a **projection** of its canonical owner, not a second persisted source of truth.

`workspace_epoch` is an Eyle-owned mutation/replay coordinate. It is not a global filesystem fingerprint and does not own a persistent project graph cache. External filesystem freshness still requires live observation/hash checks.

### ObservationLedger

Owns physical tool events, reusable observation identity, coverage metadata, replay identity, continuation snapshots and the pending model-facing observation batch. `observation_map`, public tool history, physical tool count and replay count are derived views.

### SourceRecordLedger

Owns objectively materialized `src-*` records produced by capabilities. SourceRecords preserve physical/citable facts without asserting semantic relevance.

### EvidenceLedger

Owns only Evidence explicitly admitted by Main from SourceRecords plus narrow Runtime validation facts. A tool result is not automatically Evidence.

Owns Evidence identity, admission metadata, persistence, rehydration, freshness lookup and compact navigation. One Observation may materialize multiple SourceRecords, while only SourceRecords explicitly selected by Main are promoted into semantic Evidence.

### DecisionLedger

Owns runtime decision events plus deterministic rejection fingerprints. Public decision history and repeated-rejection counts are derived. Repeated rejection on unchanged objective state can be recognized without creating a parallel loop-state owner.

### LLMCallLedger / ExecutionContext

A logical LLM call owns its prompt metadata and accumulates physical provider attempts in the same record. `ExecutionContext` owns run-scoped deadline, physical token/call budgets, terminal capabilities and LLMCallLedger. Configuration remains immutable configuration.

### WriteTransaction

One transaction owns patches, attempts, dry-run, confirmation identity, apply, compile/tests/full verification, failure and rollback. Pending confirmation carries the transaction identity plus serialized Session; patch payloads are not duplicated in a second pending state.

### ClaimReview

Stores the semantic review itself. Follow-up debt is derived from that review when needed rather than persisted as a parallel Findings/Gaps state machine.

## Investigation

Investigation is optional persistent semantic debt created only by the Main LLM.

```text
[]        → no persistent semantic debt declared
[T1,...]  → debt declared by Main LLM
```

Once declared, Runtime enforces structural invariants only: durable identity/goal, valid Evidence IDs, Evidence for `established`, reason for closure, and no accepted Final while a target remains open.

Current statuses:

```text
open
established
dismissed
```

## Final and grounded Claim outcomes

Final has one canonical shape:

```json
{
  "answer": "...",
  "limitations": [],
  "evidence_ids": ["ev-..."]
}
```

The Main LLM selects the Evidence that supports its delivered answer. Runtime validates identity/freshness; it does not select relevant Evidence on the model's behalf.

Claim receives one canonical task plus bounded grounding coordinates:

```text
request
request:<literal_anchor_id>
answer:<anchor_id>
evidence:<EvidenceID>
runtime:<runtime_fact_id>
investigation:<target_id>
```

This avoids the false equivalence `grounding == EvidenceLedger`. Runtime mechanically validates coordinate existence. Claim decides what those coordinates establish. `material_satisfaction.status` is `satisfied`, `gap`, or `blocked`. Semantic gaps carry `required_property`, describing what remains unresolved without prescribing a tool.

Non-retryable physical tool failures are retained in `ExecutionContext.terminal_capabilities`; callable projections exclude those capabilities for the rest of the job.

## Progressive capability view

`TOOLS` is the sole executable registry. A model call does not receive every expanded tool contract.

```text
callable tool not in hot set → capability_index: compact signature + purpose
most recent requested tools  → active_tools: full contract
```

Only the **two most recently requested distinct tools** remain expanded in the hot model view. Older tools return to `capability_index` and remain callable. Expanded membership is derived from DecisionLedger request events; there is no Tool Selector call and no persisted activation state.

## Context projection

Canonical world/task state may be larger than the current model view.

The Main prompt uses a bounded deterministic projection containing:

- the active request;
- current Investigation state;
- recent SourceRecord navigation plus Investigation-pinned/recent Evidence navigation;
- pinned plus recent Observation navigation;
- bounded current-delta tool results;
- the two hot expanded tool contracts;
- compact capability navigation for the rest.

The ledgers themselves remain complete. Projection limits model materialization; it does not delete canonical state and does not introduce semantic ranking by Runtime.

Claim receives only Evidence explicitly selected by Final plus Evidence already attached by Main to Investigation. This prevents exploratory Evidence from automatically becoming verifier payload.

## Canonical capability / observation boundary

Every executable tool uses one physical result envelope with mandatory execution state plus optional observation state:

```text
Capability.execute(args)
→ status / ok / executed / changed / error_code / retryable
→ observations[]
→ coverage
→ frontiers[]
→ handles[]
→ detail
```

Registry entries expose `effect=observe|execute|mutate` as a small domain-neutral physical class. Specific `effects` metadata may still carry concrete safety detail.

### Coverage

In Rev5.8, `coverage` is capability-defined objective metadata describing what the returned observation established or examined. The Core transports and records it; domain-specific meaning remains with the originating capability.

For current Python reachability this includes fields such as `objective_complete`, `objective_result`, roots tested, scan completeness and shortest path length. These are **current tool fields**, not a claim that all future Coverage must use code/reachability semantics.

### Frontier

A `frontier` is an objective continuation boundary not materialized by the current observation. It may be a hard physical/static boundary or a deliberate soft materialization boundary. A frontier is not semantic debt and does not instruct Main to continue.

### Handle

A `handle` addresses an observation continuation without injecting its complete payload into the prompt. Rev5.8 snapshot handles are opaque, bounded, persisted with ObservationLedger and invalidated when the workspace epoch no longer matches. `expand_observation` materializes the addressed snapshot page without code-domain semantic interpretation.

```text
WORLD STATE != MODEL CONTEXT
```

Handles are a concrete selective-materialization mechanism for this boundary.

## SourceRecords, Objective Projection and Evidence Admission

Rev5.8 separates objective materialization from semantic proof. `SourceRecordLedger` owns stable `src-*` records produced by capabilities. A SourceRecord says **what was physically materialized and where it came from**; it does not say that the material is relevant or sufficient for the user's objective.

Main may explicitly place a `src-*` ID in an Investigation update or Final grounding set. Runtime then performs a deterministic identity-preserving promotion into Evidence (`src-0001 → ev-src-0001`). Runtime never chooses which SourceRecords to promote. Narrow Runtime validation facts remain the only non-Main Evidence admission path.

This keeps the authority boundary exact:

```text
Main formulates objective property
→ capability exhausts/queries physical state
→ deterministic grouping / projection / handles
→ SourceRecords + Coverage + Frontiers
→ Main decides relevance/materiality
→ explicit Evidence admission
→ Claim audits grounding and material satisfaction
```

A capability may compute over all files, AST nodes or graph edges needed for its objective query. It may sort, group, deduplicate, count, paginate and expose handles using objective rules. It must not select results because they are "more relevant" to the user's semantic intent. If the same objective capability call is issued for two different user intentions, its physical result must not change because of those intentions.

For `search_code`, Rev5.8 distinguishes physical coverage from model projection. The literal search exhausts the readable scope, groups the complete match universe, then applies deterministic cross-file diversity before bounding inline ranges. `coverage_complete` describes the searched scope; `projection_complete=false` means additional objective ranges are available behind a continuation handle. It is not a semantic incompleteness signal by itself.

## Property-directed Evidence

Eyle is a coding agent, not a dead-code auditor or a semantic liveness oracle. The Main LLM identifies the **material property** requested and gathers Evidence that discriminates that property. A convenient proxy is not proof of a stronger proposition: references do not by themselves prove productive reachability; compilation does not prove behavior; passing tests do not prove untested compatibility; matching signatures do not prove semantic equivalence.

Runtime supplies observations. Main owns interpretation.

## Structural relations

`symbol_relations` is a structural coding capability.

- `query="relations"` returns the local relation view.
- `query="reachability"` resolves explicit roots or objective Python entrypoint signals, searches the structural graph and can materialize a shortest root-to-target path with edge coordinates.

A positive path returns `coverage.objective_complete=true` for the structural reachability query and suppresses unrelated unresolved-dynamic frontiers. When a path is not established, depth boundaries, relevant dynamic/ambiguous resolution, parse errors or scan limits may remain objective frontiers behind continuation handles.

The tool does not label code `live`, `dead`, `legacy`, safe or removable.

## Physical inference envelope

Semantic freedom operates inside fixed physical containment:

```text
backend context window          <= 32768 tokens
message/job prompt attempts     <= 90000 tokens
message/job completion          <= 8000 tokens
message/job physical total      <= 98000 tokens
```

Every backend attempt charges its full locally estimated prompt to the hard envelope regardless of cache. Turns, tools, LLM calls and deadline remain independent fuses.

In `self_check`, Claim runs only when grounded runtime state exists (Observation, Evidence, Investigation or WriteTransaction). A no-state Final can be accepted without verifier work. Explicit `verified` mode always runs Claim.

## Structured transport

Agent and Claim require strict JSON Schema. There is no capability downgrade, JSON-object fallback, prompt fallback, structural repair call or truncation replay.

## Writes

```text
Main LLM patches
→ deterministic dry-run
→ explicit confirmation
→ WriteTransaction apply
→ compile/tests/full output verification
→ success or rollback
```

Writes are the only route to real workspace mutation controlled by the agent.

## Sandbox

`run_command` executes arbitrary shell/network/package/build/test work only inside a strong writable project snapshot. `backend=auto` prefers Docker, then Bubblewrap. The real workspace is never mounted read-write into the unrestricted command environment.

If no strong backend exists, `run_command` returns `SANDBOX_UNAVAILABLE` with `retryable=false`. That becomes a Runtime Fact and terminal capability fact for the job; it does not authorize weak local execution.

A network-enabled sandbox protects host/workspace integrity, **not confidentiality of non-secret source copied into the sandbox**. Code visible to a process with network access can in principle be transmitted externally.

## Persistence

Rev5.8 is a clean break. Config, Session, queue and project-memory schemas are exact 5.8. Session and project-memory loaders reject missing or unknown same-version envelope fields instead of defaulting/filtering them. Earlier state is rejected, never migrated or adapted.

Core contracts use one canonical English representation. Provider/environment variability belongs behind adapters or capabilities; it must not create aliases or dual-read contracts inside AgentSession/Runtime state.

## Future architectural direction

The current product remains a coding agent. The broader design direction is to make `Observation`, `Coverage`, `Frontier` and `Handle` increasingly domain-neutral so future capabilities can reuse the same Core observation protocol without embedding their domain into `AgentSession`.

That direction is documented separately in [architectural-direction.md](architectural-direction.md) and must not be read as a claim that non-coding toolpacks are currently implemented.

## Protected resource read boundary retained in Rev5.8

Rev5.8 retains the physical protected-resource identity boundary established in Rev5.7.7 and uses one explicit protected-resource policy. Normal source code always remains observable regardless of identifiers or literal content. Credential/private-key stores are identified by official resource-path semantics, then resolved to physical file identity so symlink and hard-link aliases inherit the same boundary. No content-based secret scanner exists.

`list_tree` may expose that a protected resource exists and marks its content access as protected. `read_file`, source search, symbol parsing, transaction dry-run and direct diff content do not expose that physical resource through either its official path or an alias. `git_status` may expose paths/status without content. Sandbox snapshots omit every protected physical alias and report the omissions. Generic PEM/public-key/certificate files and `.env` templates such as `.env.example` remain readable.

Coverage is scope-aware. A search may be complete for `readable_workspace_files` while remaining incomplete for the whole workspace because protected resources were deliberately excluded; the exclusion is represented as a `protected_resource_boundary` and preserved in negative Evidence. Stable protected-resource denials are resource-scoped observations reusable within the current workspace epoch, so a different line range does not trigger the same impossible read again. Security therefore cannot silently delete ordinary source or turn a restricted region into a false global absence.
