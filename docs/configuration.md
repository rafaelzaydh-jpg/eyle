# Configuration

Eyle reads `config.json` from the repository root.

## Local model backend

The default file targets an OpenAI-compatible local server:

```json
{
  "llm": {
    "provider": "ollama",
    "base_url": "http://localhost:8080",
    "model": "auto",
    "openai_compatible": true,
    "max_tokens": 1500,
    "context_window_tokens": 8192
  }
}
```

Use `openai_compatible: true` for LM Studio, llama.cpp server, and other local
servers exposing `/v1/chat/completions`. With Ollama's native API, set the
provider and compatibility flag according to the comments already included in
`config.json`.

## Minimum recommended model

For supervised agent use, the supported baseline is **LiquidAI/LFM2.5-8B-A1B** or a compatible quantized derivative. Smaller models may work for read-only inspection, but editing reliability is not assumed. Always benchmark the exact checkpoint and quantization used on the target machine.

## Agent rollout

```json
{
  "agent": {
    "rollout_mode": "full",
    "trusted_project_paths": ["workspace"],
    "enabled_modes": ["analyze", "suggest", "edit"]
  }
}
```

- `off`: use the earlier non-agent pipelines.
- `read_only`: allow inspection and suggestions, but block writes and execution.
- `full`: enable the supervised edit cycle only for paths listed in `trusted_project_paths`. Every write still requires explicit confirmation.

The repository default trusts only `workspace/`. External paths automatically fall back to `read_only`; add them only after repeated real-model benchmark runs.

## Runtime data

The following directories are intentionally ignored by Git:

- `workspace/`: projects being analyzed;
- `memory/`: indexed project memory and history;
- `context/`: model cache, queue, traces, tokens, confirmations, and backups.

Never publish these folders without reviewing their contents for source code,
credentials, private conversation data, and local paths.
