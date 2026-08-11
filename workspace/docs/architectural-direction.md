# Architectural direction

This document records **future design direction**, not current product capability.

Eyle is publicly and currently a **coding agent**. New abstractions described here are architectural goals for future revisions and must not be presented as shipped support for books, networks, cloud systems, IoT, robotics or other domains until dedicated capabilities exist and are validated.

## North star

Eyle should evolve toward a small agency kernel whose semantic loop remains stable while domain-specific capability packs change around it.

The desired boundary is:

```text
MAIN LLM
  decides meaning, goals, material properties and stopping

CAPABILITIES
  expose objective operations over a domain

RUNTIME
  validates and executes physical actions, owns state and safety

OBSERVATIONS
  expose what was actually materialized

INVESTIGATION
  preserves semantic debt declared by Main

CLAIM
  challenges whether delivery is grounded in what was observed or done
```

A future architectural change should be judged by a simple question:

> Does this make Eyle better at operating arbitrary capabilities, or does it put knowledge of one specific capability/domain into the Core?

Domain knowledge should stay outside the Core unless the state truly belongs to the universal agent protocol.

## Compatibility doctrine

> **Compatibility inside the Core is suspicious. Compatibility behind adapters/capabilities is desirable.**

The Core should expose one exact canonical contract for each responsibility. It should not accept alternate field names, historical payload shapes, language aliases, dual-read/dual-write formats or silent downgrade paths merely to tolerate external variation. If a Core contract changes, Eyle should make a clean break and reject the previous shape.

External variability belongs at a boundary that owns it:

```text
provider / environment / domain protocol
                ↓
        adapter or capability
                ↓
       canonical Core contract
```

Examples of desirable compatibility behind adapters include OpenAI-compatible vs Ollama transport, Docker vs Bubblewrap execution, OS-specific process details, and future domain capability packs. The adapter may know many external protocols; AgentSession should know one.

### Future structured-output compatibility

Rev5.7.5 intentionally keeps Agent and Claim on one strict JSON-Schema contract. A future LLM adapter layer may support providers that expose structured output through different native mechanisms, for example:

```text
provider A  json_schema
provider B  tool/function call
provider C  json_object
provider D  constrained/plain text protocol
             ↓
      provider adapter
             ↓
canonical Agent/Claim object
             ↓
     strict Core validation
```

This is not permission to restore the old Core-level `json_schema -> json_object -> prompt` fallback chain. The compatibility decision and provider-specific parsing must stay inside the adapter. The Core sees one canonical profile or a transport/adapter failure.

This boundary is what can eventually let Eyle connect to a wider range of LLM runtimes without teaching the Core every provider protocol.

## Observation as the universal boundary

The strongest candidate for a stable Tool → Runtime boundary is a domain-neutral observation envelope:

```text
CapabilityResult
├─ execution state
│  ├─ status
│  ├─ ok
│  ├─ executed
│  ├─ changed
│  ├─ retryable
│  └─ failure/error
│
├─ observations[]
├─ coverage?
├─ frontiers[]?
├─ handles[]?
└─ domain data/detail
```

Simple capabilities should not pay infrastructure tax for state they do not have. A calculator may return a result with no meaningful frontier. A large repository query may return a rich observation with coverage, continuation boundaries and handles.

## Coverage

### General definition

**Coverage is the objective boundary of what an Observation claims to have materialized, examined or established for the physical query that produced it.**

Coverage is not semantic sufficiency. It does not answer whether the user has enough information, whether a conclusion is important, or whether Main should stop.

The Core should understand only the small protocol-level shape needed to transport and reason about observation completeness. Domain-specific metrics belong to the capability.

A future generic shape may converge toward something like:

```text
coverage
├─ status       complete | partial | blocked
├─ scope        opaque/domain reference
└─ domain data  optional capability-defined metadata
```

The exact schema should be proven by multiple capabilities before being frozen. Current Rev5.7.5 code-reachability fields such as `files_scanned`, `roots_tested`, `shortest_path_hops` and `objective_result=reachable` are **domain payload**, not universal Coverage semantics.

