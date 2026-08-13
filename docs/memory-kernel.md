# Memory Kernel — Eyle 2.7.5 Rev1.4.3

Rev1.3.6 introduced the first Memory Kernel directly in Eyle; Rev1.4 keeps that contract unchanged. It is deliberately small and does **not** merge Observation, Tasks or Investigation.

## Law

> **The model owns meaning. The kernel owns memory state.**

Main decides what is worth remembering, region names, tags, relation labels, supersession and when a view is sufficient. The Kernel enforces only identity, persistence, revisions, references, atomicity, bounded materialization and continuation state.

## Physical shape

Only three Core modules exist:

```text
eyle/core/memory.py             public kernel surface
eyle/core/memory_store.py       SQLite state + ChangeSets + history
eyle/core/memory_navigation.py  bounded activation + continuation
```

There is no backend interface, graph database, vector database, salience engine, planner, router, memory LLM, CLI or automatic consolidation.

SQLite is the only store. `:memory:`/temporary databases are sufficient for isolated tests, so a parallel `MemoryStore` abstraction is intentionally absent.

## Persistent primitives

### Memory Node

```text
id
region
content
status: current | archived | superseded
revision
provenance
created_at
updated_at
```

### Tag

```text
memory_id
tag
```

Tags are semantic labels supplied by Main and mechanically indexed by SQLite.

### Relation

```text
id
source
label
target
status: current | retired
revision
provenance
```

Hierarchy is only a possible relation label. The Kernel does not impose a universal tree.

### ChangeSet / History

One ChangeSet may create/update/archive/supersede memories and create/retire relations. Revision preconditions and references are checked inside one SQLite transaction; any failure rolls the whole ChangeSet back. Every accepted mutation appends a physical history event.

## Bounded navigation

Memory navigation has its own contracts:

```text
MemoryView
├─ memories
├─ MemoryCoverage
└─ MemoryFrontier
```

These names are intentionally distinct from Observation `Coverage`/`Frontier`. The mechanics are similar, but Rev1.4 still does not extract a shared abstraction.

`activate_memory(...)` can seed navigation with region, tags, lexical text and direct relations. Direct relations may cross regions. A view materializes at most 30 Memory Nodes.

If more candidates remain, Runtime persists continuation state privately and returns only an opaque `mf-*` MemoryFrontier. `continue_memory_view(...)` consumes the next bounded page without exposing cursor state.

## Public Kernel API

The internal API is exactly:

```text
apply_memory_changeset(...)
activate_memory(...)
continue_memory_view(...)
memory_history(...)
```

`memory_record(...)` is a physical inspection helper, not an additional semantic operation.

## Agent tools

The existing public capabilities remain:

```text
memory_search
memory_store
```

They are adapters over the Kernel rather than a second memory system.

`memory_search` activates or continues a bounded view. Its compact discovery contract keeps uncommon seeds nested so the global capability index does not grow with Memory Kernel features.

`memory_store` creates one semantic node and may atomically add relations or supersede prior memories. Observation `grounding_ids` are optional provenance; they are not a truth gate. Memory may also represent decisions, preferences, intentions or semantic inferences that do not originate from Observation.

## What was removed

The old JSON project-memory implementation is gone:

```text
entries[-200:]
kind/text/files/created_at
mandatory grounding for every write
hash-filtered file-fact search
search_memory()/store_memory() legacy functions
```

Rev1.4 still does not migrate that legacy state.

## Separation from current Core state

Observation remains physical reality:

```text
Material + Coverage + Frontier
```

Tasks remain Main-owned intentional state.

Investigation remains Main-owned epistemic state.

Memory Kernel is persistent cognitive state.

The Kernel may later prove capable of physically hosting Tasks or Investigation, but Rev1.4 does not change their ownership or storage contracts. Repetition must be demonstrated before any extraction or merger.

## Proofs retained in Rev1.4

Deterministic tests prove:

1. 10,000 stored Memory Nodes can be queried while a view materializes at most 30.
2. MemoryFrontier continues without repeating the first page.
3. Relations can activate a connected memory across regions.
4. A stale revision causes `MEMORY_CONFLICT` and rolls back the whole ChangeSet.
5. Supersession preserves the old node and creates `superseded_by` history/structure.
6. History is append-only and survives reopening SQLite without transcript state.
7. An incompatible Memory Kernel schema is rejected rather than migrated.

## Explicitly deferred

Not in Rev1.4:

- embeddings;
- salience ranking;
- automatic tagging;
- automatic consolidation;
- automatic task completion;
- semantic truth resolution;
- vector/graph DB;
- shared Observation/Memory traversal abstraction;
- Tasks/Investigation migration;
- capability validators.

The next Memory revision should be justified by failures observed in longitudinal use, not by sections that existed in the original architecture document.
