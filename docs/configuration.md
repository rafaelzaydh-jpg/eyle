# Configuration

`config.json` contains Eyle-side host/runtime mechanics. Provider URL, API key, model translation and provider-specific headers/bodies belong in the transport Adapter under `server/`.

## Identity

Rev3 uses:

```text
app_version = 2.7.5
config_schema_version = 2.7.5-r3-ecc
revision = rev3-ecc
```

Clean late-Rev2.x configs are accepted and normalized in memory to the current identity without reinterpreting operator settings.

## LLM boundary

Eyle requires the local Adapter boundary:

```text
http://127.0.0.1:8080
http://localhost:8080
http://[::1]:8080
```

with optional `/v1`.

Direct remote-provider URLs, legacy local-model endpoints, port `8000`, and Ollama-style port `11434` are rejected by config validation. Provider connection details belong in `server/.env`.

Important `llm` settings:

- `model` — normally `auto`, passed through to Adapter;
- `temperature` — cognition temperature;
- `generated_token_fuse` — execution-wide provider-reported completion-token fuse (default `120000`);
- `context_window_tokens` — optional operator-declared physical context window; default `null` means Eyle does not locally crop to an arbitrary fixed window;
- `connect_timeout_seconds` / `read_timeout_seconds` — transport mechanics;
- retry/cooldown/cache fields — provider transport mechanics exposed to the Eyle client.

The Eyle→Adapter structured wire is fixed. Upstream selection among native JSON Schema / JSON-object / prompt JSON belongs exclusively to Adapter.

## Agent deadline

`agent.task_deadline_seconds` is an absolute logical execution budget. A confirmation pause does not reset it. Resume reconstructs the persisted logical deadline before applying a deferred write.

## Memory

Memory is Host-owned internal state, not transcript history. The prompt receives a working page, not the entire graph and not a Runtime relevance judgment. Main explicitly uses `memory_overview`, `memory_activate`, histories, relation navigation and `continue` when more context may exist.

No small fixed temporary-memory capacity silently archives learned state.

## Page-size settings

Settings such as:

- `max_file_read_lines`
- `max_tree_entries`
- `max_git_diff_chars`
- search/range limits

are default **materialization page sizes** when more finite content can be represented by Frontier. They are not semantic “Main may never read beyond this” rules.

Physical sandbox/process/file safety bounds remain real hard limits.

## Sandbox

`providers.standard.sandbox.backend=auto` chooses the strongest available supported backend. Unrestricted `run_command` operates on a disposable copied workspace and never receives a read-write mount of the real project.

`bloquear_rede=false` protects host/workspace integrity but does not make copied source confidential from a network-enabled process. Set the policy appropriate to the workload.

## Adapter environment

Copy `server/.env.example` to `server/.env` and configure:

```dotenv
UPSTREAM_BASE_URL=https://provider.example/v1
UPSTREAM_API_KEY=...
DEFAULT_MODEL=...
PORT=8080
UPSTREAM_STRUCTURED_MODE=auto
```

See [../server/README.md](../server/README.md) for transport details.
