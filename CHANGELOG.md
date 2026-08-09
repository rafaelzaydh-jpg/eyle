# Changelog

## Rev5.2.3 — Investigation Memory & Progress Semantics — 2026-08-09

- separated current-prompt source visibility from historical source telemetry; historical reads can no longer suppress a needed reread after the body leaves the prompt;
- pinned Evidence named by insufficient Claims/Semantic Gaps and reopened Investigation targets across semantic follow-up;
- changed no-progress accounting so `ok=true` without new Evidence/state change is not progress;
- suppressed unchanged repeated `project_stats`, `inspect_project`, `count_tokens`, `agent_info` and same-scope `run_tests` until an observable state-changing action;
- kept 16 public tools and the existing 8-turn / 12-tool / 9k-completion physical limits unchanged; added eight focused Rev5.2.3 regressions.


This file tracks public release-level changes. Detailed experimental and intermediate revision notes were intentionally removed before the Rev5 Git publication; Git history is the canonical record for future development.

## Rev5.2.2 — Runtime Contract Hardening — 2026-08-08

- replaced production authority from lexical `request_needs_project_evidence` / `request_requires_write` classifiers with a Main-LLM-declared `workspace_scope` contract (`none|read|write`);
- added fail-closed scope-only semantic review for non-chat finals that declare `workspace_scope=none` while a workspace is active, so semantic disagreement returns control to the Main LLM without runtime keyword inference;
- block patch proposals and confirmed write resumes while any Investigation target remains `open`;
- changed semantic-follow-up completion reservation to exactly one configured verifier call (default 900 tokens), preventing historical Claim/gap count from starving the next Main LLM turn;
- rehydrate persisted file Evidence on resume from the exact path/range only when stored file/content hashes still match; stale Evidence releases its read coverage for fresh investigation;
- replaced the process-local persistence lock with a portable OS-backed interprocess file lock;
- unified secret-path/content policy across workspace reads, code search/symbol reads, Git status and Git diff;
- kept 16 public tools and the existing 8-turn / 12-tool / 9k-completion physical limits unchanged; added eight focused Rev5.2.2 regressions.

## Rev5.2.1 — Semantic Follow-up Contract Recovery — 2026-08-08

- added nullable `target_id` to every Claim so the Claim Verifier can explicitly bind an insufficient Claim to an existing Investigation target;
- runtime now reopens targets only from reviewer-declared `target_id` mappings in Semantic Gaps or insufficient Claims; `null` never creates a target;
- replaced contradictory `NO_PROGRESS_ANALYSIS: stop using tools` behavior during semantic follow-up with `SEMANTIC_FOLLOWUP_STALLED`, which reports the stall/open targets but leaves the next semantic action to the Main LLM;
- made agent `final` canonical as exactly `answer`, `evidence_ids`, and `limitations` in both strict provider schema and local parsing;
- when Claim Review has already returned the task for follow-up, reserve an elastic verifier completion budget, sized from the already-observed prior review item count through the existing Claim Review budget logic, because another review is then a known mandatory stage;
- observable Investigation Contract decisions now expose compact `Tn=status` state summaries and explicit reopen events;
- kept 16 public tools and the Rev5.2 physical limits unchanged; added six focused Rev5.2.1 regressions.

## Rev5.2 — Investigation Contract & Directed Evidence — 2026-08-08

- replaced the free-form agent `plan` with a persistent Investigation Contract stored in `AgentSession`;
- added target states `open`, `established`, and `dismissed` with deterministic identity/Evidence invariants and no semantic runtime scoring;
- project-grounded finals cannot pass while declared material targets remain open;
- extended Semantic Gaps with nullable `target_id`, allowing Claim Review to challenge/reopen an existing target or report material scope absent from the contract without runtime-created semantics;
- target-linked Evidence is pinned as compact index metadata and included in semantic review even when it is older than the recent Evidence window;
- preserved `investigation_map` as navigation history, separate from semantic investigation purpose;
- corrected `inspect_project` observable summaries to retain the current entrypoint/test/CI/framework/relation signal schema;
- removed the lexical test-only phase shortcut; test wording no longer lets runtime semantically decide that further investigation is unnecessary;
- kept the 16 public tools and existing physical limits unchanged; no Planner/Manager agent, semantic file-ranking heuristic, new read-range coverage layer, or callers/callees/reference tools were added;
- added dedicated Rev5.2 regressions covering contract transitions, target reopening, Evidence pinning, schema integration and directed semantic follow-up.

## Rev5.1 — Context Boundaries & Investigation Continuity — 2026-08-08

- made `request` the explicit sole active task and replaced one-turn/duplicated context behavior with stable per-job `conversation_background`;
- preserved explicit ongoing conversational instructions across tool turns while marking prior-task context as non-authoritative;
- added compact `investigation_map` derived from observable successful tool history so `CLAIM_INSUFFICIENT` follow-up keeps navigation discoveries after bulky source views are cleared;
- blocked repeated/covered reads no longer increment the executed-identical-tool loop and now return the prior observable investigation map;
- added Local Finding Recovery after Claim recovery, preserving Claims/Semantic Gaps and regenerating only Findings before global revalidation;
- capped agent batches at four tool calls in provider schema and local parser and removed silent `calls[:4]` truncation;
- tagged failed assistant jobs and excluded them from future conversation background;
- removed obsolete `agent.task_context_token_budget` from the public config schema.

## Rev5 — GitHub Release — 2026-08-08

Rev5 is the publication baseline built from the validated Rev4.13.13 runtime. It does not redesign the agent loop; it consolidates the current architecture and removes accumulated release-document clutter.

### Current architecture

- One `AgentSession` execution loop.
- 16 public deterministic agent tools.
- One model-facing write protocol: `action=patches` → transactional dry-run → confirmation → apply → validation/rollback.
- Runtime-owned Evidence with proportional model-visible views.
- Deterministic Final Gate followed by one semantic Claim Review.
- Local Claim and Semantic Gap recovery without runtime semantic invention.
- Adaptive structured handshake per connection/model: `json_schema` → `json_object` → prompt JSON.
- Provider enforcement plus authoritative local Eyle validation.
- One structured contract source in `llm/structured.py`.

### Repository cleanup

- Removed historical root implementation/validation reports.
- Removed accumulated `docs/releases/` notes and obsolete Rev4.11 engineering notes.
- Removed redundant `UPDATE_HISTORY.md`.
- Rewrote README and canonical docs around the current architecture only.
- Preserved source, tests, dependency locks, assets, governance/security files, and machine-state `.gitkeep` directories.

## Development baseline — Rev4.13.13

The Rev5 codebase inherits the completed Rev4.13 line: canonical structured profiles, adaptive capability probing, deterministic answer anchors, proportional Claims/Evidence, Semantic Gaps, local Claim Repair/Reverify, local Semantic Gap recovery, bounded runtime telemetry, and supervised transactional writes.
