# Benchmark and regression contract

Benchmarks detect loss of truth, physical safety or useful agency. Lower token use is not an improvement if it hides objective reality; more semantic gates are not an improvement if Main could safely decide for itself.

## Rev1.3.4 deterministic gate

```bash
python -B -m eyle.devtools.release_identity
python -B -m pytest -q
python -B -m compileall -q eyle llm web main.py
node --check web/static/app.js
```

`release_identity` is intentionally run before commands that generate bytecode/cache state; those generated artifacts must be removed before release packaging.

Rev1.2.3.1 preserves the physical-contract and operational-self-observation regressions and adds Claim-contract/transport regressions:

- zero-grounding Final is Claim-reviewed instead of blindly bypassing review;
- Main is not given domain-specific audit/search strategy by Core;
- Investigation state does not block Final or an otherwise valid write;
- an invalid Investigation update does not cancel an independent valid action;
- repeated observation is memoized rather than duplicated or converted into `OBSERVATION_REPLAY_LOOP`;
- one invalid tool sibling does not cancel valid independent siblings;
- recoverable capability failures return to Main instead of semantically killing the task;
- Claim stays a compact `accept|challenge` critic;
- continuation never exposes Runtime-private `handle:*` coordinates;
- the model-call context ceiling is exactly 38,000 tokens across configuration/budget code;
- cumulative prompt/completion budgets and fixed turn/call/tool quotas are absent; the 90k total-token fuse and deadline remain physical containment.
- old unrelated Observation rows are omitted from later Main prompts while immediately fresh rows remain visible;
- Investigation-pinned Material and every open Frontier survive delta projection;
- the Main-facing Material directory is bounded to pinned/pending/recent coordinates instead of replaying the whole ledger;
- memoized replays return coordinates plus bounded recall excerpts rather than full prior payloads;
- Main has no fixed Claim reserve; Claim review fits its fresh packet to actual physical headroom after Candidate Final;
- Observation contains no public-capability-specific extraction/signature branches;
- grounding-producing capabilities own their Material extraction;
- non-file `locator` Material registers canonically;
- Claim has no Investigation coordinate or filesystem freshness implementation;
- DecisionLedger has no rejection fingerprint/count/prescriptive property protocol;
- one large continuation snapshot is reused across multiple Frontier cursors and garbage-collected after the final cursor;
- a complete physical search Coverage may coexist with an open materialization Frontier;
- continuing a search Frontier materializes real source-capability Material;
- every public capability explicitly owns execution/signature/observation/Coverage/Frontier hooks;
- Observation contains neither public capability names nor file/filesystem primitives;
- Sandbox snapshots omit repository symlinks and enforce cwd/timeout/output boundaries;
- multi-file transactions prove rollback or explicitly report rollback failure.

Current deterministic suite in this build: **316 passed, 1 Flask-dependent skip** in the available offline build environment. `web/` is still Python-compiled and `web/static/app.js` is checked by Node.

## What to report for live runs

At minimum record task outcome, Main turns, physical tool calls, LLM calls, physical token accounting, Observation/material count, cache/replay count, material Coverage/Frontier behavior, Claim outcome and failure code when applicable.

## Historical lesson

Rev5.x repeatedly converted model mistakes into new semantic machinery. Later clean breaks repeatedly removed that machinery. Rev1.1 made the semantic boundary explicit; Rev1.2 cut capability knowledge out of Core; Rev1.2.3 keeps the mature physical observation contract and adds bounded factual self-observation:

> **Eyle constrains effects, not thought.**

Physical invariants belong in Runtime. Semantic recovery belongs to Main. Claim challenges conclusions but does not become another planner.

## Release policy

Deterministic tests and extracted-artifact verification are blockers. Live-provider runs are additional evidence, not a reason to add compatibility or semantic cages to Core.

### Current Claim closure regressions

- Fresh Claim receives only Request, Candidate Final and selected observed Material; no Main history/Investigation/Tasks/runtime event packet is exposed.
- Claim schema has strict shape/coordinate validation but no semantic issue/reference/reason quotas.
- There is no fixed Claim token reserve or configurable verifier `max_tokens` field.
- A second semantic challenge after one Main revision fails as `CLAIM_CHALLENGE_UNRESOLVED` instead of looping.
- One `MODEL_OUTPUT_TRUNCATED`/structured-protocol recovery is allowed; a second failure stays fail-closed.
- Interaction prompt explicitly permits ordinary conversation to return `final` without fabricating a formal task.
- Transport timeouts are recorded as physical attempts rather than `preflight_blocked`.


- page materialization must not deepcopy unselected snapshot items;
- malformed Coverage must fail the capability contract;
- >32 identical symbol definitions must produce exhaustive Coverage plus Frontier;
- snapshot exhaustion must not imply source-materialization completeness;
- generic capability dispatch must use registry hooks rather than tool-name branches;
- Claim has no Runtime-event compaction layer;
- Sandbox timeout must kill child process trees and container/backend lifecycle must fail closed.

- canonical Main projections expose physical facts directly; `operational_feedback` remains removed;
- task-wide physical token fuse defaults to and is capped at 90,000.
