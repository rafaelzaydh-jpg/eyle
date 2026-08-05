# Proposed revision 55.22 — external integration validation

Revision 55.21 fixes every deterministic issue found in the 55.20 audit. The following work requires the final deployment environment rather than a safe offline patch.

## 1. Qwen 3.8 MAX end-to-end benchmark

**Why it cannot be completed during packaging:** the configured Qwen endpoint, credentials, latency profile, and production repository are not available in this environment. A mock proves orchestration contracts but cannot prove model selection quality.

**Implementation plan:**

1. Add a versioned benchmark corpus with small, medium, and large repositories.
2. Run at least 20 audits per corpus using the exact production endpoint and configuration.
3. Record Scout selections, missed critical files, recovery frequency, rejected claims, token usage, P50/P95/P99, and final coverage.
4. Fail release qualification when global health claims escape, source-only coverage is insufficient, or success occurs after a failed final test run.
5. Store anonymized benchmark summaries in `context/benchmark_latest.json`; never commit project source or prompts containing secrets.

## 2. Flask integration suite

**Why it cannot be completed during packaging:** Flask is not installed and external package installation is unavailable.

**Implementation plan:**

1. Install `requirements-dev.lock` in CI and the release environment.
2. Execute `tests/test_web_security.py` and the full suite with warnings as errors.
3. Exercise job creation, database-instance identity, polling, cancellation, authentication, and stale `sessionStorage` behavior through the actual Flask test client.
4. Block release when any web test is skipped.

## 3. Semantic paraphrase robustness

**Reason for postponement:** deterministic health patterns are intentionally fail-closed for known global claims, but natural language has unlimited paraphrases. Solving this only with more regexes would become fragile.

**Better solution:** add a small structured classifier stage that labels each claim as `global_health`, `scoped_health`, `test_status`, `historical_status`, or `other`, then require deterministic evidence rules for the selected class. The classifier output must itself be schema-validated and may not override the hard-coded deny patterns.
