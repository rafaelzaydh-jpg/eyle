# Changelog

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
