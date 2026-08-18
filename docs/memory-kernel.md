# Memory Graph v12

Memory is intrinsic learned state beside ECC, not a fourth cognitive move and not a hidden context router.

## Schema dimensions

Each node has separate physical dimensions:

```text
scope        reachability boundary
domain       chat | task | eyle | knowledge
context_key  optional physical context identity
```

`scope` retains the current `user`, world, `all` and `global` reachability semantics. `domain` never replaces scope.

## Authorship

Main authors semantic knowledge through `memory_delta`: remember, revise, relate, revise relations, archive/supersede, retire relations and task lifecycle decisions.

Runtime authors only mechanically knowable state. In particular, current user/assistant message identity and ordering may be persisted in the `chat` domain without asking Main to summarize what was said.

## Sidecar contract

Memory cannot veto an already valid ECC decision. Wire parsing first obtains/validates the decision; Memory is parsed/applied as an independent sidecar. A Memory rejection is observable telemetry/feedback and the valid ECC path continues.

## Retention and epistemic state

`temporary` and `persistent` are retention choices, not truth labels or automatic prompt tiers.

Nodes/relations can carry the shared canonical epistemic structure, including nature, confidence, volatility, temporal/context fields and evidence timestamps. Parser and storage validate the same definition at separate boundaries.

## Current state and history

A node/relation exposes its current revision by default. Revision history remains persisted and inspectable. Support references are pinned to the exact referenced Memory/relation revision used when committed; later revision drift is reported mechanically rather than interpreted as semantic invalidation.

## Task Memory

A task is an ordinary `kind=task` graph node plus minimal mechanical lifecycle state:

```text
active | blocked | resolved | cancelled
```

Task meaning and relations remain Main-authored. Task state never creates an automatic prompt working set or narrows global recall.

## Recall

Recall uses literal/mechanical candidate discovery (FTS5 with SQL fallback), persists the exact selection in SQLite and materializes a page. An exact cursor Frontier exposes the remainder without storing the full match universe in Session.

Main may supply queries, exact IDs/tags, epistemic filters, relation labels and neighbor expansion. Runtime never treats lexical rank as semantic importance.

## No automatic projection

Normal cognition does not call a global `project_memory_view`. Only explicit Main activation is materialized as Memory context.

There is no `memory_focus`, Active Projection, HOT/WARM/COLD tier, automatic Temporary Memory working set or hidden semantic selector.

## Graph size independence

Growing the graph must not proportionally increase a trivial prompt. Nodes that are not materialized remain reachable through explicit recall/activation/paging.

## Migration boundary

Runtime opens v12 only.

The sole retained historical conversion for this release is the explicit one-shot v11→v12 tool:

```bash
python -m eyle.devtools.migrate_memory_v11_to_v12 <storage-directory>
```

It performs mechanical metadata conversion only. It does not ask an LLM to reinterpret historical nodes and it is not imported by the normal runtime.
