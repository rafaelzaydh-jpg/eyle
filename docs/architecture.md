# Architecture

Eyle keeps meaning and mechanics separate.

```text
                    MEMORY GRAPH
                       ↕
USER ──► MAIN LLM ── ECC ──► RUNTIME ──► CAPABILITY BODY ──► WORLD
                  E / C / C
```

## Layers

1. **Main LLM** — understands meaning and chooses what to do.
2. **Objective State** — optional short state for what is still being pursued.
3. **Memory Graph** — persistent knowledge written semantically by Main.
4. **ECC Core** — carries one of three moves: Explorar, Construir, Concluir.
5. **Runtime** — mechanical safety, persistence, Evidence, limits, anchors and transactions.
6. **Capability providers** — deterministic body-specific operations.
7. **World** — whatever the attached Host can observe or change.

## Request and Objective

`current_request` is the exact input that created the AgentSession. Runtime may preserve and verify it, but never interprets it.

`objective_state` belongs to Main and may be `null`. It describes **what is still wanted**, never a tool plan. Runtime stores it but does not use its status to choose actions or block `Concluir`.

## Evidence and Memory

A physical capability result creates Material and active-session Evidence. Evidence means the world was actually observed.

Memory is different. Main decides whether the observation changed knowledge worth keeping. Runtime persists only the graph changes Main requested.

## Freshness

A memory can be linked to a physical source through a provider-owned anchor. Runtime can mechanically detect that the source changed and mark the anchor stale/degraded.

Freshness does not prove that Main's old interpretation was correct. Semantic correction remains Main's job.

## Body boundary

Core does not branch on code, desktop, robot, network, spreadsheet, or document domains. A Host attaches providers. Providers own their input schemas, execution, Evidence selectors, and freshness checks.

## Semantic boundary

Main decides meaning, relevance, intent, memory content, relations, Objective meaning, next investigation steps, and when enough is known.

Runtime decides only mechanically checkable facts: contract validity, IDs, persistence, limits, permissions, confirmation, transactions, rollback, anchor freshness, graph topology, and deterministic execution.
