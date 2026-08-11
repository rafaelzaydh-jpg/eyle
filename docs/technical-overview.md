# Technical overview — Eyle Rev5.8

Eyle uses one Main-LLM execution loop with deterministic capabilities, canonical Observation/SourceRecord/Evidence state, bounded model-facing projection, supervised writes and optional Claim Review.

## What this runtime buys in practice

The architecture is optimized for repository questions whose answer spans multiple files or requires proof of a path rather than a text hit. Main can use deterministic capabilities to trace entrypoints, calls, imports, callbacks, contracts and protected observation boundaries while canonical state remains available for grounding. The measured behavior belongs in [benchmark.md](benchmark.md); this document explains the mechanism rather than repeating benchmark tables.

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
ObservationLedger → SourceRecordLedger → explicit Evidence admission → EvidenceLedger
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
ObservationLedger  → physical tool events, replay identity, coverage and continuation snapshots
SourceRecordLedger → objective citable materialization, identity, persistence and freshness
EvidenceLedger     → Main-admitted Evidence identity and navigation
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

## Objective projection

Rev5.8 lets capabilities perform large deterministic work without turning Runtime into a relevance model. The Main LLM formulates the objective property; the capability may exhaust the corresponding literal search, AST relation or graph computation, then group/deduplicate/page the physical result using rules independent of user intent.

`search_code` demonstrates the pattern: it scans the complete readable literal-match universe, groups every range, applies deterministic cross-file diversity before inline limits, and exposes omitted objective ranges through handles. `coverage_complete` belongs to the searched scope; `projection_complete` belongs only to the model-facing materialization. Main alone decides whether any returned group/frontier matters.

## Truthful projection before token minimization

Objective Projection is intentionally not a semantic top-k system. A capability may process a very large search/graph/state space and return a bounded projection, but the Runtime must preserve enough objective metadata for Main to know what was actually examined and what remains outside the inline projection.

This creates a deliberate asymmetry:

```text
large deterministic machine work is acceptable
small additional truthful model context is acceptable
hidden semantic filtering is not acceptable
```

Main may continue through a Handle when the current projection is insufficient. If Main does not continue, that is a semantic stopping decision made with visible Coverage/frontier information — not a Runtime claim that omitted material is irrelevant.

Rev5.8's live message-contract rerun demonstrates the tradeoff: estimated physical tokens rose from 32,479 to 33,747 (+3.9%), while Evidence fell from 46 to 2, unreferenced Evidence fell to zero, and broad literal search exposed complete search Coverage plus continuation handles. That modest cost is acceptable because the additional state is truthful and navigable rather than duplicated semantic noise.

## Context projection

Canonical state is deliberately larger than repeated prompt state.

Main receives a bounded hot projection rather than every accumulated SourceRecord/Evidence/Observation/tool contract on every turn. Current projection includes recent SourceRecord navigation, Investigation-pinned/recent Evidence/Observation navigation, bounded latest tool deltas and the two hot tool contracts. Projection is recency/pinning based, never semantic relevance ranking by Runtime.

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

Final/Investigation may select `src-*` SourceRecords. Runtime promotes only those explicit selections into canonical `ev-src-*` Evidence. Those Evidence IDs define the source-Evidence subset sent to Claim Review.

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

Claim can use `request`, literal `request:r*`/`answer:a*` anchors, `evidence:*`, `runtime:*` and `investigation:*` grounding coordinates. Request anchors are coordinates, not parsed requirements. It cannot call tools or mutate the project.

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
session         5.8 exact
queue           5.8 exact
project memory  5.8 exact
config          5.8 exact
```

Deprecated aliases, migration bridges and dual-read contracts do not exist. Same-version persistence envelopes are exact rather than permissive, and benchmark artifacts use an independent exact schema version (`1`).

Compatibility inside the Core is treated as suspicious. Compatibility behind provider/environment/domain adapters is desirable when all variants normalize into the same canonical Core contract.

## Scope

This overview documents the current coding-agent runtime. Broader reuse of the observation protocol is a future design direction documented in [architectural-direction.md](architectural-direction.md), not a current non-coding product claim.

## Rev5.8 protected-resource identity boundary

- Rev5.7.5 canonical Core/adapters boundaries remain intact.
- Content-based secret classification is removed. Normal files are readable independent of variable names, literal values or key-looking source text.
- Only explicit credential/private-key resources and physical aliases restrict content access. Their existence may remain visible while reads/searches/diffs/parsing are denied.
- Generic `.pem`, public-key and certificate resources remain readable; explicit private-key names/containers remain protected.
- Sandbox snapshots omit only protected-resource paths and expose an objective omission count instead of silently deleting normal source.
- `symbol_relations` reports protected source-like omissions as a coverage boundary rather than claiming complete negative coverage.
