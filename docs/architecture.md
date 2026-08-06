# Eyle Rev4.11.2 architecture

## Active path

```text
web/CLI
→ runtime service
→ AgentSession
→ LLM decision
↔ safe tools / external memory
→ final answer or write proposal
```

There is no semantic router, mission interpreter, scout, finalizer agent, recovery agent, or automatic project-memory injection.

## AgentSession

The active session contains only:

- original request;
- optional adaptive plan;
- turn and tool counters;
- latest tool results;
- compact evidence index;
- write proposal persisted by the runtime when confirmation is required;
- a simple exact-consecutive-repeat counter;
- prompt/token diagnostics.

The latest tool result may contain source code once. If a write dry-run fails, the last relevant source stays available for one correction instead of forcing a reread loop. Older code is represented by evidence metadata.

## Execution boundary

The LLM controls strategy, tool choice, code generation, and natural language. The runtime controls:

- tool schemas and current availability;
- workspace path safety;
- read limits;
- dry-run;
- explicit write confirmation;
- atomic file replacement;
- multi-file transaction and rollback;
- test execution;
- post-write reread;
- deadlines, LLM turns, and tool-call limits.

## External memory

Memory is a tool, not prompt baggage. `memory_store` requires evidence IDs from the active session. Stored entries retain file hashes. `memory_search` filters stale entries before returning them.

## Loop controls

Only three broad controls remain:

- maximum LLM turns;
- maximum tool calls;
- maximum consecutive identical calls.

Invalid write proposals have a separate two-attempt cap. The runtime does not attempt semantic equivalence or manufacture a definition of “progress.”
