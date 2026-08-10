# Changelog

## Rev5.6 — Grounded Outcomes & Docker Backend — 2026-08-10

- Replaces Evidence-only Claim grounding with typed coordinates: `request`, `answer:<anchor>`, `evidence:<id>`, `runtime:<fact>`, and `investigation:<target>`. Runtime validates coordinate existence; Claim owns semantic sufficiency.
- Makes `material_satisfaction` explicit as `satisfied|gap|blocked`; truthful physical impossibility can be a valid final outcome grounded by Runtime Facts.
- Removes the schema/validator split that allowed Claim output to be accepted structurally and rejected later for missing Evidence IDs; all Claim verdict/gap grounding arrays are non-empty in the strict schema/parser contract.
- Projects bounded Runtime Facts from ObservationLedger into Claim packets so physical failures such as `SANDBOX_UNAVAILABLE` can be verified without fabricating EvidenceLedger entries.
- Adds job-scoped terminal capabilities: a tool result with `retryable=false` is remembered physically and that capability is removed from later callable views for the same job.
- Extends `symbol_relations` with registry/assignment/callback binding edges plus directed `incoming|outgoing|both` projections and opt-in literal text references.
- Makes Docker the recommended/default strong sandbox backend in `auto`, with Bubblewrap fallback. Docker uses one persistent disposable container per job, default `python:3.12-slim`, `--pull missing`, network access and a writable sanitized snapshot; the real workspace is never mounted read-write.
- Preserves unrestricted package/toolchain installation inside the Docker sandbox while keeping real-workspace writes exclusively behind confirmed `WriteTransaction`.
- Clean break: config/session/queue/project-memory schemas are exact 5.6; previous persisted state is rejected rather than migrated.
- Current validation: 155 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5.5 — Canonical Task Input Integrity — 2026-08-10

- Makes `needs_user` a strict blocking-clarification object and prevents greetings/task-acquisition conversation from becoming false pending work.
- Folds user clarification persistently into the single canonical request instead of a transient `user_response` observation, so Main LLM and Claim audit the same task after intermediate tools.
- Validates pending TTL/project ownership before `user_input` can capture a new message and fixes clarification cancellation ordering.
- Separates per-job physical turn/tool histories from cumulative task state and adds a canonical request-identity invariant across resumed Agent/Claim calls.
- Clean break: config/session/queue/project-memory schemas are exact 5.5.5.
- Current validation: 149 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5.4 — Property-Directed Evidence & General Sandbox — 2026-08-10

- Generalizes Directed Evidence: the Main LLM must identify the actual material property requested and must not substitute easier proxies such as references, imports, compilation, tests or signatures when the stronger property is behavior/reachability/causality/compatibility/completeness/absence.
- Adds `symbol_relations`, a general structural primitive. Python uses AST-aware definitions/calls/imports/decorators/inheritance plus optional root-to-symbol call paths; other source files contribute truthful textual references. The tool never emits live/dead/legacy semantics and reports unresolved dynamic sites explicitly.
- Adds `run_command`, an unrestricted shell capability inside a writable per-job project snapshot. It may use network, install workspace-local packages, compile and test. Only Bubblewrap or configured Docker qualify; weak local-process backends fail closed. Sandbox mutations persist inside the current job but never mutate/authorize writes to the real workspace.
- Makes `find_symbol` a locator in the model view; raw source remains Runtime Evidence and `read_file` is the canonical content tool.
- Makes `inspect_project` model-facing output macro-level instead of replaying its full relation/test inventories.
- Removes Runtime freshness hashes from the Main-LLM Evidence index and slims Observation navigation; canonical ledgers still retain complete freshness/coverage state.
- Detects the exact Investigation failure seen in the `extract_symbols` benchmark: the same structurally invalid target transition repeated without objective state change now stalls/fails on the second repetition instead of burning repeated LLM calls. Runtime still never chooses Evidence IDs semantically.
- Further compresses capability discovery: 17 tools fit in roughly 508 local-estimated tokens in the current registry.
- Keeps the 98k physical message/job envelope, 32k per-call Llama context cap, 24 LLM turns and 64 tool calls as independent physical fuses.
- Clean break: config/session/queue/project-memory schemas are exact 5.5.4.
- Current validation: 143 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5.3 — Progressive Capabilities & Budget Guard — 2026-08-10

