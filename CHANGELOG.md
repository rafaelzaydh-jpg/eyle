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
- Validation: 102 tests passed; one optional Flask UI test was skipped because Flask is unavailable.

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

## 2.7.4 Rev4.5 — 2026-08-05

- Added `TargetCoverageLedger` with `required`, `essential`, and `optional` classifications.
- Added stable system-owned claim IDs and evidence-to-claim links.
- Added deterministic claim-to-rendered-segment links.
- Grounding-rejected claims are retained with reasons and importance instead of disappearing silently.
- Publication now fails when required or essential information loses evidence, a claim, or a rendered segment.
- Added manifest-backed `must_preserve` fixtures for preservation regression tests.
- Added `python main.py compare-coverage <baseline> <candidate>` for automatic release comparison.
- Benchmark reports now include preservation gates, coverage counts, and silent-discard metrics.
- Added 8 Rev4.5 regression tests; 347 tests pass in the packaging environment.
- The real Qwen Rev4.5 smoke remains deployment-only.

## 2.7.4 Rev4.4 — 2026-08-05

- Fixes the real-model failure `A conclusão final perdeu a aderência à intenção solicitada` after human-readable rendering.
- Separates `requested_outputs` into blocking `required_outputs` and diagnostic `optional_outputs`.
- Keeps `plain_language_summary` and `main_behavior` mandatory for `code_analysis`.
- Treats components, component relationships, and verified limitations as optional enrichments when fresh evidence does not support them.
- Evaluates final intent adherence on grounded structured claims, not on the prose formatting of the rendered answer.
- Exposes required and optional outputs separately in expandable task details.
- Adds regressions proving that grounding may remove one unsupported optional claim without rejecting the complete analysis, while missing main behavior remains blocking.
- Validation: 339 tests passed; one optional Flask test skipped because Flask was unavailable in the packaging environment.
- The real Qwen Rev4.4 smoke remains deployment-only.

## 2.7.4 Rev4.3 — 2026-08-05

- Changes the `code_analysis` contract from generic `analysis` output to explicit plain-language summary, main behavior, important components, component relationships, and verified limitations.
- Requires the Finalizer to explain what the software is and what it does before discussing configuration, missing tests, or audit limitations.
- Enumerates observable HTTP routes, methods, handlers, and returned values when present in fresh evidence.
- Orders grounded claims deterministically and groups them into readable paragraphs.
- Keeps audit coverage disclosure in expandable execution details instead of prepending it to the main answer.
- Adds Rev4.3 regressions for semantic ordering, intent coverage, route explanation, and separation of the main answer from audit diagnostics.
- Validation: 336 tests passed; one optional Flask test skipped because Flask was unavailable in the packaging environment.
- The real Qwen Rev4.3 response-quality smoke remains deployment-only.

## 2.7.4 Rev4.2 — 2026-08-05

- Treats structured claim annotations as the canonical grounding units instead of re-splitting rendered text and losing type, scope, or evidence IDs.
- Preserves supported structured claims when one extra claim is rejected; utility, intent, and audit coverage gates still decide whether the remaining answer is publishable.
- Makes the latest successful dry-run the canonical source for confirmed `apply_patch` arguments.
- Derives `codigo_original_esperado` from fresh evidence instead of trusting a second copy generated by the model.
- Allows a genuinely empty original range only when fresh evidence and the exact dry-run fingerprint prove it.
- Adds Rev4.2 regressions for audit grounding resilience and canonical patch confirmation.
- Validation: 332 tests passed; one optional Flask test skipped because Flask was unavailable in the packaging environment.
- The real Qwen Rev4.2 smoke remains deployment-only.

## 2.7.4 — 2026-08-05

### Revision 4.1 — Intent routing and write-verification fixes

