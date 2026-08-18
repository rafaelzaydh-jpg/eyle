# Memory Graph v12

Eyle Memory is persistent learned state attached to the agent, not a hidden prompt router and not a fourth ECC movement.

Main decides what knowledge means. Runtime owns mechanical storage, revision, reachability, provenance identity, and retrieval mechanics.

## Core model

A Memory node has separate physical dimensions:

```text
scope        reachability boundary
domain       chat | task | eyle | knowledge
context_key  optional physical context identity
```

These dimensions are intentionally independent.

- `scope` answers **where can this node be reached from?**
- `domain` answers **what physical class of context does it belong to?**
- `context_key` identifies a concrete context when the domain needs one.

`domain` does not replace `scope`.

## Authorship

### Main-authored state

Main authors semantic knowledge through `memory_delta`, including:

- remember;
- revise;
- relate;
- revise relation state;
- archive;
- supersede;
- retire relation;
- Task meaning/lifecycle decisions.

### Runtime-authored facts

Runtime may persist facts that are mechanically knowable without semantic interpretation, such as:

- conversation ID;
- message ID;
- role;
- ordering;
- timestamps;
- current physical context identity.

Runtime does not summarize user meaning on its own.

## Memory as an ECC sidecar

Memory is validated independently from the ECC decision.

```text
valid ECC decision
+ invalid/rejected memory_delta
=
ECC continues
+ Memory rejection is recorded
```

A Memory parser/storage problem must not create an extra paid cognition solely to rescue the sidecar.

## Retention

Retention is a persistence choice, not a truth label or automatic prompt tier.

Current retention classes include:

- `temporary`;
- `persistent`.

A Temporary node is not automatically "hot" and a Persistent node is not automatically projected.

## Epistemic state

Nodes and relations can carry canonical epistemic metadata such as:

- nature;
- confidence;
- volatility;
- temporal/context fields;
- evidence timestamps;
- support references.

Parser and storage validate the same canonical structure at separate boundaries.

Runtime stores the structure mechanically. Main remains responsible for what the knowledge means.

## Revision history

Current state is returned by default, while historical revisions remain persisted and inspectable.

Support references are pinned to the exact Memory/relation revision used when committed. If the referenced object later changes, Runtime can report revision drift mechanically; it does not infer that the old support became semantically false.

## Relations

Graph relations connect Memory nodes without creating a second semantic engine.

Main chooses the semantic relation. Runtime owns:

- relation identity;
- current revision;
- revision history;
- support references;
- mechanical retirement.

## Task Memory

A Task is an ordinary `kind=task` Memory node plus minimal mechanical lifecycle state:

```text
active | blocked | resolved | cancelled
```

Task meaning and relations remain Main-authored.

Task state does not create a hidden automatic working set and does not narrow global recall.

## Recall

Recall performs literal/mechanical candidate discovery.

Current mechanics use:

- FTS5 when available;
- SQL fallback;
- exact IDs;
- tags;
- scope;
- `domain`;
- `context_key`;
- retention/epistemic filters;
- relation labels;
- optional neighbor expansion.

Main decides whether returned candidates are semantically relevant.

Runtime never promotes lexical rank into universal meaning.

## Activation

Recall discovery and prompt materialization are separate.

When Main explicitly activates Memory:

1. Runtime selects/persists the exact result set;
2. the next page is materialized in `memory_view`;
3. an exact Frontier represents any remaining page;
4. the operation observation contains compact IDs/counts/provenance instead of duplicating the node bodies.

This gives Memory bodies one prompt authority.

## No automatic projection

Normal cognition does not call a global `project_memory_view`.

There is no:

- `memory_focus`;
- Active Projection;
- HOT/WARM/COLD tier;
- automatic Temporary Memory working set;
- hidden semantic selector;
- embedding-based prompt insertion.

The graph can grow without proportionally growing a trivial prompt.

## Graph-size invariant

A larger Memory Graph must not increase baseline prompt size merely because more nodes exist.

The intended relationship is:

```text
graph growth
    !=
automatic prompt growth
```

Nonmaterialized nodes remain reachable through explicit recall/activation/paging.

## Conversation continuity

Recent conversation is provided directly through native conversation roles. Memory is not used as a substitute for the current conversation window.

Older conversation can remain reachable through the `chat` domain and its context identity when explicit recall is required.

## Migration boundary

Runtime opens v12 only.

The retained historical conversion is an explicit one-shot v11 → v12 tool:

```bash
python -m eyle.devtools.migrate_memory_v11_to_v12 <storage-directory>
```

The migration performs mechanical metadata conversion. It does not ask an LLM to reinterpret historical Memory and is not imported by the normal request path.
