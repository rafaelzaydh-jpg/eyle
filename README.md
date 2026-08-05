<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle — supervised coding agent" width="100%">
</p>

<p align="center"><strong>One coding agent, one execution path.</strong></p>

<p align="center">
  <a href="README.pt-BR.md">Português</a> ·
  <a href="docs/architecture.md">Architecture</a> ·
  <a href="docs/configuration.md">Configuration</a> ·
  <a href="docs/benchmark.md">Benchmark</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

<p align="center">
  <img alt="Python 3.8+" src="https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white">
  <img alt="Release 2.7.4" src="https://img.shields.io/badge/release-2.7.4-2563EB">
  <img alt="Tests" src="https://img.shields.io/badge/tests-307%20passed-16A34A">
</p>

**Version:** 2.7.4 · **Schema:** 2.7.4 · **Revision:** 3-target-coverage-fast-path

## What changed in 2.7.4

Eyle now has one project pipeline. The historical Retrieval → Analyst → Executor → Verify paths and their hidden fallbacks were removed. A project request either runs through the Eyle agent or returns a specific failure; it is never silently rerouted into another architecture.

```text
User request
→ Eyle agent
→ validated tools
→ fresh evidence
→ guarded write confirmation when needed
→ tests and reread
→ validated answer
```

BM25 remains available as a search **tool**, not as a separate decision pipeline. Indexed metadata is only a navigation hint; current claims still require fresh reads.

Revision 2 also made normal project reads return structured `claims[]` before deterministic rendering. On Windows, tests may use the opt-in `trusted_local` backend, restricted by the command allowlist and executed in a temporary project snapshot.

Revision 3 extracts a minimal target contract from the request, blocks incomplete conclusions, allows only one directed repair, and finalizes explicit reads without spending an intermediate call merely to return `ready_to_finalize`.

## Core capabilities

- Analyze repositories and explain files, symbols, relationships, risks, and project structure.
- Create or edit code through validated tools and explicit write confirmation.
- Atomic writes, hashes, dry-run, test execution, reread, and rollback.
- Persistent task state, queue, checkpoints, CLI, and optional Flask UI.
- OpenAI-compatible, Ollama-style, llama.cpp, and LM Studio backends.
- Provider metadata including resolved model, token usage, reasoning usage, and finish reason.
- Project audit coverage and structured claims for evidence-backed conclusions.

## Quick start

```bash
python ingest.py /path/to/project --nome "My project"
python main.py status
python main.py serve
```

For a direct CLI task:

```bash
python main.py agent "Analyze the project"
```

Writes remain supervised even when `agent.rollout_mode` is `full`:

```json
{
  "agent": {
    "rollout_mode": "full",
    "require_confirmation_for_write": true
  }
}
```

## Design rule

The model is the reasoning engine. Deterministic code controls permissions, tool schemas, file boundaries, evidence freshness, confirmation, atomic writes, tests, rollback, deadlines, and terminal status.

See [Architecture](docs/architecture.md) for the runtime flow and [Configuration](docs/configuration.md) for the supported settings.
