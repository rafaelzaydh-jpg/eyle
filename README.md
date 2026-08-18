<p align="center">
  <img src="assets/eyle-icon.png" width="96" alt="Eyle">
</p>

<h1 align="center">Eyle</h1>

<p align="center">
  <strong>A stateful general-agent runtime built around explicit cognition, persistent memory, deterministic execution, and observable tool use.</strong>
</p>

<p align="center">
  Python 3.11+ · Eyle 2.7.5 · Rev3.7.5.1
</p>

---

Eyle is a single-agent runtime that turns a language model into a persistent software agent with a clear separation between **meaning**, **agent logic**, **physical execution**, **memory**, and **provider transport**.

It is designed for long-lived conversations and technical work where an agent must inspect a project, use tools, remember relevant state, perform controlled changes, verify results, and expose what happened without hiding execution behind an opaque orchestration layer.

Eyle is not a prompt-only wrapper, a multi-agent swarm, or a semantic router around an LLM. The model remains the semantic authority; deterministic components own the facts and invariants that can be decided mechanically.

> **Design rule:** every component does only what it exists to do.

## What Eyle does

- **Persistent conversation** — recent conversation is preserved as native `user` / `assistant` roles and materialized by token budget.
- **Explicit cognition** — Main chooses one of three ECC movements: **Explore**, **Build**, or **Conclude**.
- **Persistent Memory Graph** — Memory Graph v12 stores learned state, revision history, relations, provenance references, retention, and context identity.
- **Project understanding** — inspect trees, read files, search code, find symbols, inspect relations, Git state, and project statistics.
- **Controlled execution** — run commands and tests inside an isolated project copy instead of mutating the real workspace directly.
- **Transactional changes** — persistent workspace mutations pass through explicit Runtime safeguards, confirmation, verification, and rollback-capable transactions.
- **Self inspection** — Eyle can inspect its own source through `source="eyle"` without conflating itself with the user's `workspace`.
- **Observable execution** — jobs expose turns, tool use, token accounting, Memory state, observations, failures, and physical progress without exposing hidden chain-of-thought.
- **Provider boundary** — the bundled Adapter connects Eyle to the configured DeepSeek model and owns only transport and structured-wire conformance.

## Why the architecture is split

Eyle keeps semantic and mechanical authority separate:

| Component | Owns |
|---|---|
| **Main** | meaning, relevance, investigation, learning intent, sufficiency |
| **Core** | Eyle-specific logic and contracts |
| **Runtime** | physical truth, execution invariants, persistence, budgets, transactions |
| **Memory** | persisted graph state and revision history |
| **Capability providers** | concrete ways to observe or change the external world |
| **Adapter** | provider connection, request translation, JSON/schema conformance, transport usage |
| **Service / UI** | conversation recording, jobs, worker lifecycle, user-facing execution state |

This prevents a helper layer from silently becoming a second planner or semantic authority.

## ECC: Explore, Build, Conclude

Every Main decision uses one of three movements:

1. **Explore** — observe, inspect, search, recall, calculate, run tests, or gather evidence without persistent world mutation.
2. **Build** — request a persistent physical change through Runtime safeguards.
3. **Conclude** — answer when Main judges that the request is resolved.

Memory is not a fourth movement. `memory_delta` is an independent sidecar to the ECC decision, so a Memory rejection cannot veto an otherwise valid Explore, Build, or Conclude decision.

## How a request flows

```text
User / API / Web UI
        │
        ▼
     Service
        │  conversation + job state
        ▼
ContextMaterializer
        │  bounded physical context
        ▼
      Main cognition
        │
        ├──────────────► LLM client ─► Adapter ─► DeepSeek
        │                                  │
        │                                  └─ schema delivery / validation / one format repair
        │
        ▼
ECC decision + memory_delta
        │                 │
        ▼                 ▼
     Runtime         Memory Graph v12
        │
        ▼
Capability Registry
        │
        ├─ inspect / read / search / test / Git
        ├─ isolated sandbox execution
        └─ confirmed transactional workspace changes
```

The active user request is always emitted exactly once as the final provider `user` message. Recent conversation precedes it as native roles; Runtime state is materialized before the conversation.

## Memory model

Memory Graph v12 separates three physical dimensions:

