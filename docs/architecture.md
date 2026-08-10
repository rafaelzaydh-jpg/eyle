# Eyle Rev5.6 architecture

## Frozen authority boundary

```text
Main LLM = semantic authority
Runtime  = physical/structural authority
Claim    = independent semantic challenge
```

The Main LLM decides request meaning, tools, semantic debt, Evidence sufficiency, stopping, writes and Final content. Runtime never classifies intent or creates semantic debt. Claim is the single independent semantic challenger.

## Canonical state ownership

Rev5.6 extends the ObservationLedger principle to every factual lifecycle:

```text
physical tool reality      → ObservationLedger
citable factual units      → EvidenceLedger
semantic debt              → Investigation
runtime decisions/rejects  → DecisionLedger
LLM calls/provider attempts→ LLMCallLedger (ExecutionContext)
workspace mutation         → WriteTransaction
semantic audit             → ClaimReview
```

A history, counter, summary, prompt view or diagnostic is a **projection** of its owner. It is not persisted as a second source of truth.

### ObservationLedger

Owns physical tool events, reusable observation identity, file-range coverage, replay identity and the pending model-facing observation batch. `observation_map`, public tool history, physical tool count and replay count are derived views. Hot replay/source payloads are never persisted.

### EvidenceLedger

Owns Evidence identity, registration, persistence, rehydration, freshness lookup and the compact Evidence index. One Observation may produce multiple Evidence items, so Evidence remains separate from Observation.

### DecisionLedger

Owns every runtime decision event plus deterministic rejection fingerprint. Public decision history and repeated-rejection counts are derived. Each rejection attempt is an event; the same deterministic rejection fingerprint on unchanged objective state is derived as a repetition and later attempts may be marked `stalled`.

### LLMCallLedger / ExecutionContext

A logical LLM call is created once with its prompt metadata and accumulates physical provider attempts inside the same record. There are no separate prompt/response arrays and no later correlation step.

`ExecutionContext` owns run-scoped deadline, physical token/call budgets and the LLMCallLedger. `config` stays configuration and is never mutated into runtime state.

### WriteTransaction

One transaction owns patches, attempts, dry-run, confirmation identity, apply, compile/tests/full verification, failure and rollback. Confirmation pending state carries only the `transaction_id` plus the serialized Session; patches are not duplicated outside the transaction.

### ClaimReview

Stores only the semantic review itself. Summary/follow-up debt is derived when needed and is never a parallel persisted state.

## Grounded Claim outcomes

Claim receives one canonical task plus bounded coordinates from the factual domains that can actually ground a verdict:

```text
request
answer:<anchor_id>
evidence:<EvidenceID>
runtime:<runtime_fact_id>
investigation:<target_id>
```

This avoids the false equivalence `grounding == EvidenceLedger`. Runtime mechanically validates coordinate existence only. Claim decides what those coordinates establish. `material_satisfaction.status` is `satisfied`, `gap`, or `blocked`; a physical blockage can therefore be a correct final delivery when a relevant Runtime Fact proves the limitation. Semantic gaps carry `required_property`, which states what remains unresolved without prescribing a tool.

Non-retryable physical tool failures are retained in the job's `ExecutionContext.terminal_capabilities`; callable tool projections exclude those capabilities for the rest of the job. This is resource/reality authority, not semantic task routing.

## Investigation

Investigation is optional persistent semantic debt created only by the Main LLM.

```text
[]        → no persistent semantic debt declared
[T1,...]  → debt declared by Main LLM
```

Once declared, Runtime enforces only structural invariants: durable identity/goal, valid Evidence IDs, Evidence for `established`, reason for closure, and no Final while a target is open.

## Final

One object only:

```json
{"answer":"...","limitations":[]}
```

## Progressive capability view

`TOOLS` remains the sole executable registry. The Main LLM does not receive every expanded contract on every turn.

```text
unused callable tool  → capability_index: compact signature + purpose
first real request    → Runtime validates canonical schema directly
later turns           → active_tools: expanded contract for that requested tool
```

There is no Tool Selector call and no persisted active-tool state. Expanded membership is derived from actual `tool/tool_calls=requested` DecisionLedger events. A tool can be called on its first appearance in `capability_index`.

## Physical training envelope

Semantic freedom exists inside fixed physical containment:

```text
backend context window          <= 32768 tokens
message/job prompt attempts     <= 90000 tokens
message/job completion          <= 8000 tokens
message/job physical total      <= 98000 tokens
```

Every backend attempt charges its complete locally estimated prompt to the hard envelope, regardless of cache. `prompt_tokens_effective` remains useful telemetry but is not budget authority. Turns, tools, LLM calls and deadline remain independent fuses.

In `self_check`, Claim is invoked only when objective grounded runtime state exists (Observation, Evidence, Investigation or WriteTransaction). A no-state Final is accepted without a verifier call; Runtime does not infer that the task is "simple". Explicit `verified` mode always runs Claim.

## Structured transport

Agent and Claim require strict JSON Schema. There is no capability downgrade, structural repair call or truncation replay.

## Writes

```text
Main LLM patches
→ deterministic dry-run
→ explicit confirmation
→ WriteTransaction apply
→ compile/tests/full output verification
→ success or rollback
```

## Physical limits

Turns, tools, LLM calls, tokens and deadline are physical safety fuses only. Ledgers are not truncated by arbitrary item-count limits. Context may be cropped only to satisfy the actual model window.

## Persistence

Rev5.6 is a clean break. Config, Session, queue and project-memory schemas are exact 5.6. Earlier state is rejected, never migrated or adapted.

## Property-directed Evidence

Eyle is a general coding agent, not a dead-code auditor. The Main LLM must identify the **material property** requested and gather Evidence that discriminates that property. A convenient proxy is not proof of a stronger proposition: references do not by themselves prove productive reachability; compilation does not prove behavior; passing tests do not prove untested compatibility; a matching signature does not prove semantic equivalence. Runtime only supplies observations.

## Structural relations

`symbol_relations` is a general navigation primitive, not a semantic oracle. Python analysis includes definitions, calls/imports plus structural binding edges such as registry values, assignments, callback arguments, decorators and inheritance. Callers may request `direction=incoming|outgoing|both` and keep literal text references disabled unless they are actually needed. Optional root reachability reports structural paths only; it never labels code `live`, `dead`, `legacy`, safe or removable. Coverage metadata continues to disclose incomplete static resolution.

## Unrestricted per-job sandbox

`run_command` executes arbitrary shell/network/package/build/test work only inside a strong writable project snapshot. `backend=auto` prefers Docker, then Bubblewrap. Docker starts one persistent container per job using `python:3.12-slim` by default and `--pull missing`; the container keeps package installations and rootfs changes between commands. Runtime mounts only the sanitized snapshot as `/workspace`, never the real workspace read-write, and destroys the container/snapshot at job end.

If no strong backend exists, `run_command` returns `SANDBOX_UNAVAILABLE` with `retryable=false`. That objective failure becomes a Runtime Fact and a terminal capability fact for that job; it does not cause automatic task failure and does not authorize weak local execution. Sandbox mutations remain experiments only. Real source mutation still has one path: confirmed `WriteTransaction`.