- Replaces the full expanded tool catalog on every Agent call with progressive model views: compact `capability_index` for unused callable tools and expanded `active_tools` only after actual Main-LLM requests.
- Tool activation is derived from canonical DecisionLedger events; there is no Tool Selector LLM, activation call, semantic router or persisted active-tool state. First use is directly validated against the canonical `TOOLS[name].input_schema`.
- Removes `tool_taxonomy` from the Main-LLM prompt. Registry category/effect metadata remains Runtime-owned.
- Adds a hard training envelope per user message/job: 90k prompt attempts, 8k completion and 98k physical total tokens. Every backend attempt charges its full prompt even when cached; cache discount is diagnostic only.
- Hard-caps each backend request to the current Llama Server context of 32768 tokens, in both strict config validation and the physical prompt compiler.
- Exposes remaining physical token budget to the Main LLM alongside remaining tool/turn fuses so it can prioritize decisive work without Runtime deciding semantic sufficiency.
- Reframes Investigation in the Agent contract as the Main LLM's own semantic working memory. Multi-candidate audits are instructed to create/close persistent targets instead of carrying unresolved questions only in transient reasoning.
- Skips Claim Review for Finals with zero Observation, Evidence, Investigation and WriteTransaction state. This is state-derived and does not classify the task as simple/chat.
- Clean break: config/session/queue/project-memory schemas are exact 5.5.3; earlier state is rejected, never migrated.
- Current validation: 136 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5.2 — Canonical State Ownership — 2026-08-10

- Applies the ObservationLedger ownership pattern across runtime state: one factual responsibility, one canonical owner, all histories/counters/views derived.
- Adds canonical `DecisionLedger`; deletes parallel persisted decision history and repeated-rejection counters.
- Adds run-scoped `ExecutionContext` with canonical LLMCallLedger; logical prompt metadata and provider attempts now live in the same record.
- Deletes `prompt_snapshots`, separate `llm_responses`, `correlate_prompt_attempts`, logical-call sequence state and mutable `_runtime_agent_budget` hidden in configuration.
- Makes `runtime/history.py` project the canonical `ExecutionTrace` instead of reconstructing the job independently; Prompt Accounting reads LLMCallLedger directly.
- Adds canonical `EvidenceLedger` for Evidence registration, persistence, rehydration, freshness and indexing; Agent no longer owns Evidence lifecycle helpers.
- Evolves `ObservationLedger` into the single owner of physical tool events, replay/coverage identity, pending model-facing results and public tool history. Hot source/replay bodies are not serialized.
- Adds canonical `WriteTransaction`; patches, attempts, validation, failure and rollback live once in the Session. Confirmation pending state stores only `transaction_id` and canonical Session state.
- Stops persisting derived Claim summaries.
- Removes arbitrary fixed item-count truncation from Observation/Decision ledgers.
- Clean break: config/session/queue/project-memory schemas are exact 5.5.2; 5.5.1 state is rejected, not migrated.
- Current validation: 131 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5.1 — Second Deep Cut — 2026-08-09

