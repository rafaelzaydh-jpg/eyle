# Benchmark — Eyle Rev4.12.2

The benchmark remains a development tool under `eyle/devtools/`; it is not part of the agent's reasoning path.

A useful Rev4.12.2 benchmark measures the public behavior of the active `AgentSession` loop and the new observable execution record:

- request preservation and correct phase transitions;
- tool selection and fresh evidence;
- factual correctness and claim-to-evidence quality;
- explicit finding-limit compliance;
- supervised write confirmation, dry-run, hashes, atomic apply, tests, rollback, and reread;
- false-success rate;
- logical LLM calls, backend requests, raw/cached/new/effective tokens, and latency;
- common multi-file writes completing within the phase budget;
- patch-only enforcement after write investigation;
- semantic blocking of overlapping reads;
- observable-history completeness without raw prompts, model responses, source bodies, or chain-of-thought;
- calculator tasks completing in two LLM calls even when the final is structured;
- focused `run_tests` output staying bounded while preserving the failing summary;
- `git_status`/`git_diff` remaining read-only and compact;
- rejected final/protocol decisions appearing in public history with a reason code.

```bash
python main.py benchmark --output context/benchmark_latest.json
```

The packaged suite uses deterministic doubles. A real Qwen benchmark must run in the deployment environment because model interpretation, tool selection, patch quality, latency, token usage, cache behavior, and JSON conformance cannot be proven offline.

Coverage and efficiency comparisons remain development commands:

```bash
python main.py compare-coverage baseline.json candidate.json
python main.py compare-efficiency baseline.json candidate.json --tolerance 0.10
```

For architecture decisions that were intentionally removed, read [`../UPDATE_HISTORY.md`](../UPDATE_HISTORY.md) before proposing a reintroduction.

## Rev4.12.2 regression targets

- tree + README + project inspection must compact below the model context budget instead of raising `PROMPT_CONTEXT_BUDGET_EXCEEDED`;
- missing pytest must return `TEST_RUNNER_UNAVAILABLE`, not `TESTS_FAILED`;
- a preflight-blocked prompt must not be counted as a provider request;
- an explicit test request should normally call `run_tests` directly and answer from that observation.
