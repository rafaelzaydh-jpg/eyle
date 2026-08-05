# Eyle 2.7.4 — Core Reset report

## Goal

Start the 2.7.4 line with one Eyle coding agent capable of reading, analyzing, creating, editing, testing, and verifying projects without silently switching to the historical pipeline.

## Removed

- Project pipelines `consulta`, `dicas`, `visao_geral`, and `engenharia`.
- Historical Retrieval → Analyst → Executor → Verify orchestration.
- Legacy read fallback and patch-proposal fallback.
- Separate Analyst, Executor, Suggestor, Engineer, and Understander LLM wrappers/prompts.
- `engine/dicas.py`, `engine/entender.py`, and `verify/validar.py`.
- LLM-generated file understanding during ingest.
- Trusted-path rollout compatibility and the `off` fallback mode.
- Tests whose only purpose was preserving removed pipelines.

## Kept intentionally

- BM25 as an optional `search_code` tool controlled by the single agent.
- Deterministic `estrutura.json` and navigation hints in `entendimento.json`.
- Evidence registry, claims, audit coverage, response recovery, and grounding as validation helpers inside the same agent.
- CLI, Flask adapter, queue, worker, checkpoints, telemetry, and benchmark as adapters/devtools around the same entry point.
- Explicit confirmation before writes, dry-run, hashes, atomic replace, tests, reread, and rollback.

## Size change

- Runtime Python files: **40 → 37**.
- Runtime lines: **21,081 → 17,713**, reduction of **3,368 lines (16.0%)**.
- `engine/engine.py`: **2,769 → 944 lines**.
- `engine/compiler.py`: **1,087 → 610 lines**.
- `llm/executar.py`: **1,663 → 1,552 lines**.

## Resulting execution model

```text
request
├─ general chat → chat profile
└─ project task → Eyle agent
                  → validated tools
                  → fresh evidence
                  → confirmation if writing
                  → tests and reread
                  → validated terminal result
```

A project-agent failure remains a failure. There is no hidden alternate architecture.

## Additional correction

Atomic file replacement no longer calls `os.fchmod`; permissions are copied best-effort with `shutil.copymode`, making the write path compatible with Windows.

## Validation performed

- **297 tests passed** with `ResourceWarning` treated as an error.
- **1 test skipped** because Flask is not installed in the packaging environment.
- Python compilation passed.
- JavaScript syntax validation passed.
- Release identity passed: `2.7.4 / 2.7.4 / 1-core-reset-single-agent`.
- CLI help and status commands started correctly.

## Not validated in this environment

- Real Qwen 3.8 MAX benchmark against the user's production endpoint.
- Flask integration test without the Flask runtime dependency.

## Recommended next step

Run the benchmark on this clean base before adding more architecture. Fix only failures demonstrated by the real benchmark; do not restore the removed pipelines as fallbacks.
