# Configuration — Eyle Rev5.2.3

Rev5.2.3 accepts only the current public configuration schema. Unknown fields fail with `UNKNOWN_CONFIG_FIELD`; removed compatibility keys are not translated.

## LLM connection

`llm.openai_compatible=true` selects OpenAI-compatible Chat Completions; `false` selects Ollama `/api/chat`. `llm.model` is explicit or `auto`. Structured-output capability is discovered empirically and cached machine-locally in `context/llm_capabilities.json`.

## Context and physical budgets

The default active working-set target remains 12,000 tokens. Canonical job fuses remain 8 Main Agent turns, 12 LLM calls, 12 tool calls, 96,000 cumulative prompt tokens, 9,000 cumulative completion tokens, and 105,000 total tokens. Rev5.2 does not raise these limits.

`agent.context_view` controls physical preview sizes only. `agent.chat_history_token_budget` bounds stable `conversation_background`. Investigation targets are semantic state produced by the LLM; there is no fixed target-count setting.

## Investigation Contract

The contract is not configured by the user and has no heuristic thresholds. It is part of the canonical agent structured response. Runtime validates target identity/status/Evidence integrity; Claim Review validates semantic adequacy.

## Claim Review

`agent.claims.mode` may be `off`, `self_check`, or `verified`. Semantic Gaps now include nullable `target_id` in addition to `id`, `type`, `evidence_ids`, and `reason`. `material_omission` and `conflicting_evidence` require relevant visible Evidence; `scope_gap` may be Evidence-empty for missing/partial investigation.

## Writes

The model emits `action=patches`; runtime performs transactional dry-run, confirmation, apply, validation, reread and rollback.

## Machine-local state

Only `.gitkeep` belongs in `context/`, `memory/`, and `workspace/`. Databases, capability cache, Python/test caches, logs and runtime artifacts must not be committed.
