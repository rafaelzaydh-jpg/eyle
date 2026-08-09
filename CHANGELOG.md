# Changelog

## Rev5.2.9 — Progress-Earned Authority — 2026-08-09

- removed the artificial cumulative `max_earned_tool_extension=8` ceiling; every runtime-validated committed-progress epoch can unlock exactly `committed_progress_extension_calls` additional physical tools once;
- keeps extension credit dormant until the physical gate needs it, but converts every still-unspent progress epoch instead of discarding older earned progress;
- makes `investigation_updates.evidence_ids` a true additive delta: previously committed target Evidence is retained automatically and never has to be resent;
- adds a durable global credit-once Evidence ledger so the same Evidence ID cannot mint committed progress again through target reopen, target cloning or later semantic remapping;
- adds deterministic Claim-rework capacity feedback (remaining Agent calls, current physical authority and pending progress-funded authority) so the Agent can spend scarce follow-up calls deliberately;
- keeps the two-brain architecture, 12-tool base fuse, 8 normal Main-Agent turns and 12 task-wide LLM calls unchanged.

## Rev5.2.8 — Canonical Runtime Cleanup — 2026-08-09

- fixes false `ADMINISTRATIVE_LOOP` equivalence by keying rejected decisions on objective observed state plus the relevant physical-authority context;
- changes runtime-cycle progress to ignore free-form Investigation `reason/status` churn and count only observed reality, Evidence bindings, committed progress and workspace mutation;
- rejects any invalid tool batch atomically before physical authority so `INVALID_ARGUMENT`/phase contracts cannot be masked by a budget rejection;
- standardizes the public tool ABI on `path`, `line_start`, `line_end`, `symbol`, `limit`, `depth`, `filter`, `query`, `scope` and related existing English fields; legacy argument aliases are not accepted;
- teaches the existing Investigation contract that `open` targets may accumulate Agent-selected Evidence incrementally and recommends separable targets for independently provable debts;
- removes dead lexical workspace/write authority helpers and the `_semantic_read_signature` compatibility wrapper; renames current-context coverage to `_source_already_visible`;
- keeps the two-brain architecture, Observation Ledger, Claim follow-up, tool-credit policy and all physical limits unchanged; adds focused Rev5.2.8 regressions.

## Rev5.2.7 — Two-Brain Claim Follow-up

- Removed the `claim_repair` semantic profile, prompt, schema, state and local answer-rewrite pipeline. Production task semantics now belong only to `agent` and `claim_verifier`; the structured capability probe remains transport-only administration.
- `contradicted`, `insufficient` and semantic gaps now use one deterministic follow-up route: Runtime reopens reviewer-declared Investigation targets, pins cited Evidence and returns the reviewer debt to the Main LLM.
- Added Claim follow-up loop protection through the existing Decision Ledger. Identical reviewer debt against an unchanged canonical state fails as `CLAIM_REVIEW_STALLED` instead of spending repeated Agent/Claim cycles.
- Added a bounded Claim rework lane that uses only unused task-wide `max_llm_calls` capacity and reserves one later verifier call; `max_llm_turns` remains the normal investigation limit.
- Tightened `committed_progress`: only newly linked runtime Evidence can mint physical tool authority; a pure `established` status flip, including re-establishing a Claim-reopened target with the same Evidence, earns no credit.
- Fixed local Claim protocol recovery so recovered Claims preserve `target_id`.
- Kept Observation Ledger, transactional Investigation authority and tool-credit rules unchanged.


## Rev5.2.6 — Observation Ledger & Unified Runtime Preflight — 2026-08-09

- adds a persistent runtime-owned `ObservationLedger` keyed by normalized observation + `workspace_epoch`;
- replays/rehydrates identical observations without consuming physical tool calls, including `A -> B -> A`;
- records complete zero-match `search_code` and `SYMBOL_NOT_FOUND` as citable negative observations without assigning semantic meaning;
- moves authority/`earned_extension` after unified preflight so replayed/invalid/batch-duplicate calls cannot consume or earn physical authority;
- adds runtime-cycle progress accounting that survives early `continue` paths and a Decision Ledger that fails repeated identical rejected batches as `ADMINISTRATIVE_LOOP`;
- removes legacy `IDENTICAL_READ_BLOCKED`, `IDENTICAL_OBSERVATION_BLOCKED`, `SEMANTIC_READ_BLOCKED` execution paths and the pre-Rev5.2.5 full `investigation` snapshot fallback;
- keeps Claim Review unchanged as the separate semantic second brain and keeps the physical limits unchanged (8 Main turns, 12 LLM calls, 12 base tools, +4 earned extension cycles capped at +8);
- adds Rev5.2.6 regressions for replay, negative Evidence, persistence, workspace epochs, preflight authority and administrative-loop rejection.

## Rev5.2.5 — Transactional Contract Authority — 2026-08-09

- removed the Rev5.2.4 coupling where Claim Review minted tool credit; Claim Review remains only the second-brain semantic verifier of provisional conclusions;
- changed the Main Agent contract from full `investigation` snapshots to `investigation_updates` deltas while runtime owns the canonical Investigation Contract;
- applies valid target updates independently, preserves committed siblings when another update is rejected, and prevents committed Evidence from silently disappearing;
- records one objective `committed_progress` epoch per productive Main-LLM update cycle instead of rewarding Claim count, target count, or tool `ok=true`;
- keeps the 12-tool base fuse and grants dormant +4 `earned_extension` only at the physical budget gate when open debt remains and new committed progress exists since the previous extension, capped at +8;
- keeps tool batches atomic and keeps the history expand-all/collapse-all control while exposing committed-progress deposits and earned extensions;
- keeps 16 public tools, 8 Main-LLM turns, 12 base tool calls and the 9k completion budget unchanged; adds 13 focused Rev5.2.5 regressions.

## Rev5.2.4 — Verified Progress Budget — 2026-08-09

- kept the physical base fuse at 12 tool calls and added reviewer-earned tool credit: +4 when Claim Review confirms structurally new supported material while semantic debt remains, capped at +8 bonus in this release;
- prevented repeated supported Claims from minting duplicate budget by persisting structural support signatures in `AgentSession`;
- made tool batches atomic against the currently authorized budget: an oversized batch executes zero tools and returns the allowed batch size to the Main LLM for semantic reprioritization;
- exposed base, earned bonus, effective tool limit and bonus cycles in safe observable history/trace data;
- added `expandir tudo` / `recolher tudo` to the execution-history UI so all LLM/decision/tool accordions can be opened with one click;
- kept 16 public tools, 8 Main-LLM turns and the 9k completion budget unchanged; added seven focused Rev5.2.4 regressions.

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

The Rev5 codebase inherits the completed Rev4.13 line: canonical structured profiles, adaptive capability probing, deterministic answer anchors, proportional Claims/Evidence, Semantic Gaps, local Claim follow-up/Reverify, local Semantic Gap recovery, bounded runtime telemetry, and supervised transactional writes.
