## 2.7.4 Rev4.12.2 — 2026-08-07

- Generalized prompt compaction for large nested structured tool results (`list_tree`, `inspect_project` and future schemas) instead of cropping only named raw-source fields.
- Prompt compaction now works on a deep-copied view, preserving the complete session evidence/tool result for later turns and history.
- Added `TEST_RUNNER_UNAVAILABLE` so a missing pytest/npm runner is no longer mislabeled as `TESTS_FAILED`.
- Promoted pytest to a runtime dependency because `run_tests` is an official runtime capability.
- Explicit test requests now guide the model to call `run_tests` directly; after a test observation, read-only analysis closes tools and asks the LLM to explain the result.
- Expandable history now distinguishes logical LLM attempts, provider requests actually sent, and prompts blocked locally during preflight.
- Added Rev4.12.2 regression coverage for the real large-project context overflow and missing-pytest case.
- Replaced the temporary license placeholder with the Eyle Personal Use License: private personal non-commercial use is allowed, while redistribution, sale, sublicensing, commercial use, publication of modified copies, and hosted-service use remain restricted without written permission.
- Added explicit contributor licensing terms and aligned README, publishing guidance, security documentation, and `UPDATE_HISTORY.md` with the source-available licensing decision.

## 2.7.4 Rev4.12.1 — 2026-08-07

### Runtime tools and decision observability

- Promoted deterministic calculator results to citable runtime evidence so structured arithmetic finals normally finish in two LLM calls instead of triggering an avoidable validation retry.
- Kept the LLM as the author of every user-facing utility response; the runtime never replaces the Eyle's tone/explanation with a hard-coded calculator answer.
- Added observable per-turn decision outcomes (`tool`, `final`, accepted/rejected) and bounded rejection codes to the expandable history without exposing chain-of-thought or raw model content.
- Upgraded `run_tests` into a first-class investigation tool with optional safe pytest scope, bounded output tail, concise summary, and evidence support for executed failures.
- Added read-only `git_status` and bounded read-only `git_diff` tools.
- Recognized plural test requests (`testes`/`tests`) as project tasks so test execution is not accidentally routed through tool-free chat.
- Added seven targeted regressions for two-call calculator completion, decision-history rejection diagnostics, focused pytest, failed-test evidence, Git inspection, and tool availability.
- Validation: 157 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.

## 2.7.4 Rev4.12 — 2026-08-06

### Observable execution history

- Added an on-demand expandable `histórico` panel for every persisted job in the web UI.
- Added `GET /jobs/<id>/history` with a sanitized public runtime history instead of expanding the normal polling payload.
- Exposed objective runtime facts: agent phase/turns, LLM call count, total/cached/new/effective tokens, safe tool arguments/results, post-write validation stages, failures, and rollback status.
- Kept chain-of-thought, raw prompts, raw model responses, source bodies, hashes, and stored-memory bodies out of the public history.
- Added bounded observable tool traces and per-call phase metadata to `AgentSession` persistence.
- Added structured write-validation history for apply, `compileall`, detected tests, tool reread, full reread, and rollback.
- Reworked GitHub documentation around the active single-agent architecture and observable execution model.
- Added `UPDATE_HISTORY.md`, a recoverable architecture-decision history documenting removed approaches, why they failed, and the evidence required before reintroducing them.

### Validation

- 150 tests passed; 1 optional Flask test skipped in the packaging environment.
- JavaScript syntax validation and release-identity validation are part of the release check.

## 2.7.4 Rev4.11.8 — 2026-08-06

### Added
- Added `calculate`, `project_stats`, `count_tokens`, `inspect_project`, and `agent_info` tools.
- Project measurements and structural inspections are registered as runtime evidence.
- `inspect_project` reports objective relation signals and never marks files as important.

### Fixed
- General/self questions no longer fail because of optional evidence-free structured claims.
- Greetings keep an empty tool catalog; utility tools are surfaced only when relevant.
- Token counting reports heuristic estimates honestly with `exact: false` when no exact tokenizer is installed.

### Validation
- 145 tests passed; 1 optional Flask test skipped in the packaging environment.

## 2.7.4 Rev4.11.7 — 2026-08-06

### Sentence references, safe Markdown, and directory workflow

