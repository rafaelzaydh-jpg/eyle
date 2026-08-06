# Configuration — Eyle Rev4.11.2

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
- read/tree limits;
- task deadline and aggregate token accounting.

Only the first agent turn receives recent chat history. Patch output allowance is adaptive to the amount of fresh source instead of automatically reserving the full patch maximum on every later turn.

## Writes

`codar` controls backup, test execution, sandbox, and resource limits. Write confirmation cannot be disabled by the LLM.

## Removed settings

Rev4.11.2 rejects or no longer uses settings for Scouts, Mission Interpreter, semantic grounding, ProjectMemory prompt budgets, evidence replay, no-progress detectors, or legacy pipelines.
