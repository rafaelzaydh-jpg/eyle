# Verification — Rev4.0.0

Release verification checks the **current contract**. It is not a compatibility test for every historical runtime shape.

## Fast path

From the repository root:

```bash
make verify
```

The Makefile runs the current release identity verifier, Python compilation, Eyle tests, Adapter tests, and JavaScript syntax check.

## Individual commands

```bash
python -B -m eyle.devtools.release_identity
python -m compileall -q eyle llm server web tests main.py
python -m pytest -q
python -m pytest -q server/tests
node --check web/static/app.js
```

For a publication artifact:

1. remove generated caches/runtime state;
2. build the archive;
3. extract it into a fresh directory;
4. rerun release identity and relevant tests against the extracted copy.

This catches accidental dependencies on a working tree.

## Release identity gates

The verifier/tests must establish:

- exact app/config/revision identity for Rev4.0.0;
- current Session/pending/execution schemas only;
- Memory Graph v12 only in Runtime;
- v11 → v12 available only through the explicit devtool;
- `eyle.providers.standard` as the canonical bundled provider;
- no `standard_impl` compatibility package/facade;
- no dynamic export path that restores removed providers/contracts;
- no removed generated-token fuse or cognitive deadline;
- no fixed conversation-message snapshot ceiling;
- no automatic Temporary/global Memory projection;
- no removed search/file cognitive paging aliases;
- current public capability fields only;
- current Adapter local transport fields only.

## Structured provider-boundary gates

Rev4.0.0 retains the provider-boundary requirements and additionally requires:

1. Eyle supplies one caller JSON Schema for structured output.
2. Adapter delivers that schema to the provider as the representation authority.
3. Core does not duplicate the same wire contract as another provider-facing textual authority.
4. Safe mechanical recovery may only recover representation, not Eyle meaning.
5. Adapter validates against the same caller-supplied schema.
6. Adapter performs at most **one** format-only repair.
7. Repair context contains only:
   - schema;
   - previous candidate;
   - validation errors.
8. Repair does not replay conversation, Memory, tools, Task state, or the full Eyle prompt.
9. `finish_reason=length` is reported as truncation and does not start a format repair.
10. Adapter contains no Eyle-specific alias table for historical ECC/Memory forms.
11. Adapter reports transport/usage/repair telemetry mechanically.

## Conversation gates

Current regression coverage should prove:

- current request is the final provider `user` message;
- current request appears exactly once;
- recent conversation uses native `user` / `assistant` roles;
- conversation materialization obeys token budget;
- materialized/omitted counters are observable;
- immediate references remain resolvable;
- switching topics does not cause the previous answer to become the active request;
- complete-history negative lookup does not substitute an unrelated fact.

Representative behavioral sequences include:

```text
money -> "it"
topic switch -> return to money -> "it"
```

and:

```text
context-heavy answer
-> "hi"
-> answer the new "hi", not the previous task
```

## Self-identity gates

Tests should distinguish:

```text
"analyze your code"
-> source="eyle"

"analyze the project"
-> source="workspace"
```

Self/internal analysis must use observable source/Runtime/log state, not claim access to hidden chain-of-thought.

## Memory gates

Regression coverage must prove:

- invalid `memory_delta` cannot invalidate an otherwise valid ECC decision;
- no paid LLM retry occurs solely to rescue Memory;
- Memory Graph v12 preserves `domain` and `context_key`;
- explicit activation remains available;
- activated bodies materialize once through `memory_view`;
- activation observations stay compact;
- growing the graph does not proportionally grow a trivial prompt;
- nonmaterialized nodes remain reachable by recall/activation/paging;
- v11 is rejected by Runtime before explicit migration and accepted afterward.

## Execution-progress gates

Provider wire problems and valid Eyle execution loops are separate.

Tests must prove:

- malformed provider representation is handled at the Adapter boundary;
- an exhausted Adapter repair can lead to one fresh Eyle decision while Session/observations remain intact;
- repeated wire failure without execution progress cannot recurse indefinitely;
- repeated valid action/result fixed points produce `NO_PROGRESS` and block the exact action in the current reality;
- repeating a blocked fixed point is rejected mechanically as `ECC_FIXED_POINT_BLOCKED` without another physical execution;
- the logical task remains alive and can recover via an open Frontier, existing Evidence, another scope/operation, or conclusion;
- real new observations/effects/Task transitions reset fixed-point state;
- long cognition that keeps producing new information is not truncated by a hidden `MAX_TURNS`.