- Replaced duplicated `claims[].text` in the preferred model protocol with compact 1-based `claims[].sentence` references.
- Kept legacy text claims compatible while resolving sentence references deterministically into the internal evidence ledger.
- Excluded Markdown headings from sentence numbering and added precise invalid/out-of-range correction feedback.
- Added safe DOM-based Markdown rendering for bold text, inline code, and fenced code blocks without injecting model HTML.
- Expanded Portuguese write-intent detection for commands such as `traga`, `embuta`, `inclua`, `centralize`, and `simplifique`.
- Treated folders/directories/templates as project-evidence anchors so follow-up questions can inspect the live workspace instead of claiming reads are disabled.
- Promoted fresh `list_tree` inventories to citable structural evidence and real progress in the phase machine.
- Documented directory deletion as transactional file deletion and pruned empty parent directories after confirmed writes; rollback recreates them when needed.
- Added eight regressions covering sentence claims, Markdown safety, broader write intent, structural evidence, folder-state questions, and confirmed empty-folder pruning.
- Validation: 136 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.

## 2.7.4 Rev4.11.6 — 2026-08-06

### Claim-to-answer alignment

- Restored the compact prompt instruction that every `claims[].text` must be copied verbatim from a sentence already present in `final.answer`.
- Added conservative deterministic alignment for harmless wording drift, replacing the internal claim text with the exact visible answer sentence before the evidence ledger is accepted.
- Preserved negation, numeric values, file paths, and code identifiers during alignment; materially different claims remain rejected.
- Added targeted validation feedback for `FINAL_CLAIM_NOT_IN_ANSWER` instead of the generic “return a corrected answer” instruction.
- Prevented a valid project analysis from failing merely because the answer and claim ledger used slightly different wording.
- Kept the fixed prompt compact at about 406 conservative tokens, still below the 450-token regression ceiling.
- Added six regressions covering successful alignment, framework mismatch, reversed polarity, no-retry completion, and precise correction feedback.
- Validation: 136 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.

## 2.7.4 Rev4.11.5 — 2026-08-06

### Loop and token control

- Added explicit write/analysis phases with phase-specific tool catalogs.
- Limited common write investigation to two turns and made the following turn patch-only.
- Blocked overlapping reads from fresh evidence and equivalent tree/search/symbol requests.
- Added consecutive no-progress handling and a stable compact task-context anchor.
- Reduced the fixed agent prompt from about 1,395 to about 371 conservative tokens.
- Added raw, cached, uncached, and effective prompt accounting with provider cache metadata support.
- Switched agent regression tests from 50k/100k artificial budgets to production 12k/6k/18k limits.
- Added regressions for three-call multi-file writes, semantic read coverage, compact prompts, and cached-token accounting.
- Validation: 122 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.

## 2.7.4 Rev4.11.4.2 — 2026-08-06

### Failed-write diagnostics hotfix

- Exposed the exact bounded validation output when a confirmed write fails during application, `compileall`, tests, or final reread.
- Added structured `write_failure` details with stage, error code, affected paths, execution state, and rollback confirmation.
- Preserved failed-write metadata on the assistant message instead of losing it when the pending transaction is cleared.
- Promoted the latest failure report to citable runtime-validation evidence in the next AgentSession.
- Prevented follow-up answers from inferring “there was no error” merely because rollback restored the previous source.
- Added regressions for a missing `render_template`, metadata persistence, and evidence-backed follow-up diagnosis.
- Validation: 116 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.

## 2.7.4 Rev4.11.4.1 — 2026-08-06

### Write-intent gate hotfix

- Fixed direct file-change requests being allowed to reach factual final validation before a patch proposal existed.
- Added conservative multilingual write-intent detection for commands such as `extraia`, `crie`, `altere`, `move`, `create`, and `implementa`.
- Rejected prose-only completion of active write requests with `FINAL_WRITE_ACTION_REQUIRED`.
- Added targeted runtime feedback that sends the model back to real reads and one transactional dry-run instead of asking it to rewrite the same unsupported final answer.
- Preserved analysis and advisory questions such as `Faça uma análise` and `Como extraio...` as non-write requests.
- Added a regression reproducing `Extraia o html para templates/amor.html`, including replacement of `routes.py` and creation of `templates/amor.html`.
- Validation: 113 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.

## 2.7.4 Rev4.11.4 — 2026-08-06