- Fixed a real routing bug where natural-language words such as `criação` matched the broad write radical `cri...` and sent explanation requests into `project_write`.
- Restricted symbol extraction so natural-language phrases such as “criação e inicialização” are not treated as code symbols while concise code lists such as `tocar` and `limitar_volume` remain supported.
- Kept whole-project requests such as “analyze the project and give exactly 5 improvements” in the full project-audit pipeline even when the internal response mode is `suggest`.
- Changed analysis-plus-improvements contracts to require `analysis + recommendations`; a `problems` output is required only when the user explicitly asks for bugs, risks, failures, or critical review.
- Added exact recommendation instructions to both project-read and project-audit Finalizers and preserved deterministic count validation.
- Added explicit compound-write outputs for analysis, problem selection, implementation, verification, final state, and explanation without creating a second agent.
- Added structured tool-failure diagnostics with tool name, error code, bounded detail, and retryable/terminal policy; terminal errors now stop immediately instead of consuming three blind retries.
- Added model-facing repair instructions after a failed tool call so the same invalid call is not repeated unchanged.
- Changed “no test suite available” from fatal failure to successful application with `unverified_no_suite` / partial verification after a fresh post-write reread.
- Changed waiting-write summaries from “validation failed” to “proposal approved; awaiting confirmation”.
- Ensured every return path exposes the current task contract and task intent in the expandable work summary.

### Validation

- 328 runtime and regression tests passed locally.
- One Flask integration test remains environment-dependent when Flask is not installed.
- Python compilation, warning-clean tests, JavaScript syntax, release identity, and archive integrity are release gates.
- The real Qwen Rev4.1 behavioral smoke test remains deployment-only.

### Revision 4 — Task intent and autonomous code-agent identity

- Added a deterministic task intent to the existing single-agent contract: `analyze`, `explain`, `review`, `suggest`, `investigate`, `discuss`, `plan`, `create`, or `edit`.
- Added response profiles and requested-output gates without creating separate agents or project-size branches.
- Made recommendations opt-in: analysis-only requests reject recommendation claims and recommendation language. Explicit counts such as “10 melhorias” are preserved and validated.
- Added `absence` claims with fresh evidence IDs and an explicit reviewed scope; inferences continue to require an observed basis.
- Made the chat identity explicitly code-only while preserving analysis and technical conversation as complete autonomous tasks that do not require editing.
- Added deterministic post-write finalization: after patch, tests, and reread are verified, Eyle renders the operation receipt without another LLM call.
- Added task-intent details to the expandable work summary.

### Validation

- 315 runtime and regression tests passed locally.
- One Flask integration test remains environment-dependent when Flask is not installed.
- The real Qwen intent benchmark remains deployment-only.

### Revision 3 — Target coverage and lean project-read finalization

- Added a minimal deterministic task contract with explicit files, symbols, relationships, full-file scope, and evidence-derived literal-value targets.
- Added the `ANSWER_TARGETS_NOT_COVERED` completion gate so a grounded but incomplete answer cannot be published as success.
- Added one directed project-read repair that receives only the missing targets, prior claims, and fresh evidence; a second recovery attempt is not allowed.
- Added a project-read fast path that invokes the Finalizer as soon as all explicit files have fresh evidence, removing the intermediate `ready_to_finalize` model call.
- Kept the new behavior explicitly enabled in the release configuration while preserving compatibility for older embedded configurations that do not declare the Rev3 flags.
- Added regression coverage for prefix literal values, cross-file target coverage, single repair, and reduced Finalizer handoff calls.

### Validation

- 307 runtime and regression tests passed locally.
- One Flask integration test remains environment-dependent when Flask is not installed.
- Python compilation and release identity validation passed.
- The real Qwen smoke benchmark must be rerun against revision 3.

### Revision 2 — Structured project reads and trusted local tests

- Changed the `project_read` Finalizer contract from free-form `answer + claim_annotations` to atomic `claims[]` with explicit `type`, `text`, `evidence_ids`, and `basis`.
- Rendered project-read answers deterministically from validated claims, keeping evidence bindings intact across multi-file explanations.
- Kept typed grounding as the blocker for unsupported anchors and missing evidence while structured claims prevent false rejection of legitimate cross-file relationships.
- Changed project-read recovery to structured deterministic claims instead of loose text recovery.
- Added the explicitly opt-in `trusted_local` test backend for Windows: allowlisted argv only, `shell=False`, temporary project snapshot, filtered environment, timeout, bounded output, and no claim of network isolation.
- Made `backend=auto` select `trusted_local` on Windows only when `sandbox.allow_trusted_local=true`.
- Added regression tests for multi-file structured claims, evidence requirements, trusted-local authorization, snapshot isolation, and real pytest execution.

