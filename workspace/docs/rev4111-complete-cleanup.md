# Rev4.11.1 — Complete cleanup

Rev4.11 established the correct single-AgentSession architecture. Rev4.11.1 removes the inactive compatibility code that remained around it.

## Deleted

- `llm/cache.py` and all cache configuration/call paths;
- legacy `memory/projeto.json` workspace selection;
- `completion_gate`, `agente_status`, `agente_conclusao`, `verificacao_aprovada`, constant mode/phase/task-type fields, and duplicate response fields;
- core-side confirmation IDs and repeated pending fields (`objetivo`, `task_id`, `modo`, `version`);
- `salvar_texto_atomico`, `telemetry.clear`, unused imports, and unused constants.

## Specialized boundaries

- `AgentSession` owns only request, optional plan, recent tool results, evidence index, execution counters, and safe continuation state.
- `eyle.runtime.service` owns confirmation IDs, expiry, project binding, and the public response envelope.
- `llm.executar` only transports fresh AgentSession decisions; it does not replay prior model output.
- external memory remains available only through `memory_search` and `memory_store`.

## Functional flow

```text
user message
→ runtime records and delivers to AgentSession
→ LLM answers, plans optionally, or requests a tool
→ runtime executes the tool
→ result returns to the same AgentSession
→ agent answers or proposes a write
→ runtime requests confirmation
→ runtime applies, tests, rolls back on failure, and rereads
→ final response
```

## Validation

- 84 tests passed; one optional Flask interface test was skipped because Flask is not installed in the packaging environment.
- release identity: `2.7.4 / 4.11.1 / 4.11.1-complete-cleanup`.
- static scan found no unreferenced public top-level functions in `eyle/runtime`, `eyle/core`, or `llm`.
- runtime Python code fell from 38 files / 9,574 lines to 37 files / 9,031 lines.
- a real Qwen smoke run remains deployment-only.