### Factual response quality

- Required real read evidence before accepting concrete project/code conclusions when response-quality validation is enabled.
- Added typed internal claims for `fact`, `bug`, `risk`, and `recommendation`, with evidence mandatory for facts, bugs, and risks.
- Added a claim-to-evidence ledger to execution details, including file, line range, file hash, and content hash.
- Enforced explicit limits such as `até 3`, `up to 5`, and `como máximo 2` as deterministic overall and per-kind maximum claim counts.
- Rejected duplicate claims, direct claim contradictions, and lists that correct or retract themselves midway.
- Retained a bounded set of recent relevant source snippets across later tool calls, deduplicated by evidence ID and cropped by prompt budget.
- Kept raw source out of pending write-confirmation state because confirmed writes resume deterministically without another LLM call.
- Added response-quality configuration validation and seven focused regression scenarios.
- Validation: 110 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.

## 2.7.4 Rev4.11.3 — 2026-08-06

### Real post-write verification

- Added a deterministic post-confirmation chain: apply → compileall → detected tests → rollback on failure → tool reread → full hash verification → conclusion.
- Executed the real `compileall` module for every changed Python file in a temporary copy, avoiding `__pycache__` artifacts in the live workspace.
- Enabled project tests by default and detected newly created pytest files recursively through `test_*.py`, `*_test.py`, and `tests.py`.
- Treated test refusal, timeout, execution failure, or non-zero results as verification failures that roll back the whole transaction.
- Reread every changed file through the workspace tool and then compared full live contents with the exact expected hash.
- Confirmed promised file creation and deletion before reporting success.
- Strengthened multi-file rollback by rereading restored files and verifying their original hashes.
- Closed an inherited `.gitignore` file-handle leak found by the warning-clean validation gate.
- Replaced false “verified by dry-run and reread” claims with explicit verified or partial-verification states.
- Validation: 103 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.
- The real Qwen smoke run remains deployment-only.

## 2.7.4 Rev4.11.2 — 2026-08-06

### Write-loop and token fix

- Added an explicit multi-file write contract for full-file replacement, creation, deletion, and range updates.
- Accepted common model output shapes such as `{path, content}` and `{path, new_code}` and normalized them before dry-run.
- Filled file/range hashes only from fresh matching evidence, preserving stale-write protection without forcing the model to copy hashes perfectly.
- Preserved the last relevant source for one dry-run correction instead of making the model reread and restart the edit.
- Limited invalid write proposals to two attempts, preventing eight-turn token spirals.
- Sent recent conversation history only on the first agent turn and made patch output limits adaptive to fresh source size.
- Reduced default task budgets to 6 LLM turns, 12 tools, 8 LLM calls, and 18k aggregate tokens.
- Added regressions for `/amor`-style full-file edits, multi-file route extraction, failed-patch correction, English patch keys, and deterministic confirmed application.
- Validation: 90 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.
- The real Qwen smoke run remains deployment-only.

## 2.7.4 Rev4.11.1 — 2026-08-06

### Complete cleanup

- Removed `llm/cache.py` and every response-cache branch/configuration. The only active LLM profile is the AgentSession decision protocol, so stale model decisions are never replayed.
- Removed the obsolete `memory/projeto.json` fallback; workspace discovery now has one source of truth.
- Moved confirmation IDs completely to the runtime and removed duplicated pending metadata from the core.
- Replaced the legacy `completion_gate`/`agente_*` result envelope with one `status`, `resposta`, `avisos`, and `details` contract.
- Removed dead persistence and telemetry functions, unused imports/constants, compatibility aliases, and a duplicated agent-config clone.
- Kept external memory on demand, optional adaptive planning, execution limits, queueing, cancellation, supervised writes, tests, rollback, and reread.
- Validation: 84 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.

## 2.7.4 Rev4.11 — 2026-08-06

### AgentSession core cleanup

