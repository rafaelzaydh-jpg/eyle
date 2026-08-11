# Eyle benchmark — Rev5.8

This document records **observed engineering behavior**, not marketing quotas. Eyle is a coding agent whose Main LLM is free to use the amount of investigation a task actually requires. Benchmarks exist to detect regressions in correctness, grounding, selective observation and physical efficiency.

Benchmark artifacts use independent `benchmark_schema_version=1`. The producer and comparators accept only the canonical English schema. Missing factual/read/write gates are never interpreted as success.

## What the benchmark is trying to measure

A useful coding agent must do more than find text. The benchmark suite focuses on whether Eyle can:

- locate relevant code in a real repository;
- reconstruct productive structural paths instead of guessing from references;
- distinguish a proved path from an unresolved negative result;
- preserve objective coverage/frontier limitations;
- avoid repeatedly materializing repository state that deterministic tools can traverse themselves;
- ground the delivered answer in observations/Evidence;
- stop after the requested property is sufficiently established;
- run engineering work without mutating the real workspace outside the supervised write path.

Tool count and tokens are diagnostics. A lower number is better only when factual quality and task satisfaction are preserved.

## Live message-contract investigation — before vs Rev5.7.7

The same class of task asked Eyle to determine whether the Core still accepted `text` as an alternative to `content`, trace the real entry path, identify the normalization boundary and establish the canonical message contract.

The earlier run encountered a protection bug that hid `eyle/core/agent.py`, repeatedly tried alternate observations, never reached Claim Review and eventually exhausted the physical prompt envelope. Rev5.7.7 ran with the corrected protected-resource identity boundary and completed the investigation.

| Metric | Earlier failed run | Rev5.7.7 run | Change |
|---|---:|---:|---:|
| Main turns | 20 | **4** | **-80.0%** |
| Physical tool executions | 29 | **8** | **-72.4%** |
| Observations | 35 | **8** | **-77.1%** |
| Observation replays | 6 | **0** | eliminated |
| Provider prompt tokens | 65,417 | **25,974** | **-60.3%** |
| Estimated physical tokens | 92,448 | **32,479** | **-64.9%** |
| Runtime duration | 114.14 s | **64.74 s** | **-43.3%** |
| Claim Review | not reached | **1 supported pass** | completed |
| Task outcome | `MAX_PROMPT_TOKENS_EXCEEDED` | **completed** | corrected |

The external Cloud usage counter observed around the successful run moved from approximately **907.2k to 935.7k** (about **28.5k**). That rounded UI counter is not the canonical Runtime benchmark metric, but it closely tracks the run's reported `total_effective=28,558` and is useful as an independent sanity check.

### What the successful run actually established

The run did not merely search for the words `text` and `content`. It:

1. searched the repository for the relevant contract vocabulary;
2. read the relevant Core message-history implementation;
3. used directed reachability to establish a productive path from a real entrypoint to `_conversation_history`;
4. inspected the Runtime service boundary and `recent_messages` flow;
5. concluded that Core uses the canonical `{role, content}` contract and does not retain a productive `text` dual-read route;
6. passed one grounded Claim Review.

The structural observation returned a 10-hop productive path:

```text
main.py::<module>
→ main.py::main
→ main.py::cmd_perguntar
→ eyle/runtime/service.py::processar
→ eyle/runtime/service.py::_retomar_agente_pendente
→ eyle/core/agent.py::executar_agente
→ eyle/core/agent.py::_executar_agente_bound
→ eyle/core/agent.py::_run
→ eyle/core/agent.py::_call_agent
→ eyle/core/agent.py::_compile_prompt
→ eyle/core/agent.py::_conversation_history
```

For this directed query, the capability reported:

```text
objective_complete: true
objective_result: reachable
shortest_path_hops: 10
protected_resources_skipped: 0
```

This is the intended division of labor: graph traversal is deterministic repository work; interpretation of what the path means for the requested contract remains with Main.

## Live Rev5.8 rerun — objective projection and Evidence admission

The same message-contract request was rerun on Rev5.8 after SourceRecord/Evidence separation and objective projection were introduced. The task again completed in four Main turns and eight physical tool executions. The important change was not a lower tool count; it was a cleaner distinction between **what the repository objectively exposed** and **what Main actually used as proof**.

