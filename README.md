<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="760">
</p>

# Eyle

**Version:** 2.7.4 · **Schema:** 5.1 · **Revision:** rev5.1-context-boundaries-investigation-continuity

Eyle is a local-first coding agent built around one `AgentSession`, deterministic tools, runtime-owned Evidence, supervised transactional writes, and a semantic Claim Review before grounded answers are accepted.

> **The LLM decides semantics. The runtime validates contracts.**

## Why Eyle exists

Eyle is designed to let the connected LLM investigate and reason about a real workspace without turning the runtime into a second hidden agent. The runtime owns safety, structure, budgets, hashes, freshness, confirmation, persistence and validation. The LLM owns investigation choices, semantic interpretation, answer wording and patch intent.

The core is provider-agnostic. Qwen, Llama and other compatible models can use the same Eyle protocol; only the `llm/` boundary adapts to the structured-output capability actually delivered by the connection.

## Architecture

```text
interface
→ runtime/service
→ AgentSession
→ administrative structured handshake
→ main LLM ↔ 16 deterministic tools + live workspace
→ Evidence Core
→ deterministic Final Gate
→ Claim Review (single semantic 2FA)
   ├─ supported → response
   ├─ contradicted → local Repair → Reverify
   └─ insufficient / semantic gap → directed main-agent follow-up
```

The administrative handshake is not an agent tool. It behaviorally verifies `json_schema`, then `json_object`, then prompt-driven JSON and caches the verified mode per connection/model. Provider enforcement is never trusted by itself: every structured response is validated locally by Eyle.

## Tools

The Main Agent currently sees 16 public tools:

`calculate`, `agent_info`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `find_symbol`, `read_range`, `read_file`, `memory_search`, `memory_store`, `run_tests`, `execution_trace`, `git_status`, and `git_diff`.

Writing is intentionally not exposed as patch tools. The model emits the canonical `action=patches` protocol and the runtime executes one transactional path.


## Context boundaries and investigation continuity

`request` is the only active task. `conversation_background` is a bounded, non-authoritative conversation view that remains stable across every turn of the current job, so explicit ongoing user instructions can survive tool use without an older task silently becoming the new objective. `investigation_map` is derived from observable successful tool history and preserves the current task's navigation state across `CLAIM_INSUFFICIENT` follow-up.

Blocked duplicate/covered reads are not counted as executed identical tools. They return the existing observable map and contribute to generic no-progress control. Agent batches are contractually limited to four tool calls per turn; larger batches are rejected instead of silently truncated.

## Supervised writes

```text
request
→ inspect source
→ action=patches
→ transaction dry-run
→ user confirmation
→ transaction apply
→ compile/tests/reread
→ rollback on validation failure
→ verified response
```

## Evidence and Claim Review

Full Evidence remains runtime-owned. The model receives bounded views and can request deeper ranges. Claims and Evidence are proportional to the material content of the answer: numbers such as ~6, 12 or 20+ are guidance, never quotas.

Claim Review is the only semantic final verifier. It checks atomic Claims and conclusion-level Semantic Gaps. Local protocol recovery preserves valid review content and re-evaluates malformed Claims or Semantic Gaps; Finding coverage can also be regenerated from preserved Claims; the runtime never invents verdicts, gap types, Evidence or semantic fixes.

## Run

Create your environment and install dependencies:

```bash
python -m pip install -r requirements.lock
```

Useful commands:

```bash
python main.py status
python main.py perguntar "Analyze the project"
python main.py serve
```

For development:

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q
```

## Configuration

Edit `config.json` for the LLM endpoint, model and runtime limits. Structured-output capability does not need a provider-specific setting: Eyle probes the actual behavior and stores the machine-local result in `context/llm_capabilities.json`, which is ignored by Git.

See [Configuration](docs/configuration.md) for details.

## Project layout

```text
eyle/core/       AgentSession, tools, Evidence, Claim Review and safe editing
eyle/runtime/    service, queue, worker, persistence, telemetry and history
llm/             transport, adaptive capabilities and structured contracts
web/             local web interface
tests/           canonical regression suite
docs/            current architecture, configuration, benchmarks and publishing
```

## Validation

The Rev5.1 release is intended to be published only after the extracted artifact passes:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q .
python -m pytest -q
node --check web/static/app.js
```

See [Benchmarking](docs/benchmark.md) for the real AgentSession acceptance scenario.

## License

Eyle is **source-available, not open-source software**. Personal, private, non-commercial use is permitted under the terms in [LICENSE.md](LICENSE.md). Redistribution, publication of modified copies, commercial use, sublicensing, sale, or offering Eyle as a service require prior written permission.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Documentation

- [Architecture](docs/architecture.md)
- [Technical overview](docs/technical-overview.md)
- [Configuration](docs/configuration.md)
- [Benchmarking](docs/benchmark.md)
- [Publishing to Git](docs/github-publishing.md)
- [Changelog](CHANGELOG.md)
- [Português](README.pt-BR.md)