- Replaced the mission-interpreter/agent split with one AgentSession and one LLM protocol.
- Removed Mission Repair, JSON Repair, MissionSpec, CoreAgentState, ProjectMemory prompt injection, evidence replay, action caches, semantic progress gates, and duplicate SQLite agent tasks.
- Removed the empty semantic router and routed every non-confirmation message through the same agent.
- Removed ingest/index code because the live core has no consumer for it; workspaces open directly.
- Added optional adaptive plans inside normal agent decisions instead of a planning pipeline.
- Added evidence-backed external memory tools that are consulted only when the agent requests them.
- Kept path safety, evidence hashes, dry-run, write confirmation, atomic and transactional writes, tests, rollback, reread, deadlines, and token telemetry.
- Reduced loop controls to maximum LLM turns, maximum tool calls, and a simple consecutive-identical-call guard.
- Removed mandatory deliverable IDs and findings schemas from ordinary final answers.
- Removed obsolete revision tests and added Rev4.11 behavior tests for natural conversation, analysis, editing, deterministic resume, external memory, and loop protection.

## 2.7.4 Rev4.10.3 — 2026-08-06

- Removed the deterministic canned greeting path. Greetings and project-independent conversation now go through the LLM Mission Interpreter, preserving natural language and user tone.
- Changed evidence retirement from “after every model response” to “after an accepted decision”. Blocked or repeated decisions no longer erase the source the LLM still needs.
- Kept the latest compact tool result available until the next decision actually advances the task.
- Added state-aware cached evidence replay: a repeated read with an unchanged live hash reactivates the existing evidence instead of rereading disk or failing immediately.
- Added live file hashes to read-action signatures, so a reread after the file changes is treated as a new observation.
- Replayed cached non-read results before declaring no progress and kept a configurable warning allowance for genuine repeated loops.
- Updated the LLM prompts to explicitly allow natural, user-aligned writing instead of fixed robotic response templates.
- Added Rev4.10.3 regressions for analysis after a repeated read, editing after a repeated read, cache reactivation, hash-aware rereads, and LLM-authored greetings.
- Validation: 122 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.

## 2.7.4 Rev4.10.2 — 2026-08-06

- Audited the complete active runtime and all web routes; retained only product adapter routes and removed compatibility modules that no longer belonged to the LLM-first ecosystem.
- Removed every `eyle/core` dependency on `engine` and `ingest`; core safety, workspace access, editing, hashing, sandbox, and retention now live inside the core boundary.
- Made normal startup open `workspace/` directly without importing ingest. Ingest remains an optional cache/index command.
- Replaced full Mission Interpreter retries with a compact mission-repair request containing only the original request, malformed response, and parser error.
- Added actual model-window-aware context compilation, minimal state-filtered tool contracts, one-copy active evidence, and role-oriented bounded workspace manifests.
- Made project memory evidence-backed and hash-verified, reduced its prompt allowance to 700 tokens, and invalidated stale facts/findings after confirmed writes.
- Fixed post-write memory refresh so pre-patch evidence cannot be reinserted after invalidation; only the mandatory post-write reread repopulates changed files.
- Removed raw source from public execution details and pending confirmation state.
- Removed dead configuration entries for legacy parse retries and unused observation counts.
- Fixed the product adapter calling the new core with the removed legacy `modo` argument and removed the leftover `carregar_estrutura()` runtime call.
- Removed the current user message from recent context when it is already present as the original request.
- Removed legacy rollout/mode metadata from the public agent adapter result.
- Added a full core/route audit at `docs/rev4102-core-audit.md`.
- Validation: 116 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.

## 2.7.4 Rev4.10.1 — 2026-08-06

- Added a real evidence lifecycle: fresh → active → consumed. Raw source is available for one model decision and then remains only as a compact hash/location reference.
- Added phase-specific context compilation. Patch generation receives the mission, acceptance criteria, exact active source, latest delta, and write tools without old project memory or unrelated observations.
- Reduced the default active evidence budget to 3,200 tokens and added a dedicated 2,200-token patch evidence budget for isolated 10k-token model windows.
- Bounded compact project memory by serialized token budget instead of item count, preventing old findings and file maps from overflowing the next mission prompt.
- Added repeated-action and no-semantic-progress detection. Identical tool calls are blocked before execution and a second repeated attempt fails with `REPEATED_ACTION_NO_PROGRESS` instead of reaching `MAX_STEPS_EXCEEDED`.
- Added explicit terminal test states: `TESTS_DISABLED` and `TESTS_NOT_FOUND`. Missions that require test execution stop after mission interpretation when the runtime cannot execute tests.
- Added local confirmation control when no pending patch exists, producing a clear response with zero LLM calls.
- Added typed finding validation (`bug`, `risk`, `maintainability`, `recommendation`) and required evidence IDs for every non-recommendation finding.
- Expanded task telemetry with consumed evidence, prompt snapshots, repeated-action warnings, no-progress counters, and prompt/completion/reasoning token breakdown.
- Validation: 103 tests passed; one optional Flask UI test was skipped because Flask is unavailable.