| Metric | Rev5.7.7 | Rev5.8 | Interpretation |
|---|---:|---:|---|
| Main turns | 4 | **4** | unchanged |
| Physical tool executions | 8 | **8** | unchanged |
| Observations | 8 | **8** | unchanged |
| SourceRecords | mixed into Evidence | **52** | explicit objective/citable materialization |
| Evidence | 46 | **2** | **-95.7%** after explicit Main admission |
| Structurally unreferenced Evidence | 41 | **0** | eliminated |
| Claim-cited Evidence | 4 | **2/2** | every admitted Evidence was used |
| Evidence admission ratio | n/a | **3.85%** | 2 of 52 SourceRecords promoted |
| Provider prompt tokens | 25,974 | **27,503** | **+5.9%** |
| Estimated physical tokens | 32,479 | **33,747** | **+3.9%** |
| Fresh-observation local estimate | 11,707 | **14,064** | **+20.1%** richer objective observations |
| Evidence/Investigation state | 3,087 | **1,194** | **-61.3%** |
| SourceRecord state | n/a | **2,210** | new explicit state layer |
| Claim prompt tokens | 4,347 | **3,583** | **-17.6%** |
| Runtime duration | 64.74 s | **51.83 s** | observed -19.9%; provider latency variance applies |
| Replays | 0 | **0** | unchanged |
| Task outcome | completed | **completed** | grounded answer accepted |

### What Objective Projection actually did

The broad `search_code("text")` call is the clearest example. Rev5.8 objectively observed **969 literal matches across 84 files and 593 source ranges**. It did not send all 593 ranges to Main and it did not use a semantic relevance model to pick a hidden “best” subset. Instead it materialized a bounded, deterministic cross-file projection and exposed the remainder through a continuation handle:

```text
coverage_complete:    true
projection_complete:  false
files_with_matches:   84
matches_observed:     969
ranges_observed:      593
ranges_materialized:  12
remaining ranges:     581
continuation:          handle:search_code.ranges:...
```

The same pattern held for the other broad searches. Main saw a truthful objective projection, knew that more results existed, and retained the option to expand them. In this run Main did **not** need to expand those handles.

This is the intended scaling boundary:

```text
Main chooses the objective property to establish
        ↓
Capability exhausts/measures that property mechanically
        ↓
Runtime exposes truthful Coverage + bounded projection + Handle
        ↓
Main decides whether the projection is sufficient or whether to expand
```

No capability is allowed to decide that omitted facts are semantically irrelevant. `projection_complete=false` is therefore not a weakness hidden from Main; it is an explicit statement that more objectively matched material exists.

### Why +3.9% physical tokens is acceptable here

Rev5.8 used about **1,268 more estimated physical tokens** than Rev5.7.7. That is not considered a regression by itself. The extra cost bought:

- complete declared literal-search coverage rather than an early match-universe truncation;
- explicit separation of Coverage from model-facing Projection;
- addressable continuation handles for unmaterialized objective ranges;
- a SourceRecord layer that preserves what was observed without calling every breadcrumb Evidence;
- only two Main-admitted Evidence records, both used by Claim;
- zero structurally unreferenced Evidence;
- a smaller Claim packet and a much smaller Evidence/Investigation state.

Eyle therefore does **not** optimize for the fewest possible tokens. It optimizes for **epistemic integrity under bounded context**. A lower token count is desirable only when the same truthful Coverage, provenance, continuation ability, grounding and material task quality are preserved.

The preferred optimization order is:

```text
1. preserve truth and observable reality
2. preserve grounding and Coverage boundaries
3. preserve Main's ability to request more material
4. satisfy the user's material request
5. remove duplicate/redundant context and unnecessary LLM turns
6. only then optimize raw token count
```

### What this benchmark does not prove

One run does not prove that Rev5.8 is globally faster or cheaper. It proves that the new state model and projection boundary work under a real provider run without increasing the number of Main turns/tools. The next efficiency work should target **redundant hot-context representation**, not reduce objective truth, silently rank relevance, or lower the projection limit merely to improve a token metric.

## Why this comparison matters

