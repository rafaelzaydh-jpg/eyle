# Configuration — Eyle 2.7.4

Eyle reads `config.json` from the repository root. The core reset removed the historical `engine`, `dicas`, `entendimento`, trusted-path rollout, and legacy-pipeline settings.

## Minimal example

```json
{
  "llm": {
    "provider": "openai_compatible",
    "base_url": "http://127.0.0.1:8080",
    "model": "auto",
    "context_window_tokens": 8192
  },
  "agent": {
    "rollout_mode": "full",
    "enabled_modes": ["analyze", "suggest", "edit"],
    "max_steps": 12,
    "require_confirmation_for_write": true,
    "exigir_run_tests_apos_escrita": true
  },
  "codar": {
    "fazer_backup": true,
    "testes": {
      "ativado": true,
      "comando_python": "pytest -q"
    }
  }
}
```

## Rollout

- `read_only`: permits investigation but blocks write/exec tools.
- `full`: enables the supervised edit workflow. Writes still require confirmation when `require_confirmation_for_write=true`.

There is no `off` mode with a fallback into an older pipeline.

## Important limits

- `agent.max_steps`: maximum state-machine decisions.
- `agent.max_llm_calls`: task-wide LLM call budget.
- `agent.max_total_generated_tokens`: task-wide output budget.
- `agent.max_tree_entries`, `agent.max_tree_depth`: inventory limits.
- `agent.max_read_range_lines`: fresh-read limit.
- `llm.context_window_tokens`: provider context window.
- `llm.project_read_finalizer_max_tokens`: explanation budget.
- `agent.target_coverage_enabled`: require every deterministic request target before success.
- `agent.project_read_single_repair_enabled`: permit one directed Finalizer repair and no more.
- `agent.project_read_fast_path_enabled`: finalize as soon as all explicit files have fresh evidence.
- `agent.intent_output_gate_enabled`: require the response to match the detected code-task profile and reject unsolicited recommendations.
- `agent.deterministic_write_receipt_enabled`: finish a verified write from patch/test/reread state without an extra model summary call.
- `worker.max_parallel_jobs`: queue worker concurrency.

The schema rejects invalid types, non-positive operational limits, ports above 65535, ingest worker counts above 32, and response budgets that consume the whole context window.

## Windows trusted-local test backend

When Bubblewrap is unavailable and Docker is not configured, Windows may use an explicit local backend:

```json
{
  "codar": {
    "testes": {
      "sandbox": {
        "backend": "auto",
        "allow_trusted_local": true,
        "comandos_permitidos": [["pytest"], ["python", "-m", "pytest"]],
        "copiar_projeto": true
      }
    }
  }
}
```

`trusted_local` never uses a shell and only executes allowlisted argv in a temporary project snapshot with a filtered environment, timeout, and bounded output. It is not a network sandbox and does not provide Bubblewrap/Docker kernel isolation. `backend=auto` selects it only on Windows and only when `allow_trusted_local=true`.
