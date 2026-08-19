# Configuration — Rev4.0.0

Eyle uses a **current-contract** configuration policy. `config.json` is validated against the exact active schema; removed, unknown, or historical fields are not silently promoted or ignored.

## Release identity

The accepted identity is:

```text
app_version = 2.7.5
config_schema_version = 2.7.5-r4.0.0-ecc
revision = rev4.0.0-ecc
```

A different identity fails closed.

## Configuration surfaces

Eyle intentionally separates runtime configuration from provider credentials.

| Location | Owns |
|---|---|
| `config.json` | Eyle Runtime, context, worker, web, sandbox, telemetry |
| `server/.env` | DeepSeek Adapter/provider credentials and transport |
| `workspace/` | user project content |
| `memory/` | persisted Eyle Memory/runtime state |
| `context/` | runtime/benchmark artifacts |

Provider credentials should never be placed in `config.json` or committed to the repository.

## LLM Runtime configuration

Current `llm` fields include:

| Field | Meaning |
|---|---|
| `base_url` | local Adapter URL; default `http://127.0.0.1:8080` |
| `model` | model identity expected by Eyle diagnostics |
| `temperature` | provider generation temperature requested by Eyle |
| `provider_token_budget_per_message` | provider-accounted ledger for one logical user-message execution |
| `context_window_tokens` | physical context budget for one LLM call |
| `connect_timeout_seconds` | connection timeout |
| `read_timeout_seconds` | read timeout; `null` means no Eyle-side read timeout |
| `retry_max_attempts` | physical transport retry attempts |
| `retry_base_delay_seconds` | initial retry backoff |
| `retry_max_delay_seconds` | maximum retry backoff |
| `retry_jitter_seconds` | retry jitter |
| `max_concurrent_requests` | physical concurrent LLM requests |
| `cooldown_seconds` | configured cooldown between provider calls |
| `retry_read_timeouts` | whether read timeouts are retried |
| `stream_responses` | local Eyle streaming behavior |
| `reasoning_mode` | current provider reasoning mode |
| `adapter_status_timeout_seconds` | local Adapter status/preflight timeout |

Provider `usage.total_tokens` is authoritative. `prompt_tokens + completion_tokens` is a fallback only when the provider omits a total.

If potentially billable provider usage cannot be established safely, execution fails closed rather than guessing the remaining ledger.

Rev3.7.6 adds no additional semantic prompt-size or generated-output ceiling. Token efficiency comes from materializing only the required state and avoiding duplicate representation/context in repair paths.

## Context materialization

`context_engine` controls what physically fits into a cognition packet:

```json
{
  "safety_margin_tokens": 500,
  "chars_per_token_fallback": 3,
  "conversation_materialization_tokens": 1200,
  "observation_materialization_tokens": 2200,
  "runtime_feedback_materialization_tokens": 320
}
```

These are presentation budgets, not semantic relevance rules.

There is no:

- fixed `MAX_HISTORY_MESSAGES`;
- automatic global/Temporary Memory projection;
- semantic context ranker;
- fixed number of observations;
- hidden topic router.

Omitted content remains reachable through the appropriate conversation/Memory/Material/Frontier path.

## Web API

The `web` section contains the user-facing API token and local rate limits.

```json
{
  "api_token": null,
  "rate_limit": {
    "requests": 180,
    "auth_failures": 10,
    "window_seconds": 60
  }
}
```

When the server is exposed beyond loopback, use network/firewall controls and an HTTPS reverse proxy. The bundled Flask development server does not provide transport encryption.

## Standard provider

The bundled provider configuration lives at:

```text
providers.standard
```

Its canonical implementation package is:

```text
eyle.providers.standard
```

The provider owns workspace/tool mechanics, including the sandbox and test execution configuration.

### Sandbox

Relevant fields include:

- `backend`;
- `bloquear_rede`;
- `timeout_segundos`;
- `cpu_segundos`;
- `memoria_mb`;
- `max_processos`;
- `max_arquivos_abertos`;
- `max_saida_kb`;
- `max_arquivo_mb`;
- project-copy size/count limits;
- CPU allocation;
- trusted-local fallback policy;
- OCI image.

Sandbox limits are physical safety/resource controls. They are not reasoning limits.

### Tests

The Standard provider can expose configured test commands for Python and Node projects. These commands execute through the configured sandbox policy.

## Interaction

`confirmacoes.expiracao_segundos` controls how long a pending physical interaction/confirmation remains valid.

Human waiting time is not a cognition deadline and does not reset the provider-token ledger.

## Worker and queue

The `worker` section controls job execution mechanics such as:

- heartbeat interval;
- queue-error backoff;
- invalid-job reservation tolerance;
- parallel job count;
- per-job process isolation;
- stale-worker detection;
- head-of-line blocked detection;
- multiprocessing context.

These are Service/Runtime mechanics, not semantic planning settings.

## Telemetry

```json
{
  "enabled": true,
  "window_seconds": 3600
}
```

Telemetry aggregates execution facts such as job duration, provider usage, failure classes, and runtime behavior. User-facing diagnostics intentionally omit hidden chain-of-thought, raw prompts, raw provider responses, and protected content.

## Adapter configuration

Provider-specific settings live in `server/.env`.

Start from:

```bash
cp server/.env.example server/.env
```

Current variables include:

```dotenv
PROVIDER_PROFILE=deepseek_v4
UPSTREAM_BASE_URL=https://api.deepseek.com
UPSTREAM_API_KEY=
MODEL=deepseek-v4-flash
HOST=127.0.0.1
PORT=8080
REQUEST_TIMEOUT_SECONDS=1800
MAX_REQUEST_BYTES=10485760
LOG_LEVEL=INFO
PROXY_API_KEY=
PROXY_ALLOW_LOOPBACK_NO_AUTH=true
```

See [`../server/README.md`](../server/README.md) for the Adapter contract.

## Memory schema

Runtime opens **Memory Graph v12 only**.

An existing v11 database may be converted explicitly:

```bash
python -m eyle.devtools.migrate_memory_v11_to_v12 <storage-directory>
```

Normal Runtime code does not perform an in-process migration.

## Session and continuation state

Persisted Session and pending-interaction state are current-schema only. Historical shapes are not silently upgraded during a user request.

## Configuration rule

A configuration option should exist only when a deterministic component actually owns the behavior.

Do not add configuration for semantic choices that belong to Main, and do not keep obsolete aliases solely to make old files appear valid.