### Validation

- 307 runtime and regression tests passed locally.
- The suite also passed with warnings promoted to errors.
- One Flask integration test remains environment-dependent when Flask is not installed.
- A real `pytest -q` run passed through the `trusted_local` backend in a copied workspace.
- The real Qwen benchmark must be rerun against revision 2.

### Revision 1 — Core reset: single agent

- Removed the historical `consulta`, `dicas`, `visao_geral`, and `engenharia` project pipelines.
- Removed the separate Analyst, Executor, Suggestor, Engineer, and Understander LLM wrappers and prompts.
- Removed `engine/dicas.py`, `engine/entender.py`, and the `verify/` package.
- Replaced silent legacy fallbacks with explicit agent failures.
- Reduced `engine/engine.py` to the chat/agent public entry paths.
- Made ingest deterministic; it no longer calls an LLM to describe files.
- Kept BM25 only as a search tool available to the single agent.
- Simplified the release configuration and made `full` the default rollout while retaining explicit write confirmation.
- Added cross-platform atomic writes that do not depend on `os.fchmod`.
- Added regression tests proving that legacy modules, prompt builders, and routing paths are absent.

### Validation

- 297 runtime and regression tests passed locally.
- One Flask integration test remains environment-dependent when Flask is not installed.
- The real Qwen benchmark must be rerun against the deployment endpoint.

All notable changes to Eyle are documented here.

## Unreleased

## 2.7.3 — 2026-08-05

### Revision 55.22 — Project-read orchestration and benchmark truth

- Preserved provider completion metadata including `finish_reason`, configured/resolved model, prompt/completion/reasoning token usage, response ID, and per-call latency.
- Retried one token-limit truncation with a larger bounded output budget and failed with `MODEL_OUTPUT_TRUNCATED` if the provider still returned a truncated completion.
- Split `project_read` into evidence collection and a dedicated tool-free Finalizer with its own output budget.
- Added `ready_to_finalize` so the planning agent hands off without spending its small decision budget drafting a disposable answer.
- Routed exact symbol-existence questions deterministically to `find_symbol`, producing structured negative evidence instead of treating BM25 relevance as proof of absence.
- Converted the known post-write sequence into deterministic transitions: confirmed patch, tests, fresh reread, then finalization.
- Upgraded the benchmark report to separate factual correctness, completion, semantic grounding, workflow, and safety; it now records the resolved model, finish reasons, LLM calls, and latency per actual LLM call.
- Fixed edit-case continuation and legacy benchmark compatibility so old reports remain readable without silently satisfying the new fields.

### Validation

- `python -m compileall -q .` passed.
- JavaScript syntax checks passed.
- 362/362 executable tests passed, including `pytest -q -W error`.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.
- The real Qwen 3.8 MAX benchmark must be rerun against revision 55.22 in the deployment environment.

### Revision 55.21 — Audit truthfulness hardening

- Rejected global project-health claims even when targeted coverage and a successful test run exist; only explicitly scoped findings about reviewed components may proceed.
- Unified test status around the latest executed `run_tests` action so an older passing run cannot mask a later failure.
- Replaced legacy text recovery for `project_audit` with deterministic structured `claims[]` recovery and revalidation.
- Fixed English project-audit routing and coverage-language detection.
- Restricted critical-component metrics to files classified with critical catalog roles.
- Updated active technical and benchmark documentation so historical revision 53 results are not presented as current.
- Closed direct SQLite connections in tests and validated the suite with warnings promoted to errors.

### Validation

- `python -m compileall -q .` passed.
- `node --check web/static/app.js` passed.
- 353/353 executable tests passed, including `pytest -q -W error`.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.
- The real Qwen 3.8 MAX endpoint was not available in the packaging environment.

### Revision 55.20 — Real audit coverage and honest disclosure

