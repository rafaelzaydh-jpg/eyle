<p align="center"><img src="assets/eyle-banner.svg" alt="Eyle autonomous programming agent" width="100%"></p>
<p align="center"><strong>One LLM brain, real programming tools, and supervised writes.</strong></p>

**Version:** 2.7.4 · **Schema:** 4.11.7 · **Revision:** 4.11.7-sentence-markdown-directory-flow

## Architecture

```text
Interface
→ runtime service
→ AgentSession
→ LLM
↔ tools
↔ external memory on demand
→ response-quality gate
→ response
```

The same LLM converses, interprets, plans when useful, investigates, writes code, and produces the final response. No separate agent prepares the mission or judges the answer.

The runtime controls only executable reality:

- safe paths and read limits;
- tool contracts and evidence hashes;
- dry-run and confirmation before writes;
- atomic and multi-file transactions;
- mandatory post-write compile checks, detected tests, transactional rollback, full reread, and exact failure diagnostics;
- sentence-indexed evidence-backed project claims, explicit finding limits, and response-quality validation;
- safe Markdown rendering and fresh structural evidence for directory questions;
- deadlines, calls, tokens, queueing, cancellation, and telemetry.

## AgentSession

A task keeps the original request, a compact stable task context, its current phase, an optional model-authored plan, latest tool results, a bounded set of relevant source snippets, a compact evidence index, progress counters, and a pending write proposal when needed. Project conclusions retain an internal typed claim-to-evidence ledger. The model references visible non-heading sentences by number instead of duplicating their text; legacy text claims remain compatible and materially different claims remain invalid. When a confirmed write fails, the real validation output and rollback state are preserved as runtime evidence for follow-up questions.

Common writes get at most two investigation turns before the tool catalog becomes patch-only. Overlapping or equivalent reads are blocked from existing evidence, and consecutive no-progress turns close investigation. External memory is never injected automatically. The agent searches or stores evidence-backed facts only through explicit memory tools.

## Editing flow

```text
request
→ LLM investigation and patch
→ dry-run
→ user confirmation
→ apply transaction
→ compile changed Python files
→ detect and run existing or newly created tests
→ on failure, expose the real validation output and rollback the whole write
→ preserve the failure report for follow-up questions
→ reread every changed file and confirm creates/deletes
→ final response with an honest verification state
```

No LLM call is required after confirmation.

## Layout

```text
eyle/core/       AgentSession, tools, memory, and safe editing
eyle/runtime/    service, queue, worker, persistence, and telemetry
llm/             backend transport and response adaptation
web/             Flask interface
```

## Usage

```bash
python main.py status
python main.py perguntar "Analyze the project"
python main.py serve
```

## Validation

- 136 tests pass in the packaged validation suite;
- 1 optional interface test was skipped because Flask is unavailable in the packaging environment;
- the real Qwen smoke test remains deployment-only.

See [Architecture](docs/architecture.md), [Configuration](docs/configuration.md), [Factual response quality](docs/rev4114-factual-response-quality.md), [Post-write verification](docs/rev4113-post-write-verification.md), and [Changelog](CHANGELOG.md).
