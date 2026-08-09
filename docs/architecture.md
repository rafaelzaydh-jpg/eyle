# Eyle Rev5.2.9 architecture

```text
interface
→ runtime/service
→ AgentSession
   ├─ request / conversation_background
   ├─ investigation (semantic purpose ledger)
   ├─ ObservationLedger + workspace_epoch (physical reality memory)
   ├─ investigation_map (observable navigation history)
   ├─ Evidence
   └─ runtime authority/progress state
→ administrative structured handshake
→ main LLM ↔ transactional Investigation updates
→ runtime contract admin ↔ 16 deterministic tools + live workspace
→ deterministic Final Gate
→ Claim Review
   ├─ supported + no unresolved material gap → response
   ├─ contradicted → reopen mapped debt → Main LLM
   └─ insufficient / semantic gap → same directed follow-up route
```

## Responsibility boundaries

- **Main LLM:** defines material Investigation targets, chooses tools, interprets Evidence, proposes target deltas, writes the answer and patch intent.
- **Runtime:** owns the canonical Investigation state, commits valid target deltas independently, preserves accepted work, validates schema/IDs/Evidence existence, executes tools/transactions, and administers physical authority.
- **Tools:** deterministic observations/actions; no semantic routing.
- **Evidence Core:** complete runtime-owned EvidenceRecords plus bounded model-visible views.
- **Claim Review:** the single second-brain semantic verifier for provisional answer Claims and Investigation target coverage; it never grants tool authority, rewrites the answer, or chooses tools.

**The LLM decides semantics; the runtime validates contracts.**

## Investigation Contract

A target contains exactly `id`, `goal`, `status`, `evidence_ids`, and `reason`. Status is `open`, `established`, or `dismissed`. The runtime owns the canonical target list; the Main LLM returns only `investigation_updates` for targets it adds or changes. Unmentioned targets stay committed.

Runtime invariants are mechanical only:

- target updates are applied independently; one invalid sibling does not roll back accepted siblings;
- IDs are unique within one update batch;
- an existing target's goal cannot silently mutate;
- committed Evidence cannot silently disappear;
- cited Evidence IDs must exist;
- `established` requires Evidence and a reason;
- `dismissed` requires a reason.

The runtime does **not** decide whether a target is necessary, whether Evidence proves it, or whether dismissal is semantically valid. Claim Review can challenge a declared target through `semantic_gaps[].target_id`. A material gap absent from the contract uses `target_id=null`, leaving target creation to the Main LLM.

## Directed completion

For project-grounded requests, the Main LLM must declare a non-empty Investigation Contract before tool/write/final work. A grounded final with an `open` target is rejected deterministically before Claim Review. Physical limits (turns, tools, tokens, deadline) remain fuses, not semantic completion criteria.

## Observation authority

The Main LLM may request any available observation again. Runtime normalizes the tool+arguments and checks `ObservationLedger` at the current `workspace_epoch`. A known observation is replayed/rehydrated without physical execution; a new observation proceeds to authority. Prompt visibility is independent: if a source body left the working set, Runtime restores retained reality instead of rerunning the tool. Complete zero-match searches and `SYMBOL_NOT_FOUND` can become EvidenceRecords describing exactly the observed absence, without Runtime inferring legacy/dead-code semantics.

A verified workspace write increments `workspace_epoch`; session/memory bookkeeping does not. Thus a search may legitimately run again after code changes while remaining reusable across ordinary reasoning turns.

## Transactional tool authority

Every job still starts with the same 12-tool base fuse. Runtime deposits one `committed_progress` epoch only when a Main-LLM decision links objectively new runtime Evidence to an already-committed Investigation target. Multiple target changes in one decision still count as one epoch, so target fragmentation cannot manufacture authority.

That deposit is dormant. Only when an atomic tool batch would otherwise hit the physical gate, at least one target is still objectively `open`, and one or more committed-progress epochs remain unspent may runtime convert each unspent epoch into +4 tool calls. There is no cumulative earned-extension ceiling. Each Evidence ID can finance committed progress at most once for the whole session, so reopen/remap cycles cannot recycle old Evidence into authority. Claim Review is not consulted and cannot grant credit.

Tool batches remain atomic for novel physical work. Unified preflight first removes invalid calls, duplicates and replays; authority is calculated only from the remaining novel calls. If no earned extension is available and that novel batch cannot fit, none of its novel tools execute. Repeated identical rejected batches against unchanged canonical state are stopped as `ADMINISTRATIVE_LOOP`.

## Evidence continuity

Evidence linked to targets is pinned as compact index metadata while full source content remains runtime-owned and bounded views remain subject to the context engine. `visible_source_ranges` answers only whether source text is already present in the current prompt. `ObservationLedger` separately answers whether the computer must execute the observation again. These concerns no longer share a blocker.

## Navigation continuity

`investigation_map` remains separate from the contract. It answers “where have I already looked?” while `investigation` answers “what still needs to be established?”. `inspect_project` observable summaries now preserve the current `entrypoint_signals`, `test_signals`, `ci_signals`, `framework_signals`, and `relation_signals` schema.

## Structured boundary

`llm/structured.py` is the canonical source for the only two task-semantic profiles: `agent` and `claim_verifier`. The adaptive handshake chooses `json_schema`, `json_object`, or prompt JSON behaviorally; Eyle always validates locally.

## Writes and guards

Writing remains `action=patches` through one supervised transaction. The 8 main-agent turns, cumulative token budgets, no-progress controls, and repeated-call protections remain unchanged. The tool fuse still starts at 12 and can extend only through the bounded committed-progress authority mechanism above.

## Rev5.2.1 recovery boundary

An insufficient Claim may carry `target_id=<existing target>`. This is a semantic decision made by Claim Review. The runtime validates that the ID exists and reopens exactly that target; it never guesses a target from Claim text. Semantic Gaps retain the same rule. During a pending semantic follow-up, no-progress is only an observable stall signal and cannot force the Main LLM to answer.

Agent finals are structurally canonical (`answer`, `evidence_ids`, `limitations`). Once Claim Review has already returned the job for follow-up, Rev5.2.2 reserves exactly one configured verifier call (900 tokens by default). The reserve is physical budget authority only and no longer scales from historical Claim/gap count.
## Rev5.2.2 runtime contract hardening

`workspace_scope` is a Main-LLM semantic declaration with exactly `mode` (`none|read|write`) and `reason`. Runtime validates shape, monotonic authority and observable consistency: project actions cannot use `none`, patches require `write`, and a `read/write` declaration cannot silently downgrade. Production no longer uses lexical request classifiers to grant grounding/write authority.

To make `none` fail-closed without giving semantic interpretation back to runtime, a non-chat final in an active workspace is checked by the existing semantic verifier in `verify_workspace_scope` mode. That pass returns no Claims/Findings: it either accepts `none` or emits one unmapped `scope_gap`; runtime only persists the result and returns control to the Main LLM.

Writes are contract-gated twice: patch proposals are rejected while any Investigation target is open, and a persisted write confirmation is rechecked before apply. Persisted file Evidence is rehydrated from its exact path/range on resume only when stored file/content hashes match; stale Evidence releases its visible range so the Main LLM can observe it again.

Workspace source surfaces share a single secret policy, and persistence locking is OS-backed across processes rather than thread-local only.

