# Configuration — Rev5.6

`config.json` is the strict current-release contract.

Required identity:

```json
{
  "app_version": "2.7.4",
  "config_schema_version": "5.6",
  "revision": "rev5.6-grounded-outcomes-docker-backend"
}
```

Unknown fields and mismatched identity are errors. Removed fields are not aliases.

## Physical Agent fuses

Current defaults:

```text
max_llm_turns          24
max_tool_calls         64
max_llm_calls          32
max_prompt_tokens      90000
max_completion_tokens  8000
max_total_tokens       98000
task_deadline_seconds  1800
```

`max_total_tokens=98000` is a physical per-message/job training envelope. Full prompt attempts count against it even when the provider reports cache hits; cache weighting is diagnostic only. `max_prompt_tokens=90000` and `max_completion_tokens=8000` are independent sub-fuses.

`llm.context_window_tokens` is capped at **32768** for the current Llama Server. The runtime subtracts the system prompt, output reservation and safety margin before compiling the user payload. Values above 32768 are rejected.

The worker derives only a technical hard-kill grace from the canonical task deadline; there is no second public job deadline.

## LLM transport

Structured Agent/Claim calls require strict JSON Schema. Current transports are OpenAI-compatible Chat Completions and Ollama, but either must support the canonical schema mechanism for structured calls.

`retry_max_attempts` is the single transient transport retry policy. There is no Agent-specific retry knob, structured protocol retry, capability downgrade or truncation retry.

## Claims

`agent.claims.mode` controls the single global verifier (`off`, `self_check`, `verified`). In `verified`, the verifier transport/model must be configured explicitly and be distinct from the main model endpoint/model pair.

## Writes/tests

`codar` controls write/test authority. Writes remain confirmation-gated, transactional and rollback-capable. Sandbox/network/process limits are physical safety policy only.

## General Agent sandbox

`agent.sandbox` configures the unrestricted `run_command` capability. It is physically separate from real-workspace mutation authority.

Default release policy:

```text
backend                 auto (Docker first; Bubblewrap fallback)
docker image            python:3.12-slim (pull if missing)
network                 enabled for run_command
timeout                  300s
memory                   2048 MiB
processes                256
snapshot                 writable, copied once per job
Docker container         one persistent container per job
real workspace writes    impossible through run_command
trusted_local/process    rejected for unrestricted execution
```

The sandbox may create/delete files, install packages/toolchains, compile and run arbitrary commands without confirmation. In Docker, both snapshot changes and container root-filesystem changes persist across `run_command` calls in the same job. Docker may auto-pull the configured base image when absent. Protected secret paths/content are omitted from the snapshot and the real workspace is never mounted read-write. A real workspace change still requires the canonical confirmed `WriteTransaction`.

When no strong backend is available, `run_command` returns `SANDBOX_UNAVAILABLE` with `retryable=false`. ExecutionContext marks that capability terminal for the current job and removes it from later callable projections. The task itself may still finish truthfully as `blocked`; Runtime Facts are available to Claim for grounding.