- `scope` — where a node is reachable;
- `domain` — `chat`, `task`, `eyle`, or `knowledge`;
- `context_key` — optional physical context identity.

Main authors semantic knowledge. Runtime may persist mechanically knowable conversation facts such as message identity, role, ordering, and conversation ID.

Normal cognition does **not** project the whole graph into the prompt. Memory bodies enter cognition only through explicit activation, so graph growth does not automatically increase the size of a trivial request.

See [`docs/memory-kernel.md`](docs/memory-kernel.md).

## Workspace and tools

The bundled Standard provider lives at `eyle.providers.standard`.

The user project is the dedicated `workspace/` directory. Eyle's own source is a separate read-only/self-inspection surface exposed as `source="eyle"`.

The Standard provider currently includes capabilities for:

- project inspection and tree listing;
- file reading and code search;
- symbol discovery and relations;
- calculations and token counts;
- Git status and diff inspection;
- commands and tests in an isolated copy;
- sandbox export and promotion;
- transactional real-workspace changes.

A command sandbox protects the host and real workspace from direct mutation. If network access is enabled, source visible to a process inside the sandbox should not be treated as confidential from that process.

See [`docs/capability-providers.md`](docs/capability-providers.md) and [`SECURITY.md`](SECURITY.md).

## Provider Adapter

Eyle connects to a local Adapter, by default at `127.0.0.1:8080`.

The current bundled Adapter is intentionally narrow and targets the configured **DeepSeek V4** profile. It:

- authenticates and connects to the upstream provider;
- translates the local Eyle request to the configured provider transport;
- delivers Eyle's caller-supplied JSON Schema as the output contract;
- mechanically recovers a JSON representation when safe;
- validates the result against the same schema;
- allows at most one isolated format-only repair;
- returns provider usage and transport diagnostics.

The Adapter does not own ECC meaning, Memory, Task state, tools, planning, relevance, or Eyle's execution budget.

See [`server/README.md`](server/README.md) and [`docs/model-surface.md`](docs/model-surface.md).

## Quick start

### Requirements

- Python **3.11+**
- Node.js only if the workspace or verification flow requires Node tooling
- a DeepSeek API key for the bundled Adapter
- an isolation backend supported by the configured sandbox if you want command execution

### 1. Create an environment

```bash
python -m venv .venv
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Install Eyle and Adapter dependencies:

```bash
python -m pip install -r requirements.lock
python -m pip install -r server/requirements.txt
```

### 2. Configure the Adapter

Copy the example environment file:

```bash
cp server/.env.example server/.env
```

On Windows, copy the file with Explorer or PowerShell.

Set at least:

```dotenv
UPSTREAM_API_KEY=your_key_here
MODEL=deepseek-v4-flash
```

The current Adapter configuration is documented in [`server/README.md`](server/README.md).

### 3. Add a workspace

Place or copy the project Eyle should work with under:

```text
workspace/
```

An empty workspace is valid; Eyle can still operate in conversation mode.

### 4. Start the Adapter

In one terminal:

```bash
python server/server.py
```

### 5. Start Eyle

In another terminal:

```bash
python main.py serve
```

The default web panel is:

```text
http://127.0.0.1:5000/
```

You can also ask a single question from the CLI:

```bash
python main.py perguntar "Analyze the current workspace and explain its architecture."
```

Check local state:

```bash
python main.py status
```

## Configuration

Runtime configuration lives in `config.json`. Rev3.7.5.1 is **current-schema only**: unknown, removed, or older configuration shapes are rejected instead of being silently upgraded.

Important physical budgets include:

- provider-accounted tokens per user-message execution;
- per-call context window;
- conversation materialization budget;
- observation materialization budget;
- Runtime feedback materialization budget;
- sandbox CPU, memory, process, file, and output limits.

These are physical controls, not semantic limits on how many reasoning turns a task is allowed to take.

See [`docs/configuration.md`](docs/configuration.md).

## Safety model

Persistent changes are intentionally harder than observations.

Eyle's Runtime enforces:

- project-root confinement;
- protected-resource checks;
- isolated unrestricted command execution;
- dry-run / confirmation / transaction boundaries for real writes;
- post-write re-observation;
- exact sandbox staging and promotion checks;
- provider token accounting;
- fixed-point detection for valid repeated execution without new progress.

Natural-language reasoning cannot waive these Runtime restrictions.

See [`SECURITY.md`](SECURITY.md).

## Observability

Execution diagnostics are designed to answer questions such as:

- how many cognition turns ran;
- which capabilities were physically executed;
- whether results were new or replayed;
- how much conversation was materialized or omitted;
- how many Memory nodes/relations exist;
- how many provider tokens were used or cached;
- whether structured output needed Adapter repair;
- where a task failed.

Diagnostics intentionally do not expose hidden chain-of-thought, raw prompts, raw provider responses, protected content, or private Memory bodies outside their normal user-facing surfaces.

## Project structure

```text
eyle/
  core/                  Eyle-specific agent logic
  runtime/               execution invariants, persistence, queue, Memory runtime
  providers/standard/    bundled workspace/tool capability provider

