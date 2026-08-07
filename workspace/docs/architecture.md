# Eyle Rev4.11.7 architecture

## Active path

```text
web/CLI
→ runtime service
→ AgentSession
→ LLM decision
↔ safe tools / external memory
→ deterministic response-quality gate
→ final answer or write proposal
```

There is no semantic router, mission interpreter, scout, finalizer agent, recovery agent, or automatic project-memory injection.

## AgentSession

The active session contains only:

- original request;
- optional adaptive plan;
- turn and tool counters;
- latest tool results;
- a bounded set of retained relevant source snippets;
- compact evidence index;
- an internal typed claim-to-evidence ledger whose claims reference visible answer sentences by index;
- write proposal persisted by the runtime when confirmation is required;
- explicit runtime phase, semantic read coverage, no-progress and exact-repeat counters;
- prompt/token diagnostics;
- structured runtime evidence for the latest failed confirmed write.

The newest tool result remains transient, while up to four relevant read snippets stay available across later tool calls. This prevents a useful source from disappearing merely because the agent listed the tree or inspected another file. Sources are deduplicated by evidence ID and cropped by configured character limits.

## Execution boundary

The LLM controls strategy, tool choice, code generation, and natural language. The runtime controls:

- tool schemas and current availability;
- workspace path safety;
- read limits;
- dry-run;
- explicit write confirmation;
- atomic file replacement;
- multi-file transaction and rollback;
- compileall for changed Python files after the live write;
- automatic detection and execution of existing or newly created tests;
- whole-transaction rollback on syntax, test, or reread failure;
- exact validation output in the user-visible failure response;
- failure metadata retained as citable runtime evidence for later questions;
- full post-write reread with exact output hashes and create/delete confirmation;
- truthful verified/partial-verification status;
- project-fact claims backed by read evidence;
- typed bug, risk, recommendation, and fact claims by 1-based non-heading sentence reference;
- citable workspace-tree evidence for folder and structure questions;
- safe DOM-based Markdown rendering;
- pruning of empty parent directories after confirmed file deletion;
- explicit “up to N” finding limits and mid-list correction rejection;
- deadlines, LLM turns, and tool-call limits.

## External memory

Memory is a tool, not prompt baggage. `memory_store` requires evidence IDs from the active session. Stored entries retain file hashes. `memory_search` filters stale entries before returning them.

## Loop controls

The runtime now uses a small phase machine rather than waiting for the global turn limit:

- `write_investigate`: read or discover the required files;
- `write_prepare`: prefer the transaction and read only genuinely missing files;
- `write_patch_only`: expose only patch dry-run tools;
- `write_patch_retry`: correct one rejected patch without restarting investigation;
- `analysis_answer_only`: answer from retained evidence with no tools.

A whole-file read covers later range reads of the same content. Equivalent tree, search, symbol and range requests are blocked by semantic signatures. Two consecutive turns without new evidence force completion or fail with `AGENT_NO_PROGRESS`. Maximum turns, tool calls and exact-repeat limits remain final safety caps.
