# Configuration — Eyle Rev5.2.9

Rev5.2.9 accepts only the current public configuration schema. Unknown fields fail with `UNKNOWN_CONFIG_FIELD`; removed compatibility keys are not translated.

## LLM connection

`llm.openai_compatible=true` selects OpenAI-compatible Chat Completions; `false` selects Ollama `/api/chat`. `llm.model` is explicit or `auto`. Structured-output capability is discovered empirically and cached machine-locally in `context/llm_capabilities.json`.

## Context and physical budgets

The default active working-set target remains 12,000 tokens. Canonical jobs still start with 8 Main Agent turns, 12 LLM calls, a 12-tool base fuse, 96,000 cumulative prompt tokens, 9,000 cumulative completion tokens, and 105,000 total tokens. Rev5.2.9 extends only the tool fuse through runtime-validated committed progress after unified observation preflight: `agent.committed_progress_extension_calls` defaults to 4. Every unspent progress epoch can unlock +4 exactly once; there is no cumulative earned-extension ceiling. Extensions are physical runtime authority, not semantic completion signals, and Claim Review does not grant them.

`agent.context_view` controls physical preview sizes only. `agent.chat_history_token_budget` bounds stable `conversation_background`. Investigation targets are semantic state produced by the LLM; there is no fixed target-count setting.

## Investigation Contract

The contract is not configured by the user and has no heuristic thresholds. The canonical state is runtime-owned and the agent structured response carries only `investigation_updates`. Runtime validates and commits target identity/status/Evidence integrity; Claim Review validates semantic adequacy only after a provisional final.

## Claim Review

`agent.claims.mode` may be `off`, `self_check`, or `verified`. Semantic Gaps now include nullable `target_id` in addition to `id`, `type`, `evidence_ids`, and `reason`. `material_omission` and `conflicting_evidence` require relevant visible Evidence; `scope_gap` may be Evidence-empty for missing/partial investigation.

## Writes

The model emits `action=patches`; runtime performs transactional dry-run, confirmation, apply, validation, reread and rollback.

## Machine-local state

Only `.gitkeep` belongs in `context/`, `memory/`, and `workspace/`. Databases, capability cache, Python/test caches, logs and runtime artifacts must not be committed.

`ObservationLedger`, `workspace_epoch`, replay counters and the Decision Ledger are runtime session state, not user-configurable semantic policy. No new public limit was added in Rev5.2.9; the obsolete cumulative earned-extension limit was removed.
