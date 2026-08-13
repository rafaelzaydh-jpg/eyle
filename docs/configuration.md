# Configuration — Eyle 2.7.5 Rev1.3.4

Runtime baseline: **Python 3.11+**. Older Python runtimes are not a compatibility target.

`config.json` is the strict current-release contract:

```json
{
  "app_version": "2.7.5",
  "config_schema_version": "2.7.5-r1.3.4",
  "revision": "rev1.3.4-fresh-claim-token-cleanup"
}
```

Unknown fields and mismatched identity are errors. Removed fields are not aliases. Session, queue and project-memory schemas use the same `2.7.5-r1.3.4` clean-break identity.

## Physical containment

```text
llm.context_window_tokens 38000
agent.max_total_tokens    90000
agent.task_deadline_seconds 1800
```

`context_window_tokens=38000` is the hard per-call deployment ceiling for the current llama-server. Runtime reserves model-output and safety headroom inside that window.

There is **no cumulative `max_prompt_tokens` or `max_completion_tokens` quota** and no fixed `claim_reserve_tokens`. The 90,000-token total fuse and deadline are physical runaway containment; they do not define semantic completeness or prescribe strategy. Once a Candidate Final exists, Claim sizes its fresh review packet/output ceiling against the actual physical headroom remaining. Values above 90,000 are rejected by the current strict config contract. There is no fixed semantic LLM-turn or tool-call quota.

## Claims

`agent.claims.mode` is `off`, `fresh`, or `verified`.

Claim uses the strict `accept|challenge` protocol. Each issue is exactly `{kind,grounding_refs,reason}`; there is no semantic quota on issue count, reference count or reason length. Normal provider/context/output ceilings remain physical limits. `agent.claims.grounding.max_chars_per_item` bounds each selected Material excerpt. Zero grounding is reviewable but is not automatically wrong: Claim judges whether the Candidate Final actually requires observed current/external facts.

`fresh` is the default and starts a new request using Main's transport/model with no Main message history. Its semantic packet contains exactly `request`, `candidate_answer` and `observed_material`. `verified` requires an explicit distinct verifier transport/model. A first semantic challenge permits one Main revision; a second returns `CLAIM_CHALLENGE_UNRESOLVED`. The removed `self_check`, verifier `max_tokens`, Claim reserve and anchor/runtime-fact fields are rejected rather than aliased.

## Sandbox

`agent.sandbox` configures `run_command`:

```text
backend             auto (Microsandbox → Docker → Bubblewrap)
imagem_oci          python:3.12-slim
network             public profile for unrestricted Microsandbox run_command (`Network.from_profiles("public")`)
timeout             300 s
memory              2048 MiB
snapshot            writable copied workspace; bind-mounted on Linux/macOS, guest-staged on Windows
real workspace      never mounted read-write
```

`microsandbox==0.6.8` is pinned as the preferred embedded microVM backend. Its platform wheel bundles the `msb` runtime; no long-running sandbox daemon is part of the Eyle contract. On Windows the current Microsandbox path requires Windows Hypervisor Platform (WHP) and remains a provider-preview dependency. One microVM persists for the physical `run_command` job. Supervised `run_tests` keeps its existing automatic backend policy because a generic OCI image does not imply pytest/npm availability; Microsandbox can be selected there explicitly with a test-capable `imagem_oci`, and then uses a separate one-off VM plus `Network.none()` whenever `bloquear_rede=true`.

A strong sandbox is required for unrestricted `run_command`. If the Microsandbox SDK is present but the runtime/virtualization cannot create its VM, Eyle reports that physical failure rather than silently changing backend mid-attempt. Docker and Bubblewrap remain explicit/automatic fallbacks when Microsandbox is not installed. `imagem_docker` is not an alias and is rejected by the strict config boundary.

## Writes

`codar` controls editing/test authority. Real workspace writes always use confirmed `WriteTransaction`. Investigation status is not a write permission gate. Sandbox writes never count as real workspace writes.

## Provider adapters

External provider variability may be normalized behind adapters, but Core keeps one current Agent schema and one current Claim schema. No Core compatibility downgrade chain is allowed.


### Microsandbox 0.6.8 API closure

The Runtime targets the pinned Python SDK contract directly: it bootstraps the local runtime with `is_installed()`/`await install()`, uses `Network.from_profiles("public")` for ordinary `run_command`, and `Network.none()` only for explicitly network-isolated supervised execution. The removed/historical `Network.public_only()` helper is not part of the active integration.