The successful run analyzed a larger current repository and still required far less LLM work. The improvement therefore did not come from loading less of the project into the model by deleting relevant source. It came from preserving the relevant source as observable reality and letting directed capabilities answer mechanical questions without forcing Main into a long compensating search loop.

The repeated fixed prompt contract illustrates this clearly:

```text
earlier run: 42,521 locally estimated repeated fixed tokens across 20 turns
Rev5.7.7:     9,090 locally estimated repeated fixed tokens across 4 turns
```

The per-turn infrastructure cost remained in roughly the same range. The large gain came from **needing fewer inference cycles**, not from hiding more repository state or imposing an arbitrary low tool limit.

A long-term scaling target follows from this:

```text
LLM work      ≈ semantic/query complexity + relevant evidence
machine work  ≈ repository size / graph size / deterministic traversal
```

Repository growth should increase deterministic CPU/I/O much more readily than repeated Main-LLM context materialization.

## Current efficiency headroom

The Rev5.7.7 run also exposes a real remaining inefficiency:

```text
Evidence items:                       46
Evidence selected for Claim:           5
Evidence cited by Claim:               4
Structurally unreferenced Evidence:    41
Evidence amplification ratio:        5.75
Structurally unreferenced tool actions: 4 of 8
```

The hot-context projection kept this from exploding the prompt (`Evidence/Investigation` model-facing state was only about 3,087 locally estimated tokens), so canonical state growth did not recreate the old context-amplification failure.

That measurement is the Rev5.7.7 baseline. Rev5.8 changes the state model: locator/tool materializations are SourceRecords first, and only explicit Main selections become Evidence. The benchmark must now report SourceRecords and Evidence separately rather than treating `Evidence/Observation` amplification as one mixed metric.

The successful answer also demonstrated another evaluation requirement: **grounded truth and material task satisfaction are different properties**. Claim Review must verify not only that delivered claims are supported, but also that explicit requested deliverables were actually answered. Benchmark review should therefore inspect both grounding and material satisfaction rather than using a supported claim as the sole quality signal.

## Rev5.8 deterministic regression — objective projection and Evidence admission

Rev5.8 addresses the headroom exposed by the live Rev5.7.7 run without introducing a hidden relevance engine. The new regression contract is mechanical:

```text
capability executes an objective query over the full declared scope
→ SourceRecords materialize objective/citable results
→ bounded inline projection may expose continuation handles
→ Main decides which src-* records are semantically material
→ Runtime promotes only those explicit selections to Evidence
```

The implementation is regression-tested for these properties:

- `search_code` keeps the complete physical literal-match universe until after grouping;
- inline source ranges are diversified deterministically across files **before** `max_search_ranges` / `max_search_matches` bound model-facing materialization;
- `coverage_complete=true` may coexist with `projection_complete=false`: the search scope is complete even when additional objective ranges live behind a handle;
- tool success creates SourceRecords and leaves EvidenceLedger empty until Main explicitly selects a `src-*`;
- promotion is identity-preserving (`src-0002 → ev-src-0002`) and only the selected record is admitted;
- Investigation stores canonical Evidence IDs after explicit SourceRecord admission;
- Claim receives literal `request:rN` coordinates that are substrings, not parsed requirements;
- the Claim protocol rejects `satisfied + semantic_gap`, `gap` without concrete semantic debt, and `blocked` without Runtime grounding;
- Claim completion budget grows with observable request/packet size as well as answer size, without a semantic requirement counter.

These regressions matter because reducing the **Evidence count alone** would be cosmetic if Main still had to read the same broad search payload. Rev5.8 therefore changes both sides: objective capabilities bound/materialize the physical result more faithfully, and EvidenceLedger no longer receives every locator breadcrumb automatically.

The live Rev5.8 rerun above now complements these deterministic regressions. The deterministic suite establishes contract invariants; the live run shows how those invariants behave under an actual provider call. Neither is treated as a token-minimization contest.

## Directed reachability regressions

### Positive path regression

`parse_claim_review_response` originally motivated directed observation. Before directed reachability, a correct production-flow investigation required:

```text
9 Main turns
14 physical tools
31 Evidence items
64,797 provider prompt tokens
```

because Main reconstructed a long path node by node.