- Added a public `coverage` record with inventory completeness, total/read source files, total/read critical components, current test execution, documents used by final claims, and `none`/`partial`/`targeted`/`complete` level.
- Derived critical components from deterministic catalog slots and Scout selections instead of model self-reporting.
- Counted `docs_used` only from final selected evidence IDs and test execution only from an actual `run_tests` action in the current task.
- Added a deterministic coverage disclosure after grounding, including reviewed components, source-file reach, test execution status, and the explicit limitation that universal bug absence cannot be claimed.
- Allowed old release notes to be cited as historical records only when explicitly attributed to fresh documentation evidence; historical counts no longer become current operational status.
- Added the required regression matrix for large trees, documentation-only reads, insufficient single-file coverage, health claims, unverified test counts, historical releases, and complete small projects.

### Validation

- `python -m compileall -q .` passed.
- `node --check web/static/app.js` passed.
- 345/345 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.
- The real Qwen 3.8 MAX endpoint was not available in the packaging environment.

### Revision 55.19 — Structured claims, health gates, and indexed-memory trust

- Replaced the `project_audit` Finalizer contract `answer + claim_annotations` with atomic `claims[]` containing `type`, `text`, `evidence_ids`, and `basis`.
- Added deterministic response rendering from validated claims, so the final text cannot drift away from its evidence annotations.
- Added `TEST_STATUS_NOT_VERIFIED` when a claim says tests pass without a successful executed `run_tests` action in the current task.
- Added `UNSUPPORTED_HEALTH_CLAIM` for global statements such as “no critical issues” or “all functionality is operational” without complete configured audit coverage and current operational proof.
- Marked `memory/entendimento.json` as `UNTRUSTED NAVIGATION HINT`; entries only receive `HASH_VERIFIED_NAVIGATION_FACT` when the persisted file hash still matches disk, while audit conclusions still require fresh Evidence Registry IDs.
- Added schema/config support for `agent.audit_health_claim_required_score`.

### Validation

- `python -m compileall -q .` passed.
- `node --check web/static/app.js` passed.
- 336/336 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.
- The real Qwen 3.8 MAX endpoint was not available in the packaging environment.

## 2.7.3 — 2026-08-04

### Revision 55.18 — Deterministic audit Scout and Finalizer

- Added a deterministic role-based candidate catalog after `list_tree`, covering entrypoints, orchestrators, state/persistence, grounding/recovery/validation, correlated tests, and principal configuration.
- Restricted Scout selections to real catalog paths, rejected invented paths, and preserved system-required baseline slots even when the model returns an empty or incomplete plan.
- Split `project_audit` into inventory, initial Scout, automatic reads, fresh-code gap review, optional gap coverage, dedicated Finalizer, and the existing grounding/coverage gates.
- Removed tools from the Finalizer prompt and rejected any Finalizer response that attempts another tool call.
- Persisted the audit pipeline through checkpoints and exposed the public phase, selected paths, completed/failed reads, and Finalizer call count in the work summary.
- Kept the generic monolithic Agent for other task types while preventing it from planning and concluding a general project audit in one call.

### Validation

- `python -m compileall -q .` passed.
- `node --check web/static/app.js` passed.
- 328/328 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.
- The real Qwen 3.8 MAX endpoint was not available in the packaging environment.

### Revision 55.17 — Minimum project-audit coverage

- Added the dedicated `project_audit` task type for general project analysis.
- Required deterministic coverage of inventory, entrypoint, core logic, error paths, tests or test configuration, coverage reporting, and grounded conclusion.
- Prevented README, CHANGELOG, and `docs/**` from satisfying fresh source-code evidence.
- Added `SOURCE_CODE_NOT_ANALYZED` and `PROJECT_AUDIT_COVERAGE_INCOMPLETE` fail-closed results and blocked legacy fallback bypasses.

### Validation

- `python -m compileall -q .` passed.
- `node --check web/static/app.js` passed.
- 322/322 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.16 — Full project inventory

- Preserved every entry returned by `list_tree` in a dedicated structured `project_inventory`, outside the 500-character recent-observation summary.
- Included the full preserved inventory in every subsequent Agent decision, so central folders such as `engine/`, `llm/`, and `tests/` cannot disappear from context.
- Added deterministic inventory hashes, file/directory totals, root entries, extension counts, ignore counts, and explicit complete/partial coverage metadata.
- Persisted the inventory through task checkpoints without duplicating the raw tree in compact action logs.
- Marked truncated inventories as partial and instructed the model not to claim that unlisted files do not exist.
- Added regression coverage with a 143-entry tree, prompt verification, checkpoint round-trip, partial-coverage warnings, and deterministic reader metadata.