### Rev3.7.8 recoverable-continuity gates

Regression coverage must additionally prove:

- `recoverable_execution` is Runtime-owned, non-interactive, and does not consume a human confirmation slot;
- a recoverable checkpoint persists `AgentSession`, hot pending observations, Observation Ledger, Evidence, Frontiers, `reality_epoch`, execution-progress blocks, and execution/token continuity;
- rehydration by the same stable `execution_id` preserves a blocked fixed point instead of making the same action physically eligible again;
- entering budget salvage with a stable execution id creates at most one salvage checkpoint for that execution continuity state;
- a restarted Service can discover and resume a persisted recoverable checkpoint automatically;
- mechanical coverage exposes exact/merged physical file ranges and open Frontiers without judging semantic relevance or sufficiency;
- execution-convergence signals are mechanical counters only; Runtime does not classify the investigation as wandering, relevant, sufficient, or complete;
- long-file regressions keep targets beyond the first 400-line materialization reachable at 2k and 10k scales;
- `read_file` continuation snapshots are bound to the exact whole-file source revision that created them;
- `search_code` pending live ranges are bound to the exact source revision of each file;
- external source drift before continuation returns `FRONTIER_SOURCE_REVISED`, marks the Frontier stale, and never mixes bytes from different revisions;
- historical Evidence/Material remains intact after source drift;
- mechanical file coverage never merges ranges from different source revisions;
- persisted continuation binding uses stable Host-owned `provider_identity_hash`, not mutable provider context;
- one `execution_id` has at most one current `recoverable_execution` checkpoint;
- recoverable checkpoint replacement is atomic and `checkpoint_generation` is monotonic.

## Build and workspace gates

Persistent-write tests must cover:

- project-root confinement;
- protected-resource rejection;
- dry-run/exact proposal;
- confirmation where required;
- stale-state/hash rejection;
- atomic transaction behavior;
- rollback;
- post-write re-observation;
- sandbox staging/promotion hash verification;
- `merge` vs explicit `mirror` behavior.

## Service and concurrency gates

Service/queue tests should verify:

- conversation/job snapshot ordering;
- atomic message/job state transitions;
- worker heartbeat/staleness handling;
- pending interaction continuity;
- current execution diagnostics under concurrency.

## Observability gates

User-visible diagnostics should make it possible to distinguish:

- normal cognition;
- continuation;
- fresh wire retry;
- Adapter format repair;
- provider transport failure;
- model truncation;
- Memory rejection;
- recoverable fixed-point blocking/checkpoint/resume.

Token diagnostics must preserve provider-reported usage as the ledger authority while keeping local component estimates clearly diagnostic.

## Environment-sensitive tests

Sandbox/process-isolation tests require an environment that does not inject unrelated Python startup hooks or exhaust process limits before the tested command starts.

A host-environment failure should be reported separately. Runtime security must not be weakened merely to make an invalid test host pass.

Web security tests may require Flask and the current web dependencies to be installed.

## Documentation gate

Current behavior belongs in:

- root `README.md`;
- `docs/`;
- `server/README.md`;
- security/project-policy documents.

Historical runtime behavior belongs in:

- `CHANGELOG.md`;
- Git history;
- explicit migration tooling when still required.

Documentation must not describe removed compatibility paths as active behavior.

## Rev4 cognitive-surface gates

Release tests must prove:

- no current monolithic `ecc` structured profile;
- Navigation has no detailed operation schema;
- Explore exposes only observe/execute operations;
- Build exposes only mutate operations;
- trivial Navigation can conclude in one cognition without Task creation;
- Runtime never auto-selects an active Task;
- Active Task is exact-ID projection only;
- sidecar failure does not veto a valid primary cognition;
- `active_task_id` and `cognitive_surface` survive checkpoint serialization;
- Build returns to Navigation after a mutation attempt;
- Rev3.7.8 reality-bound recovery regressions remain green.
