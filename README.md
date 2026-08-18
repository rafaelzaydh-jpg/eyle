# Eyle 2.7.5 — Rev3.7.2

Rev3.7.2 is the **Canonical Cut Review**. It does not add a planner, semantic router, provider, Memory tier, or new cognitive move. Its purpose is to leave one active implementation path for each current responsibility and delete runtime compatibility paths that no longer belong to the architecture.

> **Main owns meaning. Runtime owns physical truth and enforceable limits. Historical compatibility belongs to explicit migration tools, not permanent runtime branches.**

## Current architecture

```text
User request
    │
    ▼
Service ── physical conversation facts ──► ContextMaterializer
                                             │
                                             ├─ current request
                                             ├─ recent conversation by token budget
                                             ├─ current task mechanics
                                             ├─ latest observations by token budget
                                             ├─ explicit Memory activation
                                             └─ compact feedback/frontiers
                                             │
                                             ▼
                                            Main
                                      semantic authority
                                             │
                               ┌─────────────┴─────────────┐
                               ▼                           ▼
                              ECC                     memory_delta
                    Explore / Build / Conclude              │
                               │                             ▼
                               ▼                       Memory Graph v12
                            Runtime                    sidecar; no ECC veto
```

The runtime does not create relevance scores, Active Projection, `memory_focus`, HOT/WARM/COLD tiers, hidden working sets, semantic context routing, or a second planning LLM.

## Canonical paths

Rev3.7.2 deliberately removes duplicate/compatibility paths:

- the bundled provider lives at `eyle.providers.standard`; the old `standard_impl` package/facade path is gone;
- Runtime accepts only the current Rev3.7.2 configuration identity;
- Runtime accepts only Memory Graph v12; v11→v12 exists only as an explicit one-shot devtool;
- session/pending continuation state is current-schema only;
- conversation history is materialized by physical token budget, not a fixed message count;
- Memory enters the normal prompt only through explicit Main activation;
- provider-reported token usage is the execution ledger authority;
- the public Adapter request uses `max_completion_tokens`; the removed `max_tokens` input alias is rejected;
- the UI exposes one `interaction` contract; the removed `confirmation` alias is not emitted.

## ECC and Memory

Main has three cognitive moves:

1. **Explore** — observe/read/test/recall without persistent world mutation.
2. **Build** — request a persistent physical change through Runtime safeguards.
3. **Conclude** — answer when Main judges the request resolved.

Memory is a sidecar to the ECC decision. Runtime validates and persists it independently. A valid ECC decision remains executable if `memory_delta` is invalid or rejected by the graph; the rejection is recorded rather than causing another paid cognition solely to rescue Memory.

Memory Graph v12 separates:

- `scope`: physical reachability (`user`, current world, `all`, `global`);
- `domain`: `chat`, `task`, `eyle`, or `knowledge`;
- `context_key`: physical context identity when a domain needs one.

Chat continuity is Runtime-ingested because message identity, role, conversation ID and ordering are physical facts. Semantic task understanding and durable learned knowledge remain Main-authored.

## Deterministic context materialization

`ContextMaterializer` uses only physical identities and budgets. It does not decide which topic is relevant.

Recent conversation and observations are materialized up to configured token budgets. Omitted material remains reachable through the existing Memory/Evidence/Material/Frontier mechanisms. A larger Memory Graph must not automatically produce a larger trivial prompt.

## Provider and execution budgets

Eyle talks to the bundled local Adapter on port `8080`. The Adapter is transport-only and uses the configured DeepSeek profile. Provider-specific credentials and upstream routing stay in `server/`.

Current physical limits include:

- `50000` context tokens per physical LLM call;
- `150000` provider-accounted tokens per user-message execution;
- transport/sandbox/process limits;
- interaction expiration TTL.

There is no cognitive task deadline, generated-token fuse, fixed exploration count, fixed Memory working-set size, or fixed conversation-message ceiling.

## Standard provider

The single bundled implementation is `eyle.providers.standard`. It owns project inspection, file/tree reading, code search, symbol relations, commands/tests, Git inspection, workspace transactions, sandbox export and sandbox promotion.

Core does not contain repository/language-specific planning logic.

## Verification

Before publication, run:

```bash
python -B -m eyle.devtools.release_identity
python -m compileall -q eyle llm server web tests main.py
python -m pytest -q
python -m pytest -q server/tests
node --check web/static/app.js
```

The release verifier is current-only: it rejects the reappearance of removed facades, runtime migrations, dead ceilings/deadlines, automatic Memory projection, stale session/pending identities and noncanonical public contracts.

See `docs/architecture.md`, `docs/configuration.md`, `docs/model-surface.md`, `docs/memory-kernel.md`, `docs/verification.md` and `CHANGELOG.md`.