## 2.7.4 Rev4.10 — 2026-08-06

- Added incremental context compilation: active source evidence is sent once, older evidence becomes a compact hash index, and only the latest compact tool delta is forwarded.
- Allowed the Mission Interpreter to return the first tool decision, reducing a normal project analysis from four logical steps to two in the measured small-project flow.
- Added compact state-filtered tool catalogs and batched independent tool calls.
- Added tolerant protocol parsing and one isolated JSON-repair call instead of repeating the full project context.
- Added token-budgeted conversation history and bounded external project memory for facts, file hashes, and validated findings.
- Added transactional multi-file create/update/delete dry-run, single confirmation, apply, tests, rollback, and reread.
- Exposed reasoning tokens and detailed per-request LLM metadata.
- Updated the default isolated context window to 10,000 tokens with separate decision and patch output profiles.
- Validation: 95 tests passed; one optional Flask UI test was skipped because Flask is unavailable.
- Deterministic equivalent prompt comparison: approximately 4,303 input tokens across four calls in Rev4.9 versus 1,505 across two calls in Rev4.10.

## 2.7.4 Rev4.9 — 2026-08-06

- Replaced the active architecture with a single LLM-first programming-agent loop.
- Moved semantic mission interpretation, investigation planning, tool selection, adaptation, analysis, and patch generation to the LLM.
- Kept deterministic code only at the safety/proof boundary: tool schemas, workspace isolation, fresh hashes, confirmation, dry-run, atomic write, tests, rollback, reread, budgets, persistence, and terminal errors.
- Removed keyword intent routing, deterministic TaskContract interpretation, Scouts, specialized Finalizers, lexical grounding, structured-claim court, information-preservation ledger, layered response recovery, legacy agent state, and semantic understanding memory.
- Removed `memory/entendimento.json` generation and made project ingest optional.
- Replaced indexed BM25 search with live workspace search using ripgrep and a Python fallback.
- Moved benchmark, token comparison, release identity, and coverage comparison implementations to `eyle/devtools`.
- Replaced the public work summary with mission, investigation, evidence, validation, and conclusion details.
- Removed tests whose only purpose was to preserve deleted architectures; retained safety and behavior tests.
- Validation: 88 behavioral/safety tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.
- The real Qwen conversational and editing smoke test remains deployment-only.

## 2.7.4 Rev4.8.2 — 2026-08-06

- Added a compact `MissionSpec` to every task contract with intent, original objective, semantic deliverables, literal constraints, write permission, and optional result limit.
- Kept the mission high-level: no `operations[]`, no hidden planner, and no attempt to model arbitrary function internals before reading the code.
- Added bounded result-limit extraction for requests such as `indique 3 erros` and Portuguese/English number words, while explicitly allowing fewer findings when evidence is insufficient.
- Classified `erro` and `erros` as issue-review intent so analysis-plus-errors requests retain both requested outcomes.
- Exposed the compact mission explicitly to read and audit Finalizers and in execution details.
- Preserved literal constraints such as `não use float`, `preserve as regras atuais`, and `mantenha compatibilidade com os testes`.
- Added seven Rev4.8.2 regression tests.
- Validation: 393 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.
- Semantic mission planning, persistent project state, and findings storage remain intentionally deferred to later revisions.
- The real Qwen mission-preservation smoke remains deployment-only.

## 2.7.4 Rev4.8.1 — 2026-08-06

