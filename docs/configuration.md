# Configuration — Eyle Rev4.11.7

The default configuration exposes capacity and safety, not hidden architectures.

## LLM

- `llm.context_window_tokens`: real model window used for each isolated call;
- `llm.agent_decision_max_tokens`: normal decision/final response allowance;
- `llm.agent_patch_max_tokens`: allowance after a source read, when a patch may be generated;
- timeouts, retries, provider, model, and provider and retry settings.

## AgentSession

- `agent.max_llm_turns`;
- `agent.max_tool_calls`;
- `agent.max_identical_tool_repeats` (default `2`; a repeated identical read is blocked before a second disk access);
- `agent.protocol_parse_retries`;
- `agent.max_patch_dry_run_failures`;
- `agent.chat_history_token_budget`;
- `agent.task_context_token_budget` for the stable cross-turn request anchor;
- `agent.max_write_investigation_turns` (default `2`);
- `agent.max_no_progress_turns` (default `2`);
- `agent.max_phase_violations` (default `1`);
- read/tree limits;
- task deadline and aggregate token accounting.

Only the first turn receives the broader recent history. A smaller task anchor remains on later turns so literal constraints do not disappear. Patch output allowance is adaptive to retained source, and write tools become patch-only after the investigation budget.

## Response quality

`agent.response_quality` controls the small deterministic factual gate:

- `enabled`: activates claim validation for project/code analysis;
- `max_relevant_sources`: number of recent useful source snippets retained across turns;
- `max_relevant_source_chars`: maximum raw characters kept per retained snippet;
- `reject_mid_list_corrections`: rejects lists that retract or correct themselves midway.

When enabled, project facts, verified bugs, and contextual risks require evidence IDs created by real read tools. An explicit request such as “até 3” becomes an enforced overall claim cap. Multiple limits such as “até 3 bugs e até 5 recomendações” also create per-kind caps. Recommendations remain a distinct claim kind and may be evidence-free when they are general advice.

## Token accounting

`context_engine.cached_prompt_weight` defaults to `0.2`. Every request is still checked against the full model context window, while the task-wide aggregate exposes separate raw, cached, uncached, and effective prompt counts. Repeated identical system prompts receive the configured cache weight; provider-reported `cached_tokens` replaces the estimate when available. This fixes false aggregate exhaustion without being used as loop control.

## Writes

`codar` controls backup, test execution, sandbox, and resource limits. Write confirmation cannot be disabled by the LLM.

`codar.testes.ativado` defaults to `true`. After a confirmed write, Rev4.11.7 detects pytest files recursively (including tests created by that transaction) or an npm `test` script. A detected suite must execute successfully; refusal, timeout, or failure rolls back the whole write. Disabling tests explicitly leaves the result in partial-verification state.

Changed Python files are always checked with the real `compileall` module in a temporary copy before tests run. This check does not leave `__pycache__` files in the user workspace.

## Removed settings

Rev4.11.7 rejects or no longer uses settings for Scouts, Mission Interpreter, semantic grounding, ProjectMemory prompt budgets, evidence replay, or legacy pipelines. The new no-progress counters belong to the single AgentSession phase machine, not to a second reasoning architecture.

## Failed-write diagnostics

When a confirmed write fails during `compileall`, tests, application, or final reread, the runtime returns the real bounded diagnostic output, the affected paths, and whether rollback was confirmed. That structured report is stored as metadata on the assistant message and becomes citable runtime evidence in the next AgentSession. Restored source code is never used to pretend the failed attempt had no error.
