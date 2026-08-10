# Technical overview — Eyle Rev5.6

Rev5.6 keeps canonical state ownership and adds progressive tool disclosure plus a hard training budget so the Main LLM cannot spend an open-ended execution envelope.

## Agent loop

Each Main LLM call receives the active request, optional Investigation, latest tool results, an ObservationLedger-derived `observation_map`, Evidence index and physical limits. Tool discovery is progressive: `capability_index` contains compact signatures/purposes for tools not yet used; `active_tools` contains expanded contracts only for tools the Main LLM actually requested before. Any indexed tool can be called immediately, so there is no selector call. First use itself activates the expanded view for later turns. The Main LLM directly chooses tools, patches, user input or Final.

There is no semantic router, task classifier, `workspace_scope`, phase scheduler or Investigation requirement.

## Canonical state ownership

```text
ObservationLedger → physical tool events, replay identity, source coverage and pending observation view
EvidenceLedger    → Evidence identity, persistence, rehydration, freshness and index
DecisionLedger    → decision event history and rejection identity
LLMCallLedger     → prompt metadata + provider attempts in one logical-call record
WriteTransaction  → patches, attempts, validation, failures and rollback
```

`ExecutionContext` owns run-scoped physical budgets/deadline and the LLMCallLedger. `config` remains immutable configuration. `runtime/history.py`, Prompt Accounting and `execution_trace` project these owners instead of rebuilding parallel runtime state.

## Evidence pipeline

```text
Tool
→ normalized observable result
→ ObservationLedger
→ Evidence
→ Main LLM
```

Runtime validates identity/freshness. It never decides whether Evidence proves a semantic conclusion.

## Claim pipeline

```text
Final with grounded runtime state
→ one Claim Review
→ accepted
   OR debt returned to Main LLM

self_check Final with zero Observation/Evidence/Investigation/write state
→ direct acceptance (no semantic router; there is simply no grounded state to audit)

verified mode
→ Claim always runs
```

There are no Findings lane, targeted Claim recovery, semantic-gap recovery or structural repair retries.

## Context and training budget

There is no artificial working-set target, fixed Evidence-count cap, fixed observation-count cap, `chat_history_token_budget`, `relevant_sources` or `visible_source_ranges`. Compact canonical state remains available until the real model context window and safety margin require physical cropping.

The current Llama Server window is hard-capped at **32768 tokens per backend request**. One user message/job also has a **98000-token physical envelope** across Agent, transport attempts and Claim. Prompt attempts are charged in full; cache discounts do not buy more budget. Current sub-fuses are 90000 prompt tokens and 8000 completion tokens.

## Structured output

Structured Agent/Claim calls require JSON Schema strict. Unsupported backend capability is a hard boundary error. No capability negotiation/cache, JSON-object downgrade, prompt fallback or truncation re-call exists.

## No compatibility layer

```text
session         5.6 exact
queue           5.6 exact
project memory  5.6 exact
config          5.6 exact
```

Deprecated aliases, migration bridges and dual-read contracts do not exist.
