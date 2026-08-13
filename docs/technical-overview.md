# Technical overview — Eyle 2.7.5 Rev1.5.1

## Decision loop

```text
compile prompt
  ↓
Main decision
  ├─ capability_calls → Registry → Provider → Observation/effect → Main
  ├─ await_user       → persist → request_context refinement → Main
  └─ complete         → coordinate/commitment validation → deliver
```

Confirmed capabilities also return to Main; confirmation is never terminal by itself.

## Layers

```text
eyle/core/          semantic session loop and optional Main commitments
eyle/contracts/     universal physical contracts
eyle/capabilities/  generic Registry/dispatch
eyle/runtime/       service, persistence, execution mechanics
eyle/host.py        provider-body assembly
eyle/providers/     domain implementations
```

Providers/capability infrastructure do not import Core.

## Registry

Registry accepts provider-local capability IDs and creates canonical `provider.local` names. It owns JSON-schema validation, availability, confirmation delegation, canonical result validation, effect coherence, Coverage/Frontier normalization, projection hooks and provider config delegation.

## Bundled Providers

`standard` owns workspace/code/Git/sandbox/test mechanics including confirmation-required `standard.workspace_transaction`.

`memory` independently owns persistent cognitive Memory through `memory.search` and `memory.store`.

## Result/effect contract

Canonical capability results contain:

```text
status, ok, executed, changed,
error_code, detail, retryable,
failure_scope, failure_resource,
physical_effect,
observations, coverage, frontiers
```

Physical effects are `{resource, operation, persistence, changed}`. Registry rejects mechanically incoherent provider results.

## Continuations

Pending schema `4` supports `capability_confirmation` and `await_user`. Provider confirmations bind to `provider_context_hash`. `await_user` answers are written to `AgentSession.request_context`, not concatenated into `request` and not downgraded to generic conversation background.

## HTTP retry

OpenAI-compatible transport preserves transient classification for HTTP 408/425/429/500/502/503/504 through backend error translation, so configured retry policy can actually run.

## Persistence schemas

- configuration/session: `2.7.5-r1.5.1`;
- Queue: `2.7.5-r1.4.3`;
- pending continuation: `4`.
