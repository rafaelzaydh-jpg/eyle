# Configuration — Eyle Rev4.12.2

The default configuration describes capacity, tools and executable safety. It does not enable hidden planners or alternate reasoning pipelines.

## LLM

Key settings:

- `llm.context_window_tokens` — model context window used for each request;
- `llm.agent_decision_max_tokens` — normal decision/final allowance;
- `llm.agent_patch_max_tokens` — larger allowance when a code patch is expected;
- provider/model, timeouts, retries and concurrency.

## AgentSession

- `agent.max_llm_turns`;
- `agent.max_tool_calls`;
- `agent.max_identical_tool_repeats`;
- `agent.max_patch_dry_run_failures`;
- `agent.chat_history_token_budget`;
- `agent.task_context_token_budget`;
- `agent.max_write_investigation_turns`;
- `agent.max_no_progress_turns`;
- `agent.max_phase_violations`;
- read/tree/scan limits;
- task deadline and aggregate token budgets.

The global turn limit is a final cap. Common write loops are controlled earlier by phase transitions and semantic read coverage.

## Token accounting

`context_engine.cached_prompt_weight` defaults to `0.2`.

Each individual model request is still checked against the full context window. Task-wide accounting separately tracks raw prompt tokens, provider-cached prompt tokens, uncached/new prompt tokens and effective prompt tokens. Provider cache discounts are accounting only; they do not grant more investigation turns.

## Project inspection

- `agent.max_project_scan_entries` — safe maximum entries inspected by deterministic project tools;
- `agent.max_project_scan_depth` — maximum scan depth;
- `agent.max_project_file_bytes` — largest text file measured by project-stat/token tools;
- `agent.max_inspect_relation_edges` — bounded relation edges returned by `inspect_project`;
- `agent.max_git_diff_chars` — maximum Git diff text returned to the model per `git_diff` call (default `6000`).

`count_tokens` does not claim an exact model-token count unless an exact tokenizer implementation exists. The shipped fallback reports measured characters converted by `context_engine.chars_per_token_fallback` with `exact: false`.

## Response quality

`agent.response_quality` controls the compact factual gate:

- evidence is required for concrete project facts, confirmed bugs and contextual risks;
- explicit limits such as “up to 3” are enforced;
- useful source snippets are retained within configured bounds;
- the final answer uses sentence-indexed claims internally to avoid duplicating prose.

## Writes

`codar.testes.ativado` defaults to `true`.

After confirmation, Python changes are checked by `compileall`, tests are detected and executed when applicable, and any syntax/test/reread failure rolls back the full transaction. A write without an applicable executed test suite is reported as partial verification rather than verified.

## Test and Git tools

`run_tests` is available during analysis and first-turn write investigation when tests are enabled. It can focus a safe relative path for pytest; other runners keep their configured full-suite behavior. Failed executed tests are valid runtime evidence. A missing test runner is reported separately as `TEST_RUNNER_UNAVAILABLE`; it is not mislabeled as a failing suite. Pytest is a runtime dependency because `run_tests` exposes it as an official capability.

`git_status` and `git_diff` are read-only. `git_diff` is bounded before reaching the model and should be narrowed by path when needed.

## Observable history

Rev4.12.2 adds no large prompt or model-side history feature. The expandable history is derived from already available runtime data plus a bounded sanitized tool trace.

Hard privacy rules for the public history surface:

- no chain-of-thought;
- no raw prompts;
- no raw model response bodies;
- no source-code contents;
- no evidence hashes;
- no external-memory bodies;
- no raw model decision body. Only decision type/outcome/rejection code is public.

The web client fetches job history only when the user opens it. Normal `/conversa`, `/status` and active-job polling remain compact.

## Removed settings and architectures

Settings tied to Scouts, Mission Interpreter, automatic ProjectMemory injection, legacy evidence replay, response caches and historical pipelines are not part of the active architecture. See [`UPDATE_HISTORY.md`](../UPDATE_HISTORY.md) before reintroducing any equivalent mechanism.