### Examples

Coding capability:

```text
coverage.status = complete
data.result = reachable
data.path_hops = 12
```

Knowledge capability:

```text
coverage.status = partial
data.sections_examined = [3, 4, 5]
```

Network capability:

```text
coverage.status = blocked
data.path_observed = [host-A, switch-2, router-7]
```

The Core should not need to know what `reachable`, a chapter, or a router means.

## Frontier

### General definition

**A Frontier is an objective continuation boundary at the edge of an Observation's Coverage.**

It represents something that may continue the physical/objective query but was not materialized in the current Observation.

A Frontier may be:

- **hard** — progress is physically or structurally blocked;
- **soft** — more material exists, but it was deliberately left unmaterialized to control cost/context.

A Frontier is not:

- an Investigation target;
- a semantic gap invented by Runtime;
- a statement that the remaining branch is relevant;
- an instruction to continue.

Only Main decides whether a Frontier can change the semantic conclusion.

A small generic shape is preferred:

```text
frontier
├─ at
├─ kind
├─ reason
└─ handle?
```

`kind` should remain capability-defined rather than becoming a Core enum containing every possible domain condition.

Examples:

```text
code:    dynamic_dispatch
book:    referenced_section_not_materialized
network: next_hop_unobservable
sensor:  device_offline
```

Runtime transports these values; it does not interpret their domain meaning.

## Handle

### General definition

**A Handle is an opaque reference that allows a specific continuation to be materialized later without retransmitting the entire observed space.**

A Handle should carry enough physical provenance for Runtime to reject stale or invalid continuation, while leaving continuation semantics to the originating capability.

Conceptually:

```text
Handle
├─ id
├─ source_capability
├─ resource/snapshot version
└─ opaque continuation state
```

The exact internal fields do not need to be model-visible.

The key invariant is:

```text
world state may persist
≠
all world state must remain in every model prompt
```

Handles are one mechanism for selective re-entry into canonical state.

## Directed Observation

The agent should increasingly ask capabilities **queries shaped like the property Main is trying to establish**, while keeping the property itself objective enough for deterministic execution.

Bad interaction pattern:

```text
Main asks for local neighbors
→ reads them
→ asks for next neighbors
→ reconstructs a large space token by token
```

Preferred interaction pattern:

```text
Main declares an objective query
→ capability performs mechanical traversal
→ returns the smallest observation that establishes the query
→ exposes only unresolved continuation boundaries
```

Current coding example:

```text
symbol_relations(
  symbol="parse_claim_review_response",
  query="reachability"
)
```

The capability performs graph traversal mechanically. Main decides whether structural reachability establishes the user's requested semantic property.

## Navigable spaces

Graphs are a powerful implementation technique, but **the Core must not assume every domain is a graph**.

Some capabilities naturally expose a navigable space:

```text
code        functions/modules → calls/imports/callbacks
knowledge   concepts/claims   → causes/references/contrasts
network     hosts/interfaces  → routes/links/services
cloud       resources         → dependencies/permissions
```

A reusable `NavigableSpace` or structural-query library may eventually be valuable, but it should live above the minimal Core observation protocol unless repeated implementations prove it is universal state.

The architecture should therefore remain layered:

```text
LAYER 1 — EYLE CORE
Observation / Coverage / Frontier / Handle / Evidence / Investigation / Effect

LAYER 2 — OPTIONAL NAVIGABLE-SPACE LIBRARY
nodes / relations / paths / traversal / continuation

LAYER 3 — DOMAIN CAPABILITY
Python / documents / network / cloud / devices / ...
```

A calculator should never need fake nodes and edges just to satisfy the Core.

## Knowledge-space possibility

One important future use case is large textual knowledge sources such as essays, books, reports or document collections.

A domain capability could transform source material into grounded semantic units:

```text
source spans
  ↓
claims / concepts / relations
  ↓
query-shaped observation
  ↓
coverage / frontier / handles
```

