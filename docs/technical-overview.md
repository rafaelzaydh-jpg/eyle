# Technical overview — Eyle 2.7.5 Rev1.3

## Request path

A large task normally loops freely:

```text
Main → Runtime capability → Observation → Main
```

When Main chooses to conclude and Claim is enabled:

```text
Main → provisional Final → Claim → accept → User
                         └→ challenge → Main
```

Claim also reviews a zero-grounding Final. It may accept a pure reasoning/writing answer or challenge a current-state assertion that lacks material support.

## Canonical owners

```text
AgentSession
├─ ObservationLedger  physical material/Coverage/Frontier
├─ Investigation      Main-owned epistemic notebook
├─ Tasks              Main-owned recursive intentional state
├─ DecisionLedger     observable decision history
├─ ClaimReview        latest compact critique
├─ WriteTransaction   supervised real-workspace mutation
└─ conversation background
```

`ExecutionContext` owns physical deadline/call/tool/turn/total-token containment and transport accounting. It does not own semantic progress.

## Capability result

Capabilities return one canonical physical envelope containing status plus `observations`, `coverage` and `frontiers` slots. Every registry entry explicitly owns the hook surface that produces those fields, even when a capability intentionally returns no Material or Frontier.

Capabilities emit generic observation candidates. Observation registers them as `mat-*` using `locator + content_hash` plus optional opaque `source_version`; file-specific extraction, freshness and rehydration stay in the file capabilities.

Coverage uses `scope/examined/complete` with optional physical `boundaries/facts`. Frontier is orthogonal: a scan may be physically complete while materialization still has a continuation.

Large Frontier payloads are stored exactly once in Runtime-private snapshots. Public `fr-*` refs resolve to lightweight handles/cursors, and source capabilities materialize continuation pages.

## Cached observation

Repeated objective work at the same physical state can be rehydrated from canonical Observation. A cache hit increments replay telemetry but does not append a duplicate physical Observation and does not create a specialized semantic-loop fatal.

## Investigation

Main may create, revise, establish, dismiss or ignore Investigation entries. Runtime checks shape and any supplied `mat-*` identities; it does not use Investigation status as a permission gate for Final or writes.

If an Investigation update is invalid but the same model turn also contains an independent valid capability action, Runtime rejects only the invalid update and continues the valid action.

## Claim

Claim receives a bounded packet of request coordinates, provisional-answer anchors, selected Observation material and compact physical Runtime facts. Investigation is deliberately absent.

Its contract is deliberately small:

```json
{
  "verdict": "accept | challenge",
  "issues": []
}
```

A challenge contains only blocking issues. Claim cannot call tools, prescribe which tool Main should use, or mutate Main state. The protocol is physically bounded to 3 independent issues, 4 coordinates per issue and one concise reason; the budget is derived from that contract rather than user configuration.

## Search and relations

`search_code`, `find_symbol`, `symbol_relations`, `read_file` and other capabilities expose deterministic physical contracts. Core does not encode an audit/search recipe. Main chooses and composes capabilities from their schemas and descriptions.

A Frontier is available continuation, never an instruction to continue.

## Tool failures

Schema rejection, protected-resource denial, sandbox failure and other physical capability failures are surfaced as factual results when safe continuation is possible. Runtime prevents the prohibited effect; it does not infer that the whole task has semantically failed.

## Model window

The current llama-server deployment is physically limited to **38,000 context tokens per call**.

Runtime compiles each Main/Claim request inside that window, reserving output/safety headroom. The old cumulative `max_prompt_tokens` and `max_completion_tokens` controls are absent. A task-wide `max_total_tokens=90,000` fuse and deadline provide runaway containment. Rev1.3 has no fixed LLM-turn, LLM-call or tool-call quota.

## Diagnostics

Execution trace is an internal projection built from observable canonical state. It is not a Main capability and does not expose chain-of-thought, raw prompts or raw model responses.

## Providers

Provider variability stays behind adapters. OpenAI-compatible and Ollama transports normalize into the same current Agent/Claim contracts. Core contains no compatibility downgrade chain.

## Rev1.3 intentional-state closure

Main now returns `{action,investigation_updates,task_updates}`. Tasks are persisted in `AgentSession.tasks` and validated by `eyle/core/tasks.py`. The contract is intentionally narrow: recursive parent relation, `open|completed|dropped`, and a result for closed tasks. Runtime validates shape/parent/cycle integrity only. There is no task scheduler, auto-completion, Final gate, priority, focus model or generic memory graph.

The former physical run identifier `task_id` is renamed `execution_id` in AgentSession/ExecutionContext/service boundaries so semantic Tasks have one unambiguous meaning.

The Rev1.2.3.2.2 Microsandbox physical closure remains unchanged beneath this semantic addition.

- Main receives bounded `operational_feedback` derived from canonical runtime facts, including recent problems, replay-only activity, available Material, selected Final grounding, open Frontiers, workspace epoch and physical token headroom.
- The projection never prescribes retry/stop/replan; Main remains sole semantic authority.
- `max_total_tokens` defaults to and is capped at 90,000 for this release.

- Coverage is normalized and validated at the capability execution boundary; arbitrary parallel Coverage dialects are rejected.
- Frontier pagination retains a single snapshot and copies only the requested page slice.
- `find_symbol` exhausts its safe source scope, reports files/matches examined, and exposes excess observed locations through Frontier instead of silently stopping at 32.
- Continuation Coverage separately reports snapshot exhaustion and source materialization success.
- Capability-specific normalization, memoization containment, public/model projection, continuation and freshness are owned by registry hooks; generic dispatch contains no capability-name branches.
- Claim compacts Runtime facts from the universal envelope/Coverage/Frontier shape rather than code/file-specific field names.
- `backend=auto` prefers Microsandbox before Docker/Bubblewrap. One Microsandbox microVM persists per unrestricted command job. Supervised tests use a separate one-off Microsandbox VM only when that backend is explicitly configured with a test-capable OCI image. Eyle always starts from a disposable snapshot: Linux/macOS bind-mount it at `/workspace`; native Windows copies it into the VM-private rootfs with `Sandbox.fs.copy_from_host`, avoiding the Microsandbox 0.6.8 Windows passthrough-fs EACCES/ELOOP defects. The real workspace is never a writable guest mount.
- Sandbox regression coverage includes Microsandbox bind and Windows guest-staging transports, network/rlimit/lifecycle behavior, child-process cleanup, explicit backend fallback, Docker initialization failure and persistent-container cleanup.


### Microsandbox 0.6.8 API closure

The Runtime targets the pinned Python SDK contract directly: it bootstraps the local runtime with `is_installed()`/`await install()`, uses `Network.from_profiles("public")` for ordinary `run_command`, and `Network.none()` only for explicitly network-isolated supervised execution. The removed/historical `Network.public_only()` helper is not part of the active integration.
