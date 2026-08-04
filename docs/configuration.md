# Configuration

Eyle reads `config.json` from the repository root. The shipped file is intentionally conservative: agent rollout is `read_only`, trusted project paths are empty, and project test execution is disabled until the operator configures and validates the environment.

## Local model backend

The default targets an OpenAI-compatible local server:

```json
{
  "llm": {
    "provider": "ollama",
    "base_url": "http://localhost:8080",
    "model": "auto",
    "openai_compatible": true,
    "max_tokens": 1500,
    "context_window_tokens": 8192,
    "connect_timeout_seconds": 5,
    "read_timeout_seconds": 120,
    "agent_timeout_seconds": 90,
    "executor_timeout_seconds": 180,
    "retry_max_attempts": 3
  }
}
```

Use `openai_compatible: true` for LM Studio, llama.cpp server, and compatible `/v1/chat/completions` endpoints. For native Ollama mode, set the provider, URL, model, and compatibility flag according to the comments in `config.json`.

`model: "auto"` selects the only model reported by `/v1/models`. If several models are loaded, configure the exact model identifier.

## Timeouts, retries, and budgets

Revision 51–54 separates connection, read, model-discovery, agent, and executor timeouts. Transient failures can retry with capped exponential backoff, jitter, cooldown, and `Retry-After` support. Permanent client errors do not retry.

The task-level deadline and budgets remain authoritative even when an individual operation allows more time:

```json
{
  "context": {
    "task_deadline_seconds": 300,
    "executor_retry_base_delay_seconds": 0.5,
    "executor_retry_max_delay_seconds": 2.0
  },
  "agent": {
    "task_deadline_seconds": 300,
    "max_llm_calls": 12,
    "max_total_generated_tokens": 12000
  }
}
```

## Agent rollout

```json
{
  "agent": {
    "rollout_mode": "read_only",
    "trusted_project_paths": [],
    "enabled_modes": ["analyze", "suggest", "edit"],
    "require_confirmation_for_write": true
  }
}
```

- `off`: uses the earlier non-agent pipelines.
- `read_only`: allows inspection and suggestions; blocks `WRITE` and `EXEC`.
- `full`: enables the guarded edit cycle only for paths explicitly listed in `trusted_project_paths`.

Do not switch to `full` merely to remove a warning. First run the benchmark with the exact model and target repository, define the trusted path narrowly, review the sandbox, and keep explicit write confirmation enabled.

Example for a project copied under the repository workspace:

```json
{
  "agent": {
    "rollout_mode": "full",
    "trusted_project_paths": ["workspace"]
  }
}
```

## Project test execution

The package ships with project test execution disabled:

```json
{
  "codar": {
    "testes": {
      "ativado": false
    }
  }
}
```

Enable it only after configuring an allowed command and an isolation backend that meets the intended policy. Eyle fails closed when the requested sandbox guarantees cannot be satisfied.

## Worker and queue

```json
{
  "worker": {
    "max_parallel_jobs": 2,
    "isolate_jobs": true,
    "job_deadline_seconds": 315,
    "heartbeat_interval_seconds": 5,
    "stale_worker_seconds": 30
  }
}
```

Jobs run in terminable child processes by default. Queue reservation is bounded, stale workers are recoverable, and status reports head-of-line blocking.

## Cache and telemetry

The LLM cache now uses two layers: a bounded in-process LRU for repeated calls in the active session and SQLite under `context/cache_llm.sqlite3` for reuse across sessions. Legacy JSON entries are migrated automatically. Empty responses and structured failure envelopes are invalidated instead of being returned as successful answers.

```json
{
  "llm": {
    "cache": {
      "ativado": true,
      "max_entradas": 4096,
      "memoria_max_entradas": 2048,
      "max_age_hours": 24
    }
  }
}
```

The TTL is absolute from creation time; a frequently hit response still expires. Exact cache keys include the backend fingerprint, model, temperature, system prompt, user prompt, and call mode.

Telemetry can expose call/tool/job status and latency percentiles without publishing prompt contents.

## Web API token

Run `python main.py serve`. The terminal prints the token and its persistent path. When no environment or explicit configuration token is used, the generated value is stored in `context/web_api_token.txt` with restricted permissions. The browser prompt repeats these locations, and the **token** button allows retrying or replacing the value without reloading.

## Runtime data

The following directories are intentionally ignored by Git:

- `workspace/`: projects being analyzed;
- `memory/`: generated project memory and history;
- `context/`: cache, queue, traces, tokens, confirmations, telemetry, and backups.

Never publish their contents without reviewing them for source code, credentials, private conversations, and local paths.
