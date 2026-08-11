# Rev5.7.1 benchmark contract

Benchmarks measure whether work follows task necessity rather than infrastructure pressure.

## Mandatory simple regression

```text
quantos tokens tem o projeto?
```

Expected:

```text
Main LLM
→ count_tokens
→ Observation/Evidence
→ Final
```

Acceptance: no Investigation unless the Main LLM independently declares real debt, one physical `count_tokens`, no router/classifier, no workspace classification, no semantic phase scheduler.

## Complex audit regression

For a usage/reachability audit:

- Main LLM creates only material Investigation debts it identifies;
- Runtime never creates a target;
- open declared targets block Final;
- Claim can challenge support/closure or return omitted scope with `target_id=null`;
- Main LLM decides whether that feedback requires a target or only answer correction.

## Resource interpretation

Semantic sufficiency is never inferred from token count, but inference executions are physically contained. One message/job may spend at most **98000 physical estimated tokens**, with at most 90000 prompt and 8000 completion tokens, and each backend request must fit the 32768-token Llama Server window. Exhausting a fuse fails the task; the Main LLM does not earn extensions.

Record completion/accuracy, physical tools, replays, repeated rejected decisions, prompt duplication, Investigation use, post-sufficiency work and Evidence/Observation growth. The audit regression should demonstrate that the Main LLM creates persistent Investigation debt for unresolved multi-turn candidate conclusions rather than consuming the entire envelope in broad search.


## Directed Observation regression

The motivating Rev5.6.2 production-flow benchmark for `parse_claim_review_response` completed correctly but required 9 Main turns, 14 physical tools, 31 Evidence items and 64,797 provider prompt tokens because the Main LLM reconstructed a long structural path node by node. Rev5.7 does not declare those numbers a semantic budget; they are the observed baseline for retrieval efficiency.

Mandatory deterministic regression:

```text
symbol_relations(symbol="parse_claim_review_response", query="reachability")
→ one objective structural path from a detected Python entrypoint to the target
→ coverage.objective_complete=true
→ no unrelated dynamic frontier after a positive path is established
```

The tool-level regression proves that the path can be materialized in one physical observation. An end-to-end LLM benchmark must still verify that the Main actually chooses the directed query and therefore reduces turns/tool hops/prompt accumulation without changing the factual conclusion. No hard semantic limit such as “must use <= N tools” is introduced.

Negative/incomplete regression: when no path is established and unresolved dynamic resolution can still matter, `coverage.objective_complete=false`, a bounded `frontier` is returned, and the larger continuation remains behind an opaque handle. `expand_observation(handle)` materializes that snapshot without deciding whether it matters.

## Observed Rev5.7.1 end-to-end baseline

A live Rev5.7.1 run of the `parse_claim_review_response` reachability task completed correctly with:

```text
8 Main turns
6 physical tools
5 Evidence items
6 Observations
26,264 provider prompt tokens
42,511 estimated physical tokens
64.16 s
```

The run exposed the next efficiency targets without invalidating the current result. The first directed query was explicitly requested with `max_depth=10`, while the established path required 12 hops. Main then expanded an unresolved-dynamic continuation in three pages, provisionally closed the Investigation too early, was correctly challenged by Claim, attempted one invalid continuation ID (`ev-0004` as if it were a Handle), and finally repeated directed reachability with `max_depth=12`, which returned `objective_complete=true`, `objective_result=reachable` and the complete path.

This baseline motivates future regressions for mechanical auto-depth/continuation, narrower root-to-target frontier corridors, unambiguous identifier namespaces and stronger grounding discipline around incomplete Coverage. These remain efficiency/correctness targets rather than semantic hard limits on tool count or turns.

## Rev5.6.1 inherited contract-fidelity regressions

The following checks are mandatory before broader audits:

1. `symbol_relations(direction="callers")` is rejected during tool validation with `INVALID_ARGUMENT` and `executed=false`; it must never reach the relation scanner.
2. `symbol_relations` requests that differ in `direction`, `include_text_references`, `max_depth`, `max_edges`, `roots`, `path` or `symbol` have distinct observation identities. In particular an `incoming` result must never replay for an `outgoing` request.
3. Claim packets expose complete literal refs (`answer:*`, `evidence:*`, `runtime:*`, `investigation:*`), and the local structured parser rejects shortened refs such as `a1` before semantic normalization.
4. Model-facing `symbol_relations` rows are bounded while full relation counts and coverage remain visible; bounded display must never imply bounded underlying reality.
5. If a task explicitly permits uncertainty (for example, “report it if proved; otherwise say it was not proved”), one material non-redundant investigation may legitimately end in a grounded not-proven result. An open Investigation is debt to resolve or dismiss, not a mandate to sample arbitrary additional candidates until the physical budget is exhausted.
6. `workspace_epoch` remains an Eyle-owned mutation/replay coordinate only. Benchmarks must not assume it fingerprints external workspace changes, and no AST/project-graph cache may rely on it as global freshness authority.


## Rev5.6.2 surgical regressions

1. With 4320 completion tokens remaining, a configured Agent ceiling of 3600 and a mandatory Claim reserve of 900, the next Agent call must be sent with an effective ceiling of 3420 rather than fail preflight. The 8000 completion fuse itself is unchanged.
2. If only the mandatory downstream reserve remains, the call still fails with `MAX_COMPLETION_BUDGET_INSUFFICIENT`; adaptive fitting never borrows the Claim reserve.
3. For a task that asks the Main LLM to choose one candidate and establish whether a property holds, either established polarity completes that candidate unless the request explicitly requires one polarity. A Claim gap must be repaired against its `required_property` before switching candidates.
4. `symbol_relations` keeps literal text references off by default; Main guidance requests them only when they can discriminate the active property.
5. `if __name__ == "__main__": main()` is exposed as an objective `python_main_guard` edge from the module root to the local entry function even when other files define functions with the same name. This edge is structural, not a liveness verdict.
6. Public decision history for Claim recovery exposes bounded `required_properties` so benchmark logs can distinguish vague Claim debt from Main-LLM failure to follow precise debt.

These are contract/reasoning regressions, not task classifiers. Runtime does not decide which symbol is interesting, whether code is productive, or when source facts are semantically sufficient.



## Rev5.7.1 contract regressions

1. Every executed tool result has the canonical observation fields `observations`, `coverage`, `frontiers` and `handles`, even when empty.
2. Every registry entry exposes exactly one domain-neutral `effect` class: `observe`, `execute` or `mutate`; this is physical metadata, not task routing.
3. `symbol_relations(query="relations")` preserves the local relation behavior; `query="reachability"` has distinct ObservationLedger identity.
4. Directed positive reachability returns the complete materialized path and omits unrelated unresolved-dynamic frontiers.
5. Directed unresolved negative reachability reports `objective_complete=false` and exposes continuation handles rather than dumping all unresolved sites into model context.
6. Snapshot handles are rejected with `HANDLE_STALE` after the workspace epoch changes.
7. `expand_observation` is domain-neutral and only materializes the stored bounded snapshot continuation.


## Live Rev5.7 failure that motivated 5.7.1

A real Main-LLM run of the motivating `parse_claim_review_response` task failed with `MAX_PROMPT_TOKENS_EXCEEDED` after 17 turns, 15 physical tool executions, 24 Evidence items and 61,986 provider prompt tokens. The first directed reachability observation returned `inconclusive` and exposed 134 project-global unresolved dynamic sites, causing manual fallback exploration and replay. Rev5.7.1 treats this as two separate regressions: P0 must resolve objective import/alias edges and only expose query-shaped frontiers; P1 must keep canonical state complete while bounding the model-facing working set.
