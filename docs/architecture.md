# Eyle 2.7.4 architecture

Eyle 2.7.4 has one project agent and no legacy project fallback pipeline.

## Runtime flow

```text
route request
├─ general conversation → chat
└─ project request → Eyle agent
                     ├─ inspect workspace
                     ├─ execute validated tools
                     ├─ register fresh evidence
                     ├─ pause before writes
                     ├─ apply confirmed patch atomically
                     ├─ run tests and reread
                     └─ validate and publish
```

## Core modules

- `engine/engine.py`: public entry point, chat/agent routing, persistence, and pending confirmations.
- `engine/agent.py`: the single project-agent state machine.
- `engine/agent_state.py`: bounded working state and resumable task data.
- `engine/agent_tools.py`: executable tool registry and schemas.
- `engine/project_reader.py`: safe tree, file, range, and symbol reads.
- `engine/evidence_registry.py`: fresh evidence IDs and hashes.
- `engine/compiler.py`: prompts for the same agent in planning/finalizing phases.
- `engine/codar.py`: dry-run, atomic patching, rollback, and post-write verification.
- `engine/test_execution.py`: isolated project test execution.
- `llm/executar.py`: provider transport and internal profiles for the same Eyle agent.

## Removed architecture

The following runtime paths no longer exist:

- `consulta`, `dicas`, `visao_geral`, and `engenharia` project pipelines;
- separate Analyst, Executor, Suggestor, Engineer, and Understander personalities;
- `engine/dicas.py`, `engine/entender.py`, and the `verify/` package;
- silent fallback from the structured agent into the historical pipeline.

BM25 indexing remains an optional tool used by the agent to locate candidates. It does not own routing, finalization, or publication.

## Safety boundary

The model may choose investigations and propose changes. Deterministic code owns:

- tool input validation;
- permission levels;
- path containment and secret filtering;
- file/range hashes;
- write confirmation;
- atomic replacement and rollback;
- test and reread requirements;
- deadlines, retry limits, and terminal status.

## Revision 2 read finalization

Normal code-reading tasks now hand fresh evidence to a tool-free Finalizer that returns atomic `claims[]`. Each factual claim keeps its own `evidence_ids`; the system validates the evidence contract, runs typed grounding against those claim bindings, and only then renders the user-facing text. This avoids rebuilding evidence links from free-form prose after the answer is written.

## Revision 3 target coverage and fast path

For normal `project_read` tasks, the system extracts a minimal request contract from literal files, symbols, requested relationships, complete-file scope, and origin/value questions. The Finalizer receives those required targets together with fresh evidence. A conclusion is published only when `evaluate_target_coverage` marks every target as covered.

When all explicit files have already been read, the agent skips the extra planning turn that would only return `ready_to_finalize` and invokes the tool-free Finalizer directly. If coverage, utility, or grounding rejects the result, revision 3 permits one directed Finalizer repair; a second repair is refused with a specific failure.

## Windows test execution

Strong isolation remains preferred through Bubblewrap or Docker. On Windows, `backend=auto` may select `trusted_local` only when `sandbox.allow_trusted_local=true`. The backend runs an allowlisted argv with `shell=False`, a filtered environment, timeout, bounded output, and a temporary snapshot of the project. It does not claim network or kernel isolation.


## Revision 4 task intent and code-agent identity

Eyle remains one agent. `engine/task_contract.py` now derives a compact, deterministic task intent from the original request and stores it in the same task state:

```text
intent + response_profile + requested_outputs
+ write_allowed + recommendations_requested
```

The intent does not create separate agents or separate project-size flows. It only defines what the single Eyle must deliver. Analysis does not automatically become recommendations; review/suggestion does not silently become editing. The final intent gate rejects missing requested outputs, an incorrect recommendation count, and recommendation language that the user did not request.

Structured claims now support `absence` with fresh `evidence_ids` and an explicit reviewed `scope`. Inference continues to require an observed `basis`. After a confirmed edit passes tests and the final range is reread, deterministic state renders the user-facing write receipt directly; no additional LLM decision is required merely to summarize the completed workflow.

## Rev4.2 grounding and write confirmation

Structured claims remain the canonical units through grounding; the renderer is not parsed again in a way that can discard type, scope, or evidence IDs. If one claim is rejected, supported claims are retained and the normal utility, intent, health, and audit coverage gates determine whether the reduced answer is still sufficient.

For writes, the successful `test_patch_dry_run` action is the canonical proposal. A later `apply_patch` decision cannot replace its path, range, code, or hashes. The original range is derived from fresh evidence immediately before confirmation.


## Rev4.3 human-readable analysis rendering

The `code_analysis` response profile now requires five semantic sections: a plain-language summary, observable behavior, important components, component relationships, and verified limitations. The Finalizer still returns atomic grounded claims, but deterministic code orders and groups them into readable paragraphs before publication.

Coverage disclosure, evidence IDs, tools, audit phases, and test status remain available in the expandable task details. They are no longer prepended to the main answer. When HTTP interfaces are visible, the Finalizer is instructed to enumerate routes, methods, handlers, and returned values instead of collapsing them into a generic phrase such as “status endpoints”.