### Validation

- `python -m compileall -q .` passed.
- `node --check web/static/app.js` passed.
- 314/314 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.15 — Evidence integrity and job identity

- Required observed fresh evidence before any project task can pass the utility gate.
- Added typed grounding to legacy consultation, suggestion, overview, and read-fallback paths.
- Added structured `PROJECT_NOT_READ`, `UNGROUNDED_PROJECT_ANALYSIS`, and `REQUEST_CONTEXT_MISMATCH` failures.
- Fixed Worker handling of `agente_status=failed`.
- Added persistent SQLite queue identity and browser invalidation of stale session jobs.
- Prevented a newly submitted job from inheriting status or summaries from an old numeric job ID.
- Registered executed symbol misses as negative evidence.

### Validation

- `python -m compileall -q .` passed.
- `node --check web/static/app.js` passed.
- 309/309 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.14 — Unified Response Recovery Pipeline

- Added one server-response adapter for `content`, `reasoning_content`, streaming chunks, partial JSON envelopes, and plain text. Truly empty payloads now raise `EMPTY_MODEL_RESPONSE`.
- Split generation, utility validation, typed grounding, selective repair, final utility validation, and publication into explicit stages.
- Added a deterministic utility gate that rejects file lists, line ranges, evidence receipts, and other conclusion-free outputs.
- Added layered recovery: unstructured retry, short evidence-only generation, deterministic code analysis, then `failed` if no useful answer survives.
- Grounding now runs only after useful content exists and repairs only rejected claims while preserving valid recommendations, decisions, hypotheses, and inferences.
- Added a canonical `EvidenceRegistry` shared by reading, analysis, grounding, conclusion metadata, persistence, and the public work summary.
- Rejected persisted legacy `completed` tasks whose saved response fails the new utility gate, reopening them through the current pipeline.
- Aligned terminal states: recoverable issues retry internally; technical failures are `failed`; genuine user decisions remain `needs_user`; only useful grounded answers are `success`.

### Validation

- `python -m compileall -q .` passed.
- `node --check web/static/app.js` passed.
- 302/302 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.13 — typed grounding autonomy

- Replaced binary semantic grounding with typed claim handling for facts, inferences, hypotheses, decisions, and recommendations.
- Kept objective facts tied to fresh evidence while allowing the Agent to introduce justified deductions, testable possibilities, technical choices, new values, files, and designs.
- Added optional `claim_annotations` to project finals, including claim type, evidence IDs, and a compact basis.
- Preserved valid non-factual reasoning during automatic fallback instead of deleting every statement that was not literally present in the project.
- Kept one behavior for projects of every size: project size changes evidence collection, not the autonomy policy.

### Validation

- `python -m compileall -q .` passed.
- `node --check web/static/app.js` passed.
- 294/294 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.11 — cancellation-safe messages and structured reasoning fallback

- Message deletion now cancels only the job created by that message. If a different message is already frozen in an active job context, its deletion is deferred until that response ends; new jobs exclude it immediately. Question jobs from the web panel run in a terminable child process so cancellation interrupts blocking local-LLM calls, and any late assistant response from the cancelled job is purged.
- Disabled response streaming for structured Agent decisions (`forcar_json=True`) so private `reasoning_content` is never published as progress and the complete non-streaming response can be recovered safely.
- Preserved the original structured-call intent when an OpenAI-compatible backend rejects `response_format`. The prompt-only JSON fallback now reads a decision stored only in `reasoning_content` even though the native JSON option is disabled.
- Kept `reasoning_content` private for ordinary textual calls; it is recovered only for structured Agent requests.

### Validation

- `python -m compileall -q .` passed.
- `node --check web/static/app.js` passed.
- 279/279 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.10 — expandable operational work summary

