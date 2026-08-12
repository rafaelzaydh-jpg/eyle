# Configuration — Eyle 2.7.5 Rev1.3

Runtime baseline: **Python 3.11+**. Older Python runtimes are not a compatibility target.

`config.json` is the strict current-release contract:

```json
{
  "app_version": "2.7.5",
  "config_schema_version": "2.7.5-r1.3",
  "revision": "rev1.3-task-memory"
}
```

Unknown fields and mismatched identity are errors. Removed fields are not aliases. Session, queue and project-memory schemas use the same `2.7.5-r1.3` clean-break identity.

## Physical containment

```text
llm.context_window_tokens 38000
agent.max_total_tokens    90000
agent.task_deadline_seconds 1800
```

`context_window_tokens=38000` is the hard per-call deployment ceiling for the current llama-server. Runtime reserves model-output and safety headroom inside that window.

There is **no cumulative `max_prompt_tokens` or `max_completion_tokens` contract**. The 90,000-token total fuse and deadline are physical runaway containment; they do not define semantic completeness or prescribe a strategy. Values above 90,000 are rejected by the current strict config contract. There is no fixed LLM-turn, LLM-call or tool-call quota.

## Claims

`agent.claims.mode` is `off`, `self_check`, or `verified`.

Claim uses a small `accept|challenge` contract. Its output artifact is bounded by schema: at most 3 independent blockers, at most 4 grounding coordinates per blocker, and one concise reason. The output-token reservation is derived internally from that canonical contract; `agent.claims.verifier.max_tokens` was removed and is rejected as an unknown field. `agent.claims.grounding.max_chars_per_item` still bounds each material excerpt. Zero grounding is reviewable but is not automatically wrong: Claim judges whether the actual answer requires current/external observation.

`self_check` reuses the Main model. `verified` requires an explicit verifier transport/model.

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
