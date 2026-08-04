# Changelog

All notable changes to Eyle are documented here.

## Unreleased

No unreleased changes yet.

## 2.7.3 — 2026-08-04

### Revision 55.1 — Windows PID safety hotfix

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
