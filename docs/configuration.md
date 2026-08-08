# Configuration — Eyle Rev5.1

Rev5.1 accepts only the current public configuration schema. Unknown fields fail with `UNKNOWN_CONFIG_FIELD`; removed compatibility keys are not interpreted or translated.

## LLM connection

`llm.openai_compatible=true` selects OpenAI-compatible Chat Completions; `false` selects Ollama `/api/chat`. `llm.model` is an explicit model name or `auto` for model discovery. An explicit model is never silently substituted.

Structured-output capability is discovered empirically. No provider-specific `structured_output` setting is required. Eyle probes `json_schema`, `json_object`, then prompt JSON and stores the verified result in machine-local `context/llm_capabilities.json`.

## Context and budgets

The default active working-set target is 12,000 tokens. Canonical task ceilings are 8 main-agent turns, 12 LLM calls, 12 tool calls, 96,000 cumulative prompt tokens, 9,000 cumulative completion tokens, and 105,000 total tokens. Ceilings are elastic: actual provider usage is charged.

`agent.context_view` contains only preview/working-set limits; it is not a semantic quality gate. `agent.chat_history_token_budget` bounds the stable per-job `conversation_background`; the removed `task_context_token_budget` is no longer part of the schema.

## Claim Review

`agent.claims.mode` may be `off`, `self_check`, or `verified`. `self_check` uses the main connection. `verified` requires an explicit distinct verifier connection/model.

Claims use the canonical fields `id`, `answer_ref`, `statement`, `kind`, `evidence_ids`, `verdict`, and `reason`. There is no fixed Claim or Evidence item count. Semantic Gaps use `material_omission`, `conflicting_evidence`, or `scope_gap`; the first two require relevant visible Evidence, while `scope_gap` may be evidence-empty when the problem is missing/partial investigation.

## Writes

The LLM never calls public patch tools. It emits `action=patches`; runtime performs transactional dry-run, asks for confirmation, applies, validates, rereads and rolls back on failure.

## Machine-local state

The repository intentionally keeps only `.gitkeep` files in `context/`, `memory/`, and `workspace/`. Generated databases, capability cache, Python caches, logs and runtime artifacts must not be committed.