- Added a compact `Trabalho concluído em XmYYs` row for completed question jobs.
- Added four expandable stages: understanding, reading, analysis, and conclusion.
- Derived files, line ranges, full-read status, tools, evidence IDs, fallback, validation, and limitations from real structured metadata.
- Persisted the summary with the job and exposed only a length-limited sanitized schema through `GET /jobs/<id>`.
- Kept prompts, chain-of-thought, raw tool results, and source-code contents private.
- Added equivalent read metadata to the small-project full-code fallback.

### Validation

- `python -m compileall -q .` passed.
- `node --check web/static/app.js` passed.
- 272/272 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.9 — bounded polling and real small-project analysis

- Limited active browser polling to two jobs per cycle and 1.2 seconds between cycles.
- Added global `Retry-After` backoff and prevented overlapping status requests.
- Revalidated static assets so an old aggressive `app.js` does not survive an update.
- Added complete fresh-code context for small projects within configurable file, line, and character limits.
- Fixed the fallback response that previously reported only filenames and line counts.

### Revision 55.8 — tolerant local-model decisions and guaranteed read fallback

- Normalized common local-model JSON envelopes such as `tool_calls`, `tool_call`, `action`, `action_input`, and `answer` into the Agent protocol without accepting ambiguous mixed branches.
- Fixed read-only project analysis leaking the terminal “formato invalido” message when `fallback_cause` was missing or overwritten. The Engine now recognizes the failure through redundant status, code, and text signals.
- Kept edit requests fail-closed; no write task is converted into a textual fallback.
- Routed short project-panel requests such as “Faça a análise” to project analysis instead of generic chat.
- Added regression coverage for the exact reported conversation and nested final-object parsing.

### Validation

- `python -m compileall -q engine llm web tests` passed.
- `node --check web/static/app.js` passed.
- 263/263 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.7 — live progress and stale-job cleanup

- Added safe live progress for web jobs: current stage, active tool, elapsed time, estimated generated tokens, and tokens per second.
- Added streaming of user-visible textual responses for Ollama and OpenAI-compatible backends. Internal reasoning fields and structured Agent decisions remain private.
- Persisted progress in the queue so the browser keeps showing the latest real activity even if polling pauses briefly.
- Fixed terminal jobs cached in `sessionStorage` reappearing after reload as “job #N failed” without a new request. Only pending/processing jobs survive a page reload.
- Kept the existing read-only fallback for timeout and invalid structured output.

### Validation

- `python -m compileall -q engine llm web tests` passed.
- `node --check web/static/app.js` passed.
- 254/254 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.6 — read fallback after structured-agent failure

- Added `llm.agent_retry_max_attempts=1`, preventing transport retries from nesting inside the Agent's own format-repair retry and consuming the whole task deadline.
- Added a read-only fallback to the legacy `consulta`, `dicas`, or `visao_geral` pipeline when the structured Agent times out or exhausts invalid-JSON attempts.
- Kept edit requests fail-closed: the fallback never converts a write request into an unsupervised action.
- Preserved the specific `invalid_agent_json` failure cause in the durable task checkpoint.
- Cleaned runtime SQLite databases, traces, caches, and checkpoints from the release package.

### Validation

- `python -m compileall -q engine llm web tests` passed.
- 248/248 executable tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.5 — agent reliability and ordered delivery

- Capped effective Worker consumers by `llm.max_concurrent_requests`, preventing an older local-LLM job from outliving a newer chat job and surfacing its failure later.
- Associated browser failure notices with the originating user message through safe job metadata.
- Required three complete observable-state repetitions before cycle protection pauses an agent task.
- Made the decision schema exclusive with `oneOf`; tool decisions must include `arguments`.
- Selected the last structurally valid JSON decision when a non-schema backend emits a draft followed by a correction.
- Reduced format recovery to one retry in the shipped configuration.

### Validation

- `python -m compileall -q .` passed.
- 243/243 executable non-web tests passed.
- One Flask-dependent module remained skipped because Flask was unavailable in the packaging environment.
- `node --check web/static/app.js` passed.

### Revision 55.4 — agent deadline and retry budget

