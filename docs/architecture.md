# Eyle Rev5.2.3 architecture

```text
interface
→ runtime/service
→ AgentSession
   ├─ request / conversation_background
   ├─ investigation (semantic purpose ledger)
   ├─ investigation_map (observable navigation history)
   ├─ Evidence
   └─ runtime state
→ administrative structured handshake
→ main LLM ↔ 16 deterministic tools + live workspace
→ deterministic Final Gate
→ Claim Review
   ├─ supported + no unresolved material gap → response
   ├─ contradicted → local Repair → Reverify
   └─ insufficient / semantic target gap → directed main-agent follow-up
```

## Responsibility boundaries

- **Main LLM:** defines material Investigation targets, chooses tools, interprets Evidence, updates target status, writes the answer and patch intent.
- **Runtime:** preserves target identity/state, validates schema/IDs/Evidence existence, executes tools and transactions, enforces physical limits and Final Gate invariants.
- **Tools:** deterministic observations/actions; no semantic routing.
- **Evidence Core:** complete runtime-owned EvidenceRecords plus bounded model-visible views.
- **Claim Review:** the single semantic verifier for answer Claims and Investigation target coverage.

**The LLM decides semantics; the runtime validates contracts.**

## Investigation Contract

A target contains exactly `id`, `goal`, `status`, `evidence_ids`, and `reason`. Status is `open`, `established`, or `dismissed`.

Runtime invariants are mechanical only:

- IDs are unique;
- an existing target cannot silently disappear;
- an existing target's goal cannot silently mutate;
- cited Evidence IDs must exist;
- `established` requires Evidence and a reason;
- `dismissed` requires a reason.

The runtime does **not** decide whether a target is necessary, whether Evidence proves it, or whether dismissal is semantically valid. Claim Review can challenge a declared target through `semantic_gaps[].target_id`. A material gap absent from the contract uses `target_id=null`, leaving target creation to the Main LLM.

## Directed completion

For project-grounded requests, the Main LLM must declare a non-empty Investigation Contract before tool/write/final work. A grounded final with an `open` target is rejected deterministically before Claim Review. Physical limits (turns, tools, tokens, deadline) remain fuses, not semantic completion criteria.

## Evidence continuity

Evidence linked to targets is pinned only as compact index metadata. Full source content remains runtime-owned and bounded views remain subject to the existing context engine. `visible_source_ranges` continues to own physical read visibility; Rev5.2 does not introduce a second coverage system.

## Navigation continuity

`investigation_map` remains separate from the contract. It answers “where have I already looked?” while `investigation` answers “what still needs to be established?”. `inspect_project` observable summaries now preserve the current `entrypoint_signals`, `test_signals`, `ci_signals`, `framework_signals`, and `relation_signals` schema.

## Structured boundary

`llm/structured.py` remains the canonical source for the `agent`, `claim_verifier`, and `claim_repair` profiles. The adaptive handshake chooses `json_schema`, `json_object`, or prompt JSON behaviorally; Eyle always validates locally.

## Writes and guards

Writing remains `action=patches` through one supervised transaction. The existing 8 main-agent turns, 12 tool-call fuse, cumulative token budgets, no-progress controls, and repeated-call protections remain unchanged in Rev5.2.

## Rev5.2.1 recovery boundary

An insufficient Claim may carry `target_id=<existing target>`. This is a semantic decision made by Claim Review. The runtime validates that the ID exists and reopens exactly that target; it never guesses a target from Claim text. Semantic Gaps retain the same rule. During a pending semantic follow-up, no-progress is only an observable stall signal and cannot force the Main LLM to answer.

Agent finals are structurally canonical (`answer`, `evidence_ids`, `limitations`). Once Claim Review has already returned the job for follow-up, Rev5.2.2 reserves exactly one configured verifier call (900 tokens by default). The reserve is physical budget authority only and no longer scales from historical Claim/gap count.
## Rev5.2.2 runtime contract hardening

`workspace_scope` is a Main-LLM semantic declaration with exactly `mode` (`none|read|write`) and `reason`. Runtime validates shape, monotonic authority and observable consistency: project actions cannot use `none`, patches require `write`, and a `read/write` declaration cannot silently downgrade. Production no longer uses lexical request classifiers to grant grounding/write authority.

To make `none` fail-closed without giving semantic interpretation back to runtime, a non-chat final in an active workspace is checked by the existing semantic verifier in `verify_workspace_scope` mode. That pass returns no Claims/Findings: it either accepts `none` or emits one unmapped `scope_gap`; runtime only persists the result and returns control to the Main LLM.

Writes are contract-gated twice: patch proposals are rejected while any Investigation target is open, and a persisted write confirmation is rechecked before apply. Persisted file Evidence is rehydrated from its exact path/range on resume only when stored file/content hashes match; stale Evidence releases its visible range so the Main LLM can observe it again.

Workspace source surfaces share a single secret policy, and persistence locking is OS-backed across processes rather than thread-local only.

