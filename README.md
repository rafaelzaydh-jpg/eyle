<p align="center"><img src="assets/eyle-banner.svg" alt="Eyle autonomous programming agent" width="100%"></p>
<p align="center"><strong>One LLM brain, real programming tools, and supervised writes.</strong></p>

**Version:** 2.7.4 · **Schema:** 4.11.2 · **Revision:** 4.11.2-write-loop-fix

## Architecture

```text
Interface
→ runtime service
→ AgentSession
→ LLM
↔ tools
↔ external memory on demand
→ response
```

The same LLM converses, interprets, plans when useful, investigates, writes code, and produces the final response. No separate agent prepares the mission or judges the answer.

The runtime controls only executable reality:

- safe paths and read limits;
- tool contracts and evidence hashes;
- dry-run and confirmation before writes;
- atomic and multi-file transactions;
- tests, rollback, and reread;
- deadlines, calls, tokens, queueing, cancellation, and telemetry.

## AgentSession

A task keeps only the original request, an optional model-authored plan, latest tool results, a compact evidence index, execution counters, and a pending write proposal when needed.

External memory is never injected automatically. The agent searches or stores evidence-backed facts only through explicit memory tools.

## Editing flow

```text
request
→ LLM investigation and patch
→ dry-run
→ user confirmation
→ apply
→ tests when enabled
→ rollback on failure
→ reread
→ final response
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

- 90 tests pass in the packaged validation suite;
- 1 optional interface test was skipped because Flask is unavailable in the packaging environment;
- the real Qwen smoke test remains deployment-only.

See [Architecture](docs/architecture.md), [Configuration](docs/configuration.md), [Write-loop fix](docs/rev4112-write-loop-fix.md), and [Changelog](CHANGELOG.md).
