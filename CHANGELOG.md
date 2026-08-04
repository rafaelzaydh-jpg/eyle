# Changelog

All notable changes to Eyle are documented here.

## Unreleased

No unreleased changes yet.

## 2.7.3 — 2026-08-04

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
