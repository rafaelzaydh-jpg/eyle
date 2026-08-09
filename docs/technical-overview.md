# Technical overview — Eyle Rev5.2.3

Rev5.2 adds direction to the existing Rev5.1 agent loop without adding another agent or another public tool.

## From free-form plan to Investigation Contract

The old `plan: list[str]` carried prose but no durable relationship between goal, Evidence and completion. Rev5.2 removes it and stores `AgentSession.investigation`: a complete snapshot of semantic targets owned by the Main LLM.

`eyle/core/investigation.py` contains only deterministic pure helpers for contract validation, open-target queries, target Evidence IDs and reviewer-directed reopenings. It does not rank files, choose targets or interpret Evidence.

## Main-agent flow

The same structured Main LLM call that chooses tools also creates/updates the Investigation Contract. No Planner call is added. Project-grounded tool/write/final decisions require a non-empty contract. Existing targets remain identity-stable across turns.

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

The adaptive structured handshake and three profiles remain unchanged in responsibility. `llm/structured.py` now exposes `investigation` in the agent envelope and `target_id` in Semantic Gaps. Provider enforcement remains optional assistance; local parsing and runtime validation remain authoritative.

## Rev5.2.1 delta

The benchmark exposed a recovery defect rather than a discovery-capability defect. Rev5.2.2 therefore does not add tools or planners. It strengthens the existing contract: Claim Review can bind an insufficient Claim to a target, runtime reopens only that declared target, no-progress cannot contradict a pending semantic follow-up, and final/reviewer budget contracts are explicit.

## Rev5.2.2 delta

Rev5.2.2 hardens the runtime boundary rather than adding discovery capability. Main LLM output now carries `workspace_scope`; runtime no longer grants project grounding/write authority from request vocabulary. An ungrounded non-chat final in an active workspace receives a scope-only semantic check, preserving fail-closed behavior without runtime semantic classification.

Semantic follow-up reserves one verifier ceiling instead of projecting reserve from old review size. Open Investigation debt blocks writes before proposal and again at confirmed resume. Persisted Evidence is hash-checked and rehydrated after resume, secrets are denied through one workspace policy across read/search/Git, and persistence uses an interprocess lock. No public tool or physical budget limit changed.
