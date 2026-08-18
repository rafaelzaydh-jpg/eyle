# Configuration — Rev3.7.2

`config.json` contains only the current Eyle runtime/host contract. Rev3.7.2 performs **no in-process configuration upgrade**.

## Exact identity

The accepted identity is:

```text
app_version = 2.7.5
config_schema_version = 2.7.5-r3.7.2-ecc
revision = rev3.7.2-ecc
```

A different identity or an unknown field fails closed. Removed settings are not silently ignored, promoted or translated.

## LLM boundary

Eyle calls the local Adapter at port `8080`. Provider URL, API key and provider-specific transport live under `server/`.

Current `llm` mechanics include:

- `provider_token_budget_per_message = 150000`: provider-reported ledger for one logical user-message execution;
- `context_window_tokens = 50000`: physical limit for each LLM call;
- transport timeouts/retry/cooldown/concurrency;
- `reasoning_mode`;
- `adapter_handshake_timeout_seconds`.

Provider `usage.total_tokens` is authoritative. `prompt_tokens + completion_tokens` is used only when the provider omits the total. If potentially billable usage cannot be established safely, the execution stops fail-closed.

Removed generated-token fuse/deadline settings are not part of the schema.

## Context materialization

`context_engine` controls physical materialization, not semantic relevance:

```json
{
  "safety_margin_tokens": 500,
  "chars_per_token_fallback": 3,
  "conversation_materialization_tokens": 1200,
  "observation_materialization_tokens": 2200
}
```

Conversation and observation bodies are selected by token budget. There is no `MAX_HISTORY_MESSAGES`, fixed snapshot count, automatic Temporary Memory projection, relevance ranker or semantic context router.

## Memory

Runtime opens **Memory Graph v12 only**. It does not migrate older graph schemas while serving a request.

An existing v11 database may be converted explicitly with the one-shot tool:

```bash
python -m eyle.devtools.migrate_memory_v11_to_v12 <storage-directory>
```

After migration, normal runtime code only understands v12. Historical upgrade logic is not retained as a fallback.

## Standard provider

The bundled provider configuration is under:

```text
providers.standard
```

Its canonical package is:

```text
eyle.providers.standard
```

There is no `standard_impl` compatibility package or dynamic facade.

The test configuration uses the current keys:

```text
enabled
command_python
command_node
timeout_seconds
sandbox
```

Public capability paging fields use their current names, such as `page_size`; removed cognitive ceiling aliases are rejected.

## Interaction and continuation

`confirmacoes.expiracao_segundos` controls interaction expiration. Human wait is not a cognitive task deadline and does not reset the provider-token ledger.

Persisted Session and pending continuation state are current-schema only. No runtime path promotes old Session/pending shapes.

## Sandbox

`providers.standard.sandbox.backend=auto` selects an available supported isolation backend. Sandbox CPU/RAM/process/file/output limits are physical safety controls, not cognitive quotas.

For substantial edits, `run_command` may work in the isolated job copy and `promote_sandbox` may later freeze and promote exact hash-bound bytes after one physical confirmation.