- General project analysis now executes the mandatory initial `list_tree` transition directly from `GoalState`, without spending an LLM call to rediscover a system-enforced action. The same rule applies to projects of every size.
- Added `llm.agent_max_tokens` so structured agent decisions no longer inherit the larger general-response token ceiling.
- Read timeouts no longer retry an entire local generation by default in the shipped configuration; refused connections and transient HTTP failures still retry.
- Increased the shared task deadline to allow several bounded local-agent decisions on slower CPU hardware, while each agent call remains capped independently.
- Added regression coverage for deterministic first transitions, per-profile token limits, timeout retry policy, and the complete `list_tree -> read_file -> final` analysis path.

### Validation

- `python -m compileall -q .` passed.
- 237/237 executable non-web tests passed.
- One Flask-dependent web module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.3 — local LLM response timeout

- Fixed `urllib` using the 5-second connection timeout while waiting for non-streaming llama-server response headers.
- The effective HTTP operation now waits for `read_timeout_seconds` during model generation, preventing the client from cancelling valid slow responses.
- Changed the shipped backend URL from `localhost` to `127.0.0.1` to match the IPv4 llama-server bind address and avoid loopback resolution ambiguity on Windows.
- Added a delayed local OpenAI-compatible regression server proving generation may exceed the connection timeout without being cancelled.

### Validation

- `python -m compileall -q .` passed.
- 231/231 executable non-web tests passed.
- One Flask-dependent web module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55.2 — web response delivery

- Fixed the Worker treating structured LLM failures as successful completed jobs.
- Persisted safe structured failure metadata while keeping transport errors out of assistant conversation history.
- Exposed a redacted failure message and error code through `GET /jobs/<id>` without exposing job payloads or full internal results.
- Added a visible browser failure notice instead of leaving the user message unanswered.
- Kept `/conversa` polling alive when `/jobs` polling fails temporarily.
- Fixed stale rendered message IDs after deletion, which could hide a later message that reused the same numeric ID.
- Added a startup backend preflight for `/v1/models` or Ollama `/api/tags`, clearly warning when the LLM server is offline.
- Corrected the shipped provider label to `openai_compatible` for the default port-8080 backend.

### Validation

- `python -m compileall -q .` passed.
- 229/229 executable non-web tests passed.
- One Flask-dependent web module remained skipped because Flask was unavailable in the packaging environment.
- `node --check web/static/app.js` passed.

### Revision 55.1 — Windows PID safety

- Replaced direct `os.kill(pid, 0)` liveness checks in the queue and process limiter with a shared cross-platform probe.
- Added a self-PID fast path so `/status` and limiter cleanup cannot signal the Eyle process itself.
- Used read-only `OpenProcess`/`WaitForSingleObject` checks on Windows; no signal is sent to observed processes.
- Treated legacy timezone-free timestamps as UTC and invalid heartbeats as interrupted jobs eligible for recovery.
- Prevented out-of-range PIDs from crashing POSIX health checks.
- Added regression tests covering the Windows branch, `/status`, limiter cleanup, malformed timestamps, and invalid PIDs.

### Validation

- `python -m compileall -q .` passed.
- 225/225 executable non-web tests passed.
- One Flask-dependent web module remained skipped because Flask was unavailable in the packaging environment.

### Revision 55 — inverted retrieval and parallel ingest phase 2

- Replaced dense BM25 scoring with an inverted index that visits only documents containing each query term.
- Replaced full-result sorting with an exact heap-based Top-K selector that preserves token-budget behavior.
- Added a bounded 256-entry in-memory LRU for lexically equivalent retrieval queries.
- Invalidated retrieval-query cache entries whenever `chunks.jsonl` changes.
- Kept related history fresh on cache hits instead of caching `historico.json` results.
- Parallelized safe file reads, secret checks, content hashes, AST/symbol extraction, and chunk generation during ingest.
- Preserved deterministic file/chunk ordering across serial and parallel ingest modes.
- Added revision 55 regression coverage and configuration validation.

### Validation

- `python -m compileall -q .` passed.
- 218/218 executable non-web tests passed.
- One Flask-dependent web module remained skipped because Flask was unavailable in the packaging environment.
- A synthetic 20,000-document rare-term retrieval check confirmed that scoring touches the 20 postings instead of scanning all 20,000 documents; real project gains remain workload-dependent.