- Deletes `workspace_scope` end-to-end; physical workspace use is observable from actual tools and patches rather than self-classified by the Main LLM.
- Deletes `final.evidence_ids` / `answer_evidence_ids`; Investigation owns target Evidence and the global Claim pass audits Runtime Evidence.
- Deletes the lexical `request_policy` and parallel Claim `findings[]` subsystem; material delivery remains a semantic Claim responsibility.
- Deletes generic `AGENT_NO_PROGRESS` and physical-state progress fingerprints; only deterministic repeated rejected decisions and replay-only loops are fused.
- Consolidates observation identity, file-range coverage and replay in `ObservationLedger`; removes `relevant_sources`, `visible_source_ranges`, persisted Claim feedback copies and tool-history navigation state.
- Deletes duplicate post-write tool reread; deterministic full-output verification remains the canonical post-apply verification path.
- Makes `memory_search` truly read-only and keeps writes in the canonical `memory_store` path.
- Deletes public `read_range`; `read_file(path, line_start?, line_end?)` is the single file-read ABI.
- Consolidates tool metadata into the executable `TOOLS` registry; removes parallel Agent tool-class sets, `_TOOL_CONTRACTS`, duplicate `name`, `permission`, `output_schema`, and alternate-registry injection.
- Removes Claim/Gaps recovery identities (`claim.id`, `claim.kind`, `semantic_gap.id`, signatures); atomic review records contain only coordinates still used by the global verifier.
- Requires strict JSON Schema for Agent/Claim structured calls; deletes `llm/capabilities.py`, capability cache/negotiation/revalidation, `json_object`/prompt downgrade and structural repair retries.
- Deletes automatic retry after `finish_reason=length`; truncation now fails explicitly as `MODEL_OUTPUT_TRUNCATED` instead of re-running the same inference with a larger ceiling.
- Consolidates transient backend retry policy to one `retry_max_attempts`; removes the Agent-specific transport retry override.
- Deletes dead capability-administration telemetry/history and the frontend `progress_history` block left from Progress Earned Authority.
- Keeps one public task deadline; worker hard-kill is derived with a fixed technical grace instead of exposing a second deadline knob.
- Removes artificial chat/working-set/item-count context caps; actual model window + safety margin is the physical context authority.
- Enforces strict nested configuration fields and rejects every removed key as an error rather than aliasing it.
- Current validation: 125 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5 — Semantic Authority Reset / Clean Break — 2026-08-09

- Restores the Main LLM as the sole creator of semantic debt: `Investigation=[]` is valid and workspace read/write never implies Investigation.
- Deletes `INVESTIGATION_REQUIRED`, lexical semantic routing, the semantic phase scheduler and Progress Earned Authority; tool/turn/token/deadline limits are physical fuses only.
- Keeps declared Investigation mechanically strict: identity/goal durability, real Evidence bindings, `established` proof requirements and open-target Final blocking remain Runtime invariants.
- Collapses Claim handling to one global semantic review path; `target_id=null` reports omitted debt back to the Main LLM and never creates a Runtime target.
- Enforces one canonical Final object and deletes Final-string compatibility, old Investigation snapshot APIs, specialized Claim recovery protocols and index-based prompt/response correlation fallback.
- Makes Rev5.5 a clean break for persisted state: config, session, queue and project-memory schemas are exact 5.5 contracts; old state is rejected, never migrated.
- Deletes Rev5.2.x bridges, progress-credit backfills, write-only session fields, legacy tool aliases, dead write schemas, dead capability accessors and other confirmed no-caller code.
- Removes historical revision tests that preserved obsolete APIs and replaces them with current architectural invariants.
- Renames prompt navigation state from `investigation_map` to `observation_map` so observation history is not semantically coupled to Investigation.
- Standardizes the active internal observation/tool record ABI on one English vocabulary instead of maintaining PT/EN adapter paths.
- Current validation: 124 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.4 — Grounding Unification — 2026-08-09

- Makes the canonical Investigation Contract the single project-grounding authority for workspace Finals.
- Removes `FINAL_PROJECT_EVIDENCE_IDS_REQUIRED`; `final.evidence_ids` are optional direct answer anchors and still reject unknown IDs when present.
- Claim packets expose `answer_evidence_ids` separately while `investigation[*].evidence_ids` remains canonical target grounding.
- Removes public `agent.final_validation_retries`; invalid Finals may only consume ordinary remaining turns.
- Preserves the concrete Final validation failure on the last normal turn instead of masking it as `MAX_LLM_TURNS_EXCEEDED`.
- Clarifies `analysis_answer_only`: close remaining targets from retained Evidence and answer without tools.
- Keeps P1/P2 context retirement, tool catalog changes and Evidence compaction out of this first Rev5.4 implementation.

## Rev5.3.4 — P0 Corrections — 2026-08-09

- Correlates every provider attempt with stable `logical_call_id`, `prompt_snapshot_id`, and `physical_attempt`, eliminating index drift after truncation retries.
- Raises only the closed-Investigation final-answer completion ceiling to 3000 tokens; task-wide completion budget is unchanged.
- Semantic follow-up pins only Evidence explicitly cited by rejected Claims/semantic gaps; reopening a target no longer repins its entire Evidence history.
- Sends full Claim rework feedback once, then a deterministic compact coordinate view on later rework turns.
- Keeps Rev5.3.3 Prompt Cost Accounting enabled so the effect can be measured directly.



