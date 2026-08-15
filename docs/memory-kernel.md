# Persistent Memory Graph

Memory is Eyle's persistent knowledge. It is shared by Explorar, Construir, and Concluir. It is not a provider, tool, fourth action, or Runtime-authored knowledge base.

## What belongs in Memory

Anything that may help again later can be a memory candidate: user facts and preferences, decisions, rules, important identifiers, useful facts about a world/project, relationships, and conclusions that save future work.

Main chooses what is worth keeping. Runtime never decides semantic importance.

## Scopes

Nodes use two broad scopes:

- `world` — knowledge tied to the Host's opaque `world_scope_id`;
- `user` — knowledge that may follow the user across world scopes.

Core does not require filesystem or project semantics.

## Memory operations

Each ECC decision contains a Memory sidecar with `focus`, `disposition`, and `operations`.

- `unchanged` — persistent understanding did not change.
- `updated` — Main supplies one or more graph changes.

Main may `remember`, `revise`, `relate`, `archive`, `supersede`, and `retire_relation`.

## Support and provenance

A memory revision can be supported by:

- the current request;
- another memory node;
- current Material, with an optional provider-owned selector.

Runtime stores provenance, IDs, revisions, anchors, and freshness data without interpreting their meaning.

## Freshness is not truth

If a physical source changes, Runtime marks affected support stale/degraded. It does not delete the semantic memory.

A memory can also be fresh and still be wrong because Main interpreted its source badly. Only Main can revise that semantic mistake after seeing better Evidence.

## Evidence is separate

Every physical Material can automatically create active-session Evidence. Memory is optional semantic learning from that Evidence.

```text
Material → Evidence → Main interpretation → optional Memory change
```

## Graph mechanics

Runtime may expose degree, in/out edges, connected-component size, relation diversity, articulation status, retrieval count, and exposure tier. These are structural signals only. Main decides what they mean.