### Revision 54 — token UX and aggressive LLM cache phase 1

- Added a bounded 2,048-entry in-memory LRU before the persistent cache.
- Expanded the default persistent SQLite cache to 4,096 exact entries.
- Added an absolute 24-hour TTL configurable through `llm.cache.max_age_hours`.
- Kept cache keys isolated by backend fingerprint, model, temperature, prompts, and structured-call mode.
- Preserved poisoned-cache rejection and post-budget publication gates.
- Rewrote the browser token prompt to identify the terminal line and `context/web_api_token.txt`.
- Added a visible token retry/replacement button that does not require page reload.
- Printed the persistent token path from both `main.py serve` and direct `web/routes.py` startup.
- Made direct `web/routes.py` startup launch the persistent Worker too.
- Added revision 54 regression coverage.

### Validation

- `python -m compileall -q .` passed.
- 211/211 executable non-web tests passed.
- One Flask-dependent web module remained skipped because Flask was unavailable in the packaging environment.
- JavaScript syntax validation passed with Node.js.

### Revision 53 — speed and cycle hardening

- Rejected ambiguous agent responses containing more than one valid decision JSON.
- Invalidated empty, legacy error, and structured failure cache entries.
- Published LLM responses to cache only after generated-token budget validation.
- Added short-cycle detection based on material state, tool result, evidence, edits, tests, blockers, and gaps.
- Bounded queue reservation under permanent row-conflict conditions.
- Reused identical retrieval inside the analyst cycle and added early exits for repeated gaps or directed searches.
- Added capped exponential backoff, jitter, and deadline awareness to executor retries rejected by Verify.
- Added telemetry for web-token permission failures that were previously silent.
- Added revision 53 regression tests and release documentation.
- Rebuilt the package with the same GitHub-ready structure used by the earlier public release.

### Validation

- `python -m compileall -q .` passed.
- 204/204 executable non-web tests passed in the packaging environment.
- One web test module was skipped because Flask was not installed there.
- Real-model latency and quality still require validation on the final local endpoint.

## 2.7.2 — 2026-08-04

### Revision 52 — audit closure

- Added deterministic claim-to-evidence grounding and unsupported-anchor blocking.
- Isolated jobs in terminable child processes with wall-clock watchdogs.
- Added parallel queue consumers and head-of-line blocking diagnostics.
- Centralized LLM call/token budgets and cross-process rate limiting.
- Migrated the LLM cache to SQLite with legacy JSON migration.
- Added persistent telemetry with P50/P95/P99 summaries.
- Made legacy fallbacks and configuration warnings observable.
- Added regression coverage for timeout, reset, 429, watchdog, cache, recovery, grounding, and queue behavior.

### Validation

- 193/193 executable tests passed; one web module was skipped in the packaging environment.

## 2.7.1 — 2026-08-04

### Revision 51 — reliability hardening

- Separated connection, read, agent, executor, and model-discovery timeouts.
- Added transient-only retries with exponential backoff, jitter, and `Retry-After` support.
- Added backend/model concurrency limiting and cooldown.
- Added positive and negative model-discovery caching with diagnostics.
- Added shared task deadlines and generated-token/call budgets.
- Hardened agent parsing, semantic repeat detection, worker heartbeat, queue reservation, BM25 reuse, and secret scanning.
- Added explicit release identity verification.

### Validation

- 179/179 non-web tests passed in the packaging environment.

## 2.7.0 — 2026-08-04

### Supervised-agent release line

- Consolidated the guarded agent workflow, fresh evidence, hashes, dry run, confirmation, atomic writes, tests, final reread, and rollback.
- Added professional bilingual GitHub documentation and repository metadata.
- Established LFM2.5-8B-A1B as the recommended supervised-agent target.

## 2.6.1 — 2026-08-03

### Agent safety and reliability

- Added fresh file/range evidence with canonical hashes.
- Isolated project indexes and internal traces from write detection.
- Added deterministic negative results for missing symbols.
- Added stale-patch reread and reconfirmation flow.
- Preserved explicit confirmation, dry run, atomic application, tests, final reread, and rollback gates.
- Moved internal agent instructions and structured output to English while preserving the user language.