- Fixed project-analysis routing for the common typo `analize`.
- Classified `retire`, `retirar`, `exclua`, and related verbs as supervised project writes instead of read-only analysis.
- Added a bounded recent-route reference resolver so requests such as `Apague essa rota` reuse only the last explicit route target without injecting the full conversation into the model prompt.
- Made empty replacement text valid for `test_patch_dry_run` and `apply_patch`, enabling real code deletion with the same hash and confirmation protections as other writes.
- Added deterministic Flask route removal and simple route creation for explicit requests such as removing `/` or adding `/amor`; both paths reach dry-run and confirmation with zero LLM calls.
- Promoted an approved dry-run directly to the exact `apply_patch` action after confirmation, removing a redundant model decision.
- Prevented deterministic post-write receipts from being rejected by the generic utility gate after tests and reread already proved the result.
- Added six Rev4.8.1 regressions covering typo routing, route contracts, recent references, empty patches, route deletion, and route creation.
- Validation: 386 tests passed; one optional Flask interface test was skipped because Flask is unavailable in the packaging environment.
- The real Qwen Rev4.8.1 conversational smoke remains deployment-only.

## 2.7.4 Rev4.8 — 2026-08-05

- Restored the guided editing path for natural requests such as changing `index` without requiring the user to name the file.
- Upgraded the deterministic task contract to version 3 with requested change, concrete constraints, test obligation, and post-write reread obligation.
- Added deterministic project-wide symbol resolution before patch generation; obvious target discovery no longer consumes an LLM planning call.
- Added edit-specific failures: `EDIT_TARGET_NOT_FOUND`, `PATCH_GENERATION_FAILED`, and `PATCH_RESPONSE_INVALID`, without switching project writes into textual response recovery.
- Made terminal tool errors stop immediately in both normal and resumed execution paths.
- Gave post-write state priority so an applied patch can only proceed through tests, reread, and deterministic finalization instead of rediscovering the original symbol.
- Moved unsupported lexical anchors to a secondary policy in the shipped configuration while retaining strict validation as an explicit option.
- Added an end-to-end regression for `find_symbol -> dry-run -> confirmation -> apply -> tests -> reread -> deterministic receipt`, with zero LLM calls after confirmation.
- Validation: 380 tests passed; one optional Flask test was skipped because Flask is unavailable in the packaging environment.
- The real Qwen Rev4.8 functional and token smoke remains deployment-only.

## 2.7.4 Rev4.7.1 — 2026-08-05

- Removed the structural-preservation limit of at most three source files.
- Applied structural target extraction to every fresh fully-read Python source file, regardless of repository size or global inventory completeness.
- Kept unread and partially read files outside the ledger until complete evidence exists, without disabling guarantees for files already read.
- Replaced the legacy one-file/two-file audit threshold with the same role-based `entrypoint` and `core_logic` contract for every repository.
- Added 4 Rev4.7.1 regression tests; 373 tests pass in the packaging environment.
- The real Qwen Rev4.7.1 behavior/token smoke remains deployment-only.

## 2.7.4 Rev4.7 — 2026-08-05

- Routed whole-project analyze/read/explain/overview wording through the same deterministic `project_audit` pipeline.
- Moved blocking intent adherence after typed grounding and structured claim filtering.
- Added at most one directed audit repair when grounding removes an essential analysis output.
- Combined declared output labels with deterministic semantic coverage while preserving declared response-section ordering.
- Added AST-derived essential structural targets for small fully-read Python projects: Flask identity, routes, literal returns, environment configuration, server parameters, and entrypoint guards.
- Added rejected-claim text and grounding reasons to the expandable work summary.
- Added deterministic zero-request responses for simple greetings and removed the legacy “code must be in this conversation” behavior.
- Added 7 Rev4.7 regression tests; 369 tests pass in the packaging environment.
- The real Qwen Rev4.7 behavior/token smoke remains deployment-only.

## 2.7.4 Rev4.6 — 2026-08-05

- Removed `entendimento.json` and complete path inventories from all active model prompts.
- Replaced mandatory initial/gap audit Scouts with deterministic planning and coverage-driven reads.
- Limited ambiguous audit expansion to one compact optional call; normal audits use one Finalizer call.
- Added task-wide prompt, completion, and total-token budgets with preflight before every backend request.
- Counted compatibility fallbacks/retries as real requests and replaced token estimates with provider usage when available.
- Filtered tool schemas by task state and selected chat history by a whole-message token budget.
- Hard-disabled legacy textual LLM recovery so old configuration cannot reactivate hidden calls.
- Added benchmark token metrics and `compare-efficiency` release regression checks.
- Added 14 Rev4.6 regression tests; 362 tests pass in the packaging environment.
- The real Qwen Rev4.6 behavior/token smoke remains deployment-only.

# Changelog
