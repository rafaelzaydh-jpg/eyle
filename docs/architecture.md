# Eyle Rev5.1 architecture

```text
interface
→ runtime/service
→ AgentSession
→ administrative structured handshake
→ main LLM ↔ 16 deterministic tools + live workspace
→ Evidence Core
→ deterministic Final Gate
→ Claim Review
   ├─ supported + no unresolved material gap → response
   ├─ contradicted → local Repair → local Reverify
   └─ insufficient / semantic gap → main-agent follow-up
```

## Responsibility boundaries

- **Main LLM:** semantic decisions, investigation, tool choice, answer wording, patch intent.
- **Runtime:** contracts, paths, hashes, freshness, visibility, budgets, confirmation, transactions, persistence.
- **Tools:** deterministic observations/actions; no semantic routing.
- **Evidence Core:** complete runtime-owned EvidenceRecords and bounded model-visible views.
- **Final Gate:** deterministic shape, Evidence identity/grounding, formatting and write authority.
- **Claim Review:** the single semantic final verifier.

The architectural rule is: **the LLM decides semantics; the runtime validates contracts.**

## Structured boundary

`llm/structured.py` owns the canonical contracts for `agent`, `claim_verifier`, and `claim_repair`. Before the first structured use of a connection/model, `llm/capabilities.py` behaviorally verifies the best available mode: `json_schema`, `json_object`, then prompt-driven JSON. The result is machine-local and revalidated after restart or a structural failure.

Provider enforcement is useful but never authoritative. Every structured result is parsed and validated locally by Eyle. The core never branches on Qwen/Llama/provider identity.

## Claims, Evidence and gaps

Claims and Evidence are proportional to material content, not repository size. Guidance such as ~6, 12, or 20+ is not a quota. Semantic Gaps represent material omission, conflicting Evidence, or scope/investigation gaps. Malformed local Claim/Gap contracts are re-evaluated in isolation while valid siblings are preserved.

## Writes

There is one model-facing write path: `action=patches`. Runtime enriches the transaction with deterministic preconditions, performs dry-run, asks for explicit confirmation, applies the transaction, compiles/tests/rereads, and rolls back on validation failure. Patch operations are not public agent tools.

## Global guards

Default job guards remain bounded by working set, cumulative prompt/completion/total budgets, agent turns, LLM calls, tool calls, task deadline, no-progress detection and repeated-call protection. Output limits are ceilings; only actual provider usage is charged.

## Context boundary

`request` is the only active task. `conversation_background` is stable across turns but non-authoritative; previous goals do not become current goals automatically. `investigation_map` is current-session navigation state derived from observable successful tool history and survives semantic follow-up.

Blocked reads are control feedback, not executions. The identical-tool loop counts only executable calls that actually ran. Claim, Semantic Gap and Finding consistency recoveries remain internal verifier operations.