A live Rev5.7.1 run with the first directed implementation completed correctly with:

```text
8 Main turns
6 physical tools
5 Evidence items
6 Observations
26,264 provider prompt tokens
42,511 estimated physical tokens
64.16 s
```

The current deterministic regression is stronger than that first implementation:

```text
symbol_relations(symbol="parse_claim_review_response", query="reachability")
→ mechanically exhaust the finite resolved graph
→ materialize one objective root-to-target path when reachable
→ coverage.objective_complete=true for the positive query
→ do not expose unrelated dynamic frontiers after the path is established
```

Main no longer needs to guess a `max_depth` ladder such as `5 → 12 → 32`.

### Negative/incomplete regression

A separate `request_requires_write` benchmark checks epistemic discipline. The observed pre-hardening run required:

```text
11 turns
6 physical tools
5 Evidence items
37,113 provider prompt tokens
3 Claim passes
```

The important factual result was not “the symbol is impossible to execute.” It was:

> no productive static path was confirmed, while unresolved dynamic boundaries prevented an absolute impossibility claim.

Current behavior must preserve that distinction. If a negative reachability query still has a material physical/static boundary, `coverage.objective_complete` must not be used as proof of a stronger absence than the capability actually established.

## Mandatory behavioral regressions

### Simple repository question

```text
quantos tokens tem o projeto?
```

Expected shape:

```text
Main
→ count_tokens
→ Observation/Evidence
→ Final
```

No semantic router, workspace classifier or runtime-created Investigation target should be necessary.

### Reachability

- positive query: complete materialized path when structurally established;
- negative query: explicit incomplete/blocked coverage when unresolved boundaries can change the result;
- protected-source exclusions may never become false global absence;
- reachability does not label code `live`, `dead`, `legacy`, safe or removable.

### Protected-resource integrity

Normal source must remain observable regardless of identifiers or literals such as `token`, `password`, `api_key` or private-key-looking text.

Credential/private-key resources are protected by canonical resource identity, not content guessing:

- official protected path is restricted;
- symlink aliases cannot bypass it;
- hard-link aliases to the same protected physical resource cannot bypass it;
- public keys/certificates/generic public PEM material remain readable;
- `.env.example`, `.env.sample`, `.env.template` and `.env.dist` remain readable;
- search/Evidence coverage must say when protected resources were excluded;
- a stable protected-resource denial is resource-scoped and reusable rather than repeatedly re-executed;
- sandbox and Git content surfaces use the same protected-resource identity boundary.

### Core compatibility

The Core has one exact current contract. Historical/same-version aliases, alternate field names and migration bridges are rejected. Compatibility may exist behind adapters/capabilities only when all variants normalize into the same canonical Core representation.

### Writes

```text
Main patches
→ deterministic dry-run
→ explicit confirmation
→ WriteTransaction apply
→ compile/tests/full-output verification
→ success or rollback
```

No public tool may create a second route to real workspace mutation.

## Physical containment

Current default job envelope:

```text
backend request context <= 32768 tokens
prompt attempts         <= 90000 tokens
completion              <= 8000 tokens
physical total          <= 98000 tokens
```

The envelope is a fuse, not a target. Increasing it is not an acceptable fix for a loop that should have been eliminated by better observation, replay, failure scope or tool semantics.

## Interpreting benchmark results

A benchmark result is useful only if these questions are answered together:

1. **Was the factual conclusion correct?**
2. **Was the requested scope materially answered?**
3. **Was the answer grounded in current observations/runtime facts?**
4. **Did Coverage accurately describe what was and was not established?**
5. **Did the agent stop after the material property was established, rather than continue because budget remained?**
6. **Did repository growth mostly increase deterministic work rather than repeated LLM work?**

The goal is not a coding agent that always uses the fewest tokens. The goal is a coding agent that can be trusted to investigate the right property, expose its real uncertainty, and spend LLM work where semantic judgment is actually needed.

A benchmark must therefore never reward a run for being cheaper if it achieved that reduction by hiding objective matches, collapsing Coverage/frontier information, dropping provenance, removing continuation handles, or forcing Main to treat a bounded projection as complete reality. **Truthful and expandable observation is a feature worth paying modest context for.**