For example, instead of sending an entire book to Main for every question, a capability could answer an objective knowledge query with a small grounded subgraph:

```text
industrialization
→ urban migration
→ overcrowding
→ sanitation problems
```

Each semantic relation must retain source provenance. A model-extracted knowledge graph is an **index over what the source supports**, not a replacement source of truth.

This could support:

- rapid question answering over very large texts;
- question generation from claims/relations rather than arbitrary chunks;
- causal and relational traversal across distant sections;
- selective expansion of unresolved references;
- coverage-aware study/revision systems;
- bounded model context even when the stored knowledge space is large.

None of these are current Rev5.7.5 product capabilities. They are examples validating why the observation protocol should remain domain-neutral.

## Context projection follows the same principle

The Core should preserve canonical state without rematerializing everything on every inference.

```text
RUNTIME / WORLD STATE
████████████████████████████████████████

MODEL VIEW
██████
```

The current Context Projection work is the first step. Future projection should remain deterministic/navigational unless Main explicitly pins or requests state. Runtime must not become a hidden relevance model.

The rule is:

> Do not delete world state to save tokens. Stop materializing all world state on every inference.

## Effects and mutation

Observation generalization must not accidentally force a universal mutation framework before there is evidence for one.

Current `effect=observe|execute|mutate` is intentionally small. Future action/mutation abstractions should be introduced only when multiple real capability domains expose the same physical state responsibility.

Do not build speculative universal `ActionTransaction`, robotics, network or cloud state machines inside Core merely because they can be imagined.

## Freshness and provenance

Continuation is only useful if it is tied to the reality that produced it.

Future Handles/Observations must be able to express freshness against the relevant resource, which may not be the coding workspace. Depending on the capability this may require:

- content hash;
- resource version;
- snapshot identity;
- timestamp plus validation probe;
- external revision/ETag;
- workspace epoch for Eyle-owned project mutation.

`workspace_epoch` must not be promoted into a universal freshness mechanism for resources it does not own.

## Public product identity

Until non-coding capabilities are real, tested and supported, GitHub-facing material should describe Eyle as a **coding agent**.

The broader architecture is an internal/project direction:

```text
current product identity: coding agent
architectural ambition: domain-neutral agency kernel
```

The README should lead with current behavior. This document is the appropriate place to preserve the larger design intent for future revisions.

## Criteria for future revisions

A proposed Core change should preferably satisfy all of the following:

1. **Single semantic authority** — it does not create a competing router/planner/classifier.
2. **Domain neutrality** — Core state does not need to know Python, books, routers, devices or a specific tool's semantics.
3. **Objective ownership** — Runtime only enforces facts/state it can determine mechanically.
4. **Selective materialization** — large spaces can remain outside the repeated model prompt.
5. **Grounding provenance** — observations and semantic extractions can point back to the reality/source that produced them.
6. **Fresh continuation** — Handles cannot silently outlive the resource state they describe.
7. **No infrastructure tax without state** — optional concepts stay optional.
8. **Benchmark justification** — a new abstraction should solve a measured failure or enable a validated capability, not merely make the architecture look more general.
9. **Clean break when contracts change** — do not preserve obsolete runtime paths solely for backward compatibility.

## Near-term direction

The next work should remain grounded in the coding-agent benchmarks that already exist.

Priority areas:

- improve directed reachability so Main does not guess arbitrary traversal depth when the capability can continue mechanically;
- narrow code frontiers to unresolved continuation that can actually affect the active root-to-target query;
- make Handle/Evidence/Observation identifiers structurally unambiguous;
- continue reducing prompt amplification while preserving complete canonical ledgers;
- only after the current protocol is stable, extract the smallest truly generic Coverage/Frontier/Handle contract from multiple real capability cases.

The goal is not to generalize Eyle by renaming code-specific fields. The goal is to let the same semantic loop operate new capabilities **without changing its authority model or embedding each new domain into the Core**.
