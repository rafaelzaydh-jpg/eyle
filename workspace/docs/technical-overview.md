# Technical overview — Eyle Rev5.7.5

Eyle uses one Main-LLM execution loop with deterministic capabilities, canonical observation/evidence state, bounded model-facing projection, supervised writes and optional Claim Review.

## Agent loop

```text
request
  ↓
Main LLM
  ├─ Investigation updates
  ├─ tool/tool_calls
  ├─ patches
  └─ Final
        ↓
Runtime validation/execution
        ↓
ObservationLedger → EvidenceLedger
        ↓
projected next-turn context
```

There is no semantic router, task classifier, `workspace_scope`, phase scheduler, Tool Selector or runtime-created Investigation target.

## Tool discovery and hot contracts

The first Agent call receives a compact `capability_index`. Any indexed capability may be called immediately.

After use, full tool contracts are not accumulated forever. The model-facing hot set contains only the two most recently requested distinct tools in `active_tools`; older tools return to compact capability navigation and remain callable.

This is deterministic context projection derived from DecisionLedger events, not semantic tool selection.

## Canonical state ownership

```text
ObservationLedger → physical tool events, replay identity, coverage and continuation snapshots
EvidenceLedger    → Evidence identity, persistence, rehydration, freshness and navigation
DecisionLedger    → decision events and deterministic rejection identity
LLMCallLedger     → prompt metadata + provider attempts in one logical-call record
WriteTransaction  → patches, attempts, validation, failures and rollback
Investigation     → semantic debt declared by Main LLM
ClaimReview       → semantic audit
```

`ExecutionContext` owns run-scoped physical budgets/deadline, terminal capabilities and LLMCallLedger. History, Prompt Accounting and `execution_trace` project these owners rather than rebuilding parallel state.

## Capability result pipeline

```text
Capability
→ normalized physical result
→ ObservationLedger
→ optional Evidence
→ bounded model projection
```

All executable tools share the same physical envelope:

```text
status / ok / executed / changed / error_code / retryable
observations[] / coverage / frontiers[] / handles[]
detail
```

The observation fields may be empty. `effect=observe|execute|mutate` is registry metadata for physical behavior, not a task classifier.

## Directed structural observation

For code reachability, Main can request:

```text
symbol_relations(symbol="...", query="reachability")
```

The capability builds/resolves the current Python structural graph for the query, starts from explicit roots or objective entrypoint signals, and searches for a path to the target.

A positive result can return the complete path with edge coordinates and `coverage.objective_complete=true`. Incomplete results may expose objective frontiers such as depth boundaries or unresolved relevant structural transitions. Larger continuation payloads remain behind opaque handles and can be materialized with `expand_observation`.

The capability establishes structural relations only; Main decides what those relations mean for the user's requested property.

## Context projection

Canonical state is deliberately larger than repeated prompt state.

Main receives a bounded hot projection rather than every accumulated Evidence/Observation/tool contract on every turn. Current projection includes Investigation-pinned plus recent Evidence/Observation navigation, bounded latest tool deltas and the two hot tool contracts.

```text
canonical ledgers  ██████████████████████████████
model view         ███████
```

Projection is deterministic and navigational. Runtime does not semantically rank claims or choose the Evidence that matters.

## Evidence and Final

Tool observations may register Evidence. Runtime owns identity and freshness; Main owns relevance and sufficiency.

Final is:

```json
{
  "answer": "...",
  "limitations": [],
  "evidence_ids": ["ev-..."]
}
```

Those `evidence_ids`, plus Evidence Main already attached to Investigation, define the source-Evidence subset sent to Claim Review.

## Claim pipeline

```text
Final with grounded runtime state
→ Claim Review
→ supported
   OR semantic debt returned to Main

self_check Final with zero grounded runtime state
→ direct acceptance

verified mode
→ Claim always runs
```

Claim can use `request`, `answer:*`, `evidence:*`, `runtime:*` and `investigation:*` grounding coordinates. It cannot call tools or mutate the project.

## Physical inference budget

Current default job envelope:

```text
backend request context <= 32768
prompt attempts         <= 90000
completion              <= 8000
physical total          <= 98000
```

Prompt attempts are charged in full even when the provider reports cache hits. Turns, tools, LLM calls and deadline are independent fuses. Per-call output reservation is adaptively fitted while preserving mandatory downstream Claim reserve.

## Writes and command execution

Real project writes follow one path:

```text
patches → dry-run → confirmation → apply → verification → success/rollback
```

`run_command` is unrestricted only inside a strong disposable project snapshot. It cannot mutate the real workspace directly.

## Structured output

Structured Agent/Claim calls require strict JSON Schema. Unsupported backend capability is a hard boundary error. There is no capability negotiation/cache, JSON-object downgrade, prompt fallback or structural-repair retry.

## Persistence

```text
session         5.7.5 exact
queue           5.7.5 exact
project memory  5.7.5 exact
config          5.7.5 exact
```

Deprecated aliases, migration bridges and dual-read contracts do not exist. Same-version persistence envelopes are exact rather than permissive, and benchmark artifacts use an independent exact schema version (`1`).

Compatibility inside the Core is treated as suspicious. Compatibility behind provider/environment/domain adapters is desirable when all variants normalize into the same canonical Core contract.

## Scope

This overview documents the current coding-agent runtime. Broader reuse of the observation protocol is a future design direction documented in [architectural-direction.md](architectural-direction.md), not a current non-coding product claim.

## Rev5.7.5 canonical-boundary hardening

- `search_code` backends share one deterministic file universe and one canonical ranking/truncation stage. `ripgrep-json` and `python-fallback` are physical execution choices, not different search contracts.
- Conversation history enters the Core only as `{role, content}`. Runtime storage/UI formats are normalized before the AgentSession loop.
- `agent_info` exposes and projects `registered_tools`; `tools` is not an accepted alias.
- Pending continuations use exact `pending_schema_version=1` envelopes with English field names. Core and persisted Runtime shapes reject missing, unknown, old or alternate fields.
- Python 3.8+ is the supported floor; code does not carry feature-detection branches for APIs guaranteed by that floor.
