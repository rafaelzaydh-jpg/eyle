# Technical overview — Eyle 2.7.4 Rev4.12.4.1

Eyle is a single LLM-driven programming agent. One model decides whether to answer, inspect the live workspace, use a deterministic utility, consult external memory, or propose a supervised write. Tool results return to the same `AgentSession`.

## Large-project behavior

The repository is never assumed to fit in one prompt. The LLM can begin with objective signals (`inspect_project`, `project_stats`, tree/search) and then read only sources relevant to the current task. “Important file” is not a tool-generated property; importance depends on the question.

## Tool-assisted accuracy

Deterministic tools reduce mental arithmetic and repository guessing. Rev4.12.4 sends one shared tool taxonomy per call: `READ_ONLY`/`EDIT` authority plus effect tags (`NONE`, `EXEC`, `TEMP`, `MEMORY_WRITE`, `WORKSPACE_WRITE`, `VERIFY`, `ROLLBACK`). Individual contracts keep only `purpose`, compact `inputs`, `returns`, tool-specific `caveats`, and configured numeric `limits`; there is still no per-tool routing-hint layer:

- `calculate` evaluates bounded arithmetic;
- `project_stats` measures text files/lines/characters/bytes/languages;
- `count_tokens` reports measured characters and explicitly marks heuristic token conversion as `exact: false` when no exact tokenizer exists;
- `inspect_project` emits objective structure/relation signals;
- `agent_info` exposes current runtime identity and available tools;
- `run_tests` executes the detected suite in sandbox, may focus pytest on one safe path, and distinguishes unavailable runners from failing tests;
- `git_status` and `git_diff` inspect repository state without modifying Git;
- `execution_trace` exposes sanitized current/persisted execution facts without diagnosing them.

## Token efficiency

The fixed agent prompt stays compact. Obvious greetings, calculator requests and self-capability questions keep a cheap fast path; other requests in a real workspace receive investigation capability instead of depending on a lexical project-task classifier. Full chat history is used only where useful, a smaller task anchor persists across turns, and equivalent reads are blocked from fresh evidence.

Token accounting distinguishes:

- provider-reported prompt tokens;
- cached prompt tokens;
- new/uncached prompt tokens;
- effective prompt tokens using the configured cache weight;
- generated/completion and reasoning tokens where the provider reports them.

Cache accounting never replaces loop control. The phase machine does.

## Observable execution history

Rev4.12.4 preserves the sanitized runtime history for each persisted job and makes tool names explicit in the public trace. The trace is not a reasoning transcript. It is a structured record of observable actions:

1. prompt/call metadata by turn and phase;
2. tools actually attempted/executed with redacted arguments;
3. summarized tool results;
4. post-write compile/test/reread/rollback stages;
5. aggregate token usage and failure codes;
6. the accepted/rejected decision type for each turn, including deterministic rejection codes.

The history is stored inside the normal persisted job result and fetched only on demand by the UI. No extra LLM call is used to explain the history.

## Security boundary

The history serializer intentionally discards code bodies, memory values, raw prompts and model response text. The existing web path redactor still removes known internal absolute paths before JSON is returned.

## Engineering archaeology

[`UPDATE_HISTORY.md`](../UPDATE_HISTORY.md) documents discarded architectures and their failure modes. It is a design constraint: old mechanisms require new evidence before revival.

## State-aware test completion

`run_tests` does not automatically terminate every read-only task. The runtime closes tools only when the execution state shows a narrow test-only flow; if other project observations already occurred or the LLM declared a multi-step plan, investigation remains open.

## Utility-response rule

Deterministic tools do not write the user-facing answer. For arithmetic, the intended path is `LLM → calculate → LLM final`, normally two model calls. The calculator result is promoted to citable runtime evidence so a structured second-turn final does not need a repair call.