## Rev5.3.3 — Prompt Cost Accounting — 2026-08-09

- Added safe prompt-cost accounting over the existing `prompt_snapshots`; no prompt/source/model bodies are exposed.
- Public history and `execution_trace` now expose per-call component sizes plus job aggregates for fixed repeated contract tax, fresh tool-result cost, retained context and Evidence/Investigation state.
- Added provider-vs-local prompt estimate ratios so accounting drift is distinguishable from context bloat.
- Claim verifier prompt snapshots now include component sizes and packet measurements: selected Evidence count, Evidence excerpt width, answer-anchor count and Investigation target count.
- Added observational diagnostics for Evidence amplification, replay-request rate and structurally unreferenced Evidence/tool actions. These counters are explicitly non-semantic and never classify work as wasted.
- Added a History UI section for prompt accounting.
- No context retirement, prompt cropping policy, tool catalog, Investigation, Claim semantics, authority, budgets, public tools or config schema changed.

## Rev5.3.2 — Answer Consistency Gate — 2026-08-09

- Added mandatory `answer_consistency={status,reason}` to the `claim_verifier` structured contract, with `status=consistent|conflict`.
- `answer_consistency=conflict` now blocks provisional Final acceptance even when material delivery is satisfied and every individual Claim is supported.
- Consistency-only debt returns to the Main Agent as answer rework; it does not reopen Investigation or pin new Evidence by itself.
- Added Decision-Ledger fingerprinting and follow-up feedback for visible answer conflicts so repeated unchanged inconsistency cannot spend an unbounded Agent↔Claim loop.
- Targeted Claim/Semantic Gap/Finding/workspace-scope reverifies use prescribed `satisfied` + `consistent` gates and do not rerun global delivery/consistency judgment.
- Added Rev5.3.2 regression reproducing the real benchmark contradiction: one item labeled both confirmed legacy and active, followed by a zero-legacy conclusion; correction succeeds with no extra tool call when retained Evidence already suffices.
- Directed Proof, Investigation, material delivery, tool authority, budgets, public tools and config schema remain unchanged.

## Rev5.3.1 — Material Delivery Gate — 2026-08-09

- Added mandatory Claim Verifier `material_satisfaction={status,reason}` with `status=satisfied|gap`.
- A provisional Final can no longer be accepted when the verifier says the requested material result was not actually delivered, even if every factual Claim is supported.
- Delivery-only gaps return to the Main Agent for direct Final repair without forcing new Investigation or tool use; `semantic_gaps` remain the route for omitted Evidence or missing/partial investigation.
- Material-delivery debt is included in reviewer loop fingerprinting and persisted in Claim Review history.
- Targeted verifier recovery/reverification stays local and does not rerun global delivery judgment.
- Directed Proof, Investigation, committed-progress authority, public tools, budgets, and config schema remain unchanged.
- Added Rev5.3.1 regressions reproducing the benchmark failure: supported Claims plus a capado Final are rejected, repaired, reverified, and accepted with no extra tool call.

## Rev5.3.0 — Directed Proof & Material Satisfaction — 2026-08-09

- teaches the Main Agent to identify the observable fact that would actually confirm/refute a material property before choosing tools; related Evidence and surface markers no longer count as decisive proof by default;
- separates candidate discovery from verdict: names/comments/keyword markers may nominate candidates, while conclusions require Evidence that discriminates the requested property;
- makes fresh Evidence explicitly non-equivalent to epistemic progress and encourages separate Investigation targets only for material independent verdicts;
- adds minimum-sufficient-proof discipline so the Agent stops once deeper ancestry cannot materially change the verdict;
- restores a user-facing Final contract: concrete result first, no internal Runtime/Investigation/Claim narration unless requested, and audit findings surfaced as what/where/verdict/practical reason;
- expands the existing Claim Verifier, without another LLM call or schema, to judge Claim truth, material task satisfaction and material target closure; true-but-incomplete answers now produce semantic debt;
- strengthens Claim follow-up so the Agent distinguishes answer-only debt from missing investigation and preserves the requested property instead of substituting an easier proxy;
- keeps Rev5.2.9 authority, ObservationLedger, Evidence, Investigation schema, public tools and two-brain semantic ownership intact.

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
