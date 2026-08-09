# Technical overview — Eyle Rev5.2.9

Rev5.2 adds direction to the existing Rev5.1 agent loop without adding another agent or another public tool.

## From free-form plan to Investigation Contract

The old `plan: list[str]` carried prose but no durable relationship between goal, Evidence and completion. Rev5.2 stores semantic debt in `AgentSession.investigation`; in Rev5.2.9 that list is the runtime-owned canonical contract while the Main LLM proposes only additive `investigation_updates` deltas.

`eyle/core/investigation.py` contains only deterministic pure helpers for contract validation, open-target queries, target Evidence IDs and reviewer-directed reopenings. It does not rank files, choose targets or interpret Evidence.

## Main-agent flow

The same structured Main LLM call that chooses tools also proposes Investigation deltas. No Planner call is added. Runtime applies valid deltas independently and preserves all omitted targets. Project-grounded tool/write/final decisions require a non-empty canonical contract. Existing targets remain identity-stable across turns.

## Claim Review integration

Claim Review receives the request, answer anchors, selected Evidence and the current Investigation Contract. Semantic Gaps now include `target_id`:

- existing target ID: the reviewer is challenging that declared target;
- `null`: material request scope is missing from the contract itself.

A named semantic gap reopens only that existing target. An unmapped gap never causes runtime to invent a new target.

## Evidence selection

Final-answer Evidence and Evidence referenced by Investigation targets are unioned for semantic review. Target-linked Evidence is pinned in the compact session index so long-running investigations retain its pointer without replaying source bodies.

## Existing navigation reused

Rev5.2 keeps `search_code`, `find_symbol`, `inspect_project`, `read_file`, `read_range`, `investigation_map`, and `visible_source_ranges`. No `references/callers/callees` tools were added because the failing AgentSession benchmark already found the relevant locations; the observed failure was choosing what to establish next.

A stale summary-field mismatch was fixed: `inspect_project()` already emitted useful structural `*_signals`, but the investigation-map serializer looked for obsolete field names. The current signals now survive in the observable map.

The old lexical test-only phase shortcut was removed. `run_tests` no longer causes runtime to infer that investigation is semantically complete; the Investigation Contract and Main LLM retain that authority.

## Structured and provider boundary

The adaptive structured handshake remains transport-only, while the task-semantic profiles are exactly `agent` and `claim_verifier`. `llm/structured.py` exposes `investigation_updates` in the agent envelope while the prompt carries canonical `investigation`; Semantic Gaps retain `target_id`. Provider enforcement remains optional assistance; local parsing and runtime validation remain authoritative.

## Rev5.2.1 delta

The benchmark exposed a recovery defect rather than a discovery-capability defect. Rev5.2.2 therefore does not add tools or planners. It strengthens the existing contract: Claim Review can bind an insufficient Claim to a target, runtime reopens only that declared target, no-progress cannot contradict a pending semantic follow-up, and final/reviewer budget contracts are explicit.

## Rev5.2.2 delta

Rev5.2.2 hardens the runtime boundary rather than adding discovery capability. Main LLM output now carries `workspace_scope`; runtime no longer grants project grounding/write authority from request vocabulary. An ungrounded non-chat final in an active workspace receives a scope-only semantic check, preserving fail-closed behavior without runtime semantic classification.

Semantic follow-up reserves one verifier ceiling instead of projecting reserve from old review size. Open Investigation debt blocks writes before proposal and again at confirmed resume. Persisted Evidence is hash-checked and rehydrated after resume, secrets are denied through one workspace policy across read/search/Git, and persistence uses an interprocess lock. No public tool or physical budget limit changed.

## Rev5.2.3 delta

Source suppression follows current-prompt visibility rather than historical visibility, reviewer-linked Evidence remains pinned through semantic follow-up, and unchanged successful observations no longer count as progress. Repeated project/runtime observations and same-scope tests are reused until observable state changes.

## Rev5.2.4 delta

Rev5.2.4 introduced atomic tool batches and an initial reviewer-coupled earned-budget experiment. That coupling is historical and is replaced by Rev5.2.7.

## Rev5.2.5 delta

Claim Review returned to a pure second-brain role: it verifies provisional final Claims/target coverage and never grants physical tool authority. Main LLM output contains `investigation_updates` deltas. Runtime owns the canonical Investigation Contract, commits structurally valid sibling updates independently and records `committed_progress`; dormant +4 extensions are released only at the physical tool gate, capped at +8.

## Rev5.2.6 delta

Physical observation identity became runtime-owned in `ObservationLedger`, separate from prompt visibility and from `tool_history`. The Main LLM remains free to request/reconsider an observation; when normalized tool+arguments were already executed in the same `workspace_epoch`, Runtime replays/rehydrates retained reality without incrementing physical tool calls. Complete zero-match searches and `SYMBOL_NOT_FOUND` are citable observation Evidence, not semantic conclusions. Unified preflight computes physical authority only after duplicate/replay suppression, and the old `IDENTICAL_READ_BLOCKED`, `IDENTICAL_OBSERVATION_BLOCKED`, `SEMANTIC_READ_BLOCKED` paths and full-investigation snapshot fallback were removed.

## Rev5.2.7 delta

The separate Claim Repair producer was removed. Production task-semantic profiles are exactly `agent` and `claim_verifier`; contradicted/insufficient Claims and semantic gaps return through one Runtime reopen/pin/feedback route to the Main Agent. Reviewer-debt loops use the existing Decision Ledger, and re-establishing a reviewer-reopened target with the same Evidence cannot mint tool credit.


## Rev5.2.8 delta

No new cognitive/runtime subsystem is added. The existing Decision Ledger now fingerprints rejected work against objective runtime reality (Evidence/observation fingerprints, Investigation Evidence bindings, committed-progress/workspace epochs) and the relevant tool-authority state, so a new observation or changed remaining authority cannot be misclassified as the same rejected decision. Runtime-cycle progress uses the same objective notion and does not reset merely because the Main Agent rewrites an Investigation reason or flips status without new Evidence.

Tool preflight keeps phase policy outermost, then rejects malformed batches atomically before authority. The public model-facing tool ABI is canonical English (`path`, `line_start`, `line_end`, `symbol`, `limit`, `depth`, `filter`); old aliases are removed rather than translated. The Agent prompt explicitly tells the Main Agent to attach material Evidence to open targets as it is discovered and to prefer independently provable target granularity. Dead lexical workspace/write helpers and the semantic-read signature compatibility wrapper are deleted.

## Rev5.2.9 delta

Rev5.2.9 keeps the same Agent/Runtime/Claim architecture and changes only authority accounting and Investigation delta semantics. `investigation_updates.evidence_ids` is additive: Runtime preserves every previously committed target Evidence ID automatically, so the Agent sends only newly material IDs. Tool credit is globally credit-once by Evidence ID and one progress epoch is still minted at most per Main-LLM decision cycle. Every unspent committed-progress epoch can convert into `committed_progress_extension_calls` physical calls (default +4) exactly once when the tool gate needs authority; the former cumulative +8 ceiling is removed. Claim follow-up exposes remaining Agent calls plus current and pending physical authority so the Agent can investigate on the earliest rework call and reserve its last call for a corrected final.