llm/                     model-facing Eyle contracts and client logic
server/                  local DeepSeek Adapter
web/                     Flask UI/API
memory/                  runtime Memory storage
context/                 runtime/benchmark artifacts
workspace/               user project
docs/                    architecture and operator documentation
tests/                   Eyle regression suite
```

## Documentation

| Document | Purpose |
|---|---|
| [`docs/README.md`](docs/README.md) | Documentation map and reading paths |
| [`docs/architecture.md`](docs/architecture.md) | Component ownership, request lifecycle, invariants |
| [`docs/configuration.md`](docs/configuration.md) | Current runtime configuration |
| [`docs/model-surface.md`](docs/model-surface.md) | What Main sees and the current ECC wire |
| [`docs/memory-kernel.md`](docs/memory-kernel.md) | Memory Graph v12 contract |
| [`docs/capability-providers.md`](docs/capability-providers.md) | Capability/provider rules and sandbox promotion |
| [`docs/verification.md`](docs/verification.md) | Release and behavioral gates |
| [`docs/benchmark.md`](docs/benchmark.md) | Benchmark scenarios and token/behavior metrics |
| [`server/README.md`](server/README.md) | Adapter setup and boundary contract |
| [`SECURITY.md`](SECURITY.md) | Security model and vulnerability reporting |
| [`CHANGELOG.md`](CHANGELOG.md) | Architectural evolution and release history |
| [`LICENSE.md`](LICENSE.md) | Personal-use source-available license |
| [`COMMERCIAL.md`](COMMERCIAL.md) | Commercial-use boundary |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Fork/development policy; external code contributions are currently closed |

## Development and verification

Install development dependencies:

```bash
python -m pip install -r requirements-dev.lock
python -m pip install -r server/requirements.txt
```

Run the release verifier and tests:

```bash
make verify
```

Or individually:

```bash
python -B -m eyle.devtools.release_identity
python -m compileall -q eyle llm server web tests main.py
python -m pytest -q
python -m pytest -q server/tests
node --check web/static/app.js
```

See [`docs/verification.md`](docs/verification.md) for the current release gates.

## Project status and compatibility

The active runtime follows a **current-contract** policy.

Historical compatibility is not kept indefinitely inside the hot path. When persisted user data requires a safe transition, the migration is implemented as an explicit tool rather than as a permanent runtime branch. For example, Memory Graph v11 can be migrated to v12 with the bundled one-shot migration devtool.

Release history is preserved in [`CHANGELOG.md`](CHANGELOG.md) and Git history.

## Contributions

Eyle is currently developed and maintained by its author. **External code contributions and pull requests are not currently accepted.**

You may create private modifications for personal, non-commercial use subject to [`LICENSE.md`](LICENSE.md). Technical feedback and security reports are still useful; vulnerabilities should follow [`SECURITY.md`](SECURITY.md).

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Eyle is **source-available, not open-source software**.

The public license permits personal, private, non-commercial use and private modifications subject to its terms. Commercial use, paid work, SaaS/API operation for third parties, commercial redistribution, sublicensing, or incorporation into a commercial product requires separate written permission from the copyright holder.

See [`LICENSE.md`](LICENSE.md) and [`COMMERCIAL.md`](COMMERCIAL.md).

---

**Eyle's core idea is simple:** the model should decide meaning; deterministic software should own everything that can be proven mechanically.
