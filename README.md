<p align="center"><img src="assets/eyle-banner.svg" alt="Eyle autonomous programming agent" width="100%"></p>
<p align="center"><strong>One LLM brain. Real tools. Supervised writes. Observable execution.</strong></p>

**Version:** 2.7.4 · **Schema:** 4.12.2 · **Revision:** 4.12.2-context-runner-hardening

Eyle is a local programming agent built around a deliberately small idea: the LLM decides what to do, deterministic tools measure and execute reality, and the runtime protects the few boundaries that must never be guessed.

## Why Eyle exists

Eyle is designed for real repositories, including projects too large to place in one prompt. It does not preload the whole codebase or run a committee of agents. It inspects only what the current task needs, keeps useful evidence available, and can edit several files through one supervised transaction.

```text
user
→ AgentSession
→ LLM decision
↔ deterministic tools / live workspace / external memory on demand
→ dry-run + confirmation for writes
→ compile/tests/reread/rollback
→ answer
```

## Rev4.12.2: context + test-runner hardening

Rev4.12.2 keeps the Rev4.12.1 tools and expandable history, then fixes two failures found in real 245k-token project tests: large structured tool outputs overflowing the next prompt, and `run_tests` existing while `pytest` was only a development dependency. Every assistant response created by a job can expose an on-demand **history** panel in the web UI. The panel is loaded only when opened and shows runtime facts such as:

- agent turns and final phase;
- LLM logical attempts, requests actually sent, preflight blocks, latency and finish metadata;
- prompt tokens, cached tokens, new/uncached tokens, output and effective totals;
- accepted/rejected decision type per turn, including validation rejection reason;
- tools called, their safe observable arguments and summarized results;
- post-write `compileall`, tests, rereads and rollback state;
- failure codes when a job stops.

It deliberately does **not** expose chain-of-thought, raw prompts, raw model responses, source-file contents, hashes, or stored memory bodies.

## Tool-assisted reasoning

The model does not need to calculate or estimate everything mentally. Rev4.11.8+ includes deterministic tools for:

- `calculate` — bounded decimal arithmetic;
- `project_stats` — files, lines, characters, bytes and languages;
- `count_tokens` — measured text size with explicit exact/heuristic metadata;
- `inspect_project` — objective entrypoint/import/route/test/CI/framework signals without deciding which file is “important”;
- `search_code`, `read_file`, `read_range`, `find_symbol`, `list_tree` — live source inspection;
- `agent_info` — current identity and executable tool registry;
- `run_tests` — sandboxed real test execution with optional focused pytest scope, bounded diagnostic output and explicit `TEST_RUNNER_UNAVAILABLE` diagnostics;
- `git_status` — read-only working-tree state;
- `git_diff` — read-only bounded diff inspection;
- external memory tools that are used only when the model asks for them.

Large tool results are compacted generically before entering the next prompt; the complete runtime result remains recoverable in session/history. The tool observes. The LLM decides what the observation means for the current task. Deterministic utility results such as `calculate` are evidence-backed, but the final response is still written by the LLM so tone and explanation remain natural.

## Supervised editing

```text
request
→ inspect required source
→ generate one transaction
→ dry-run
→ user confirmation
→ apply
→ compile changed Python files
→ detect and run tests
→ rollback on compile/test/reread failure
→ reread exact outputs
→ report verified or partial-verification state honestly
```

Common write investigations are phase-controlled so the agent cannot spend every turn rereading the same repository. Equivalent reads are blocked from fresh evidence and normal writes move to a patch-only phase after the investigation budget.

## Evidence and answer quality

Project facts, confirmed bugs and contextual risks must come from real project observations. The runtime keeps a compact claim-to-evidence ledger and enforces explicit limits such as “up to 3”. Claims reference visible answer sentences by index instead of duplicating the sentence text inside the model protocol.

## Project layout

```text
eyle/core/       AgentSession, tools, project inspection, memory and safe editing
eyle/runtime/    service, queue, worker, persistence, telemetry and public history
llm/             backend transport, normalization and token accounting
web/             Flask chat UI and expandable execution history
docs/            architecture, configuration, release and engineering notes
```

## Run

```bash
python main.py status
python main.py perguntar "Analyze the project"
python main.py serve
```

The web data endpoints use a Bearer token. `python main.py serve` prints where the local API token can be obtained.

## Validation

- 162 tests pass in the packaged deterministic suite;
- 1 optional Flask interface test is skipped when Flask is not installed in the packaging environment;
- the real Qwen smoke test remains deployment-only.

## License

Eyle is **source-available, not open-source software**. The repository may be viewed publicly, and the license permits individuals to download, install, run, and privately modify Eyle for personal, non-commercial use. Redistribution, publication of copies or modified versions, sale, sublicensing, commercial use, and offering Eyle as a service require prior written permission.

See [LICENSE.md](LICENSE.md) for the controlling terms and [CONTRIBUTING.md](CONTRIBUTING.md) for contributor terms. Limited rights that arise from using GitHub itself remain subject to GitHub's Terms of Service.

## Documentation

- [Architecture](docs/architecture.md)
- [Technical overview](docs/technical-overview.md)
- [Configuration](docs/configuration.md)
- [Benchmarking](docs/benchmark.md)
- [Rev4.12.2 release notes](docs/releases/2.7.4-rev4.12.2.md)
- [Update history: removed designs and why](UPDATE_HISTORY.md)
- [Changelog](CHANGELOG.md)
- [Português](README.pt-BR.md)
