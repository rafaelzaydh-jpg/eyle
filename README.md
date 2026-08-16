<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="100%" />
</p>

# Eyle

**Eyle is a source-available general-agent runtime built around a deliberately small cognitive core: one Main LLM, persistent epistemic memory, and three universal action moves.**

> **Main owns meaning. Runtime owns physical truth and enforceable limits.**

The bundled body is focused on software/project work today, but the Core is domain-neutral: capability providers can expose other deterministic bodies without adding a second planner or semantic coordinator around the model.

![Release](https://img.shields.io/badge/release-Rev3-6D5DFB)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Architecture](https://img.shields.io/badge/Core-ECC%20%2B%20Memory-6D5DFB)
![Adapter](https://img.shields.io/badge/LLM-OpenAI--compatible%20APIs-0A7EA4)
![License](https://img.shields.io/badge/license-Personal%20Use-orange)

## Why Eyle

Most agent systems grow by adding planners, routers, task state machines, reflection agents, memory agents, relevance scorers, and domain-specific logic around the LLM. Eyle takes the opposite direction:

- **one semantic authority** — the Main LLM decides meaning, relevance, what to remember, what to inspect, and when enough is enough;
- **one intrinsic Memory Graph** — learned knowledge is not raw transcript and is not a separate “memory agent”;
- **three ECC moves** — Explore, Build, Conclude are enough to move through arbitrary capability bodies;
- **deterministic Runtime authority** — paths, hashes, permissions, budgets, transactions, evidence, pagination, continuation and rollback remain mechanical;
- **transport-only Adapter** — provider/model quirks stay outside the cognitive architecture;
- **no hidden semantic ranker** — Runtime may index and page knowledge, but it does not secretly decide what Main should care about.

## Architecture

```text
                              MEMORY GRAPH
                       learned, revisable knowledge
                           ▲              │
                      memory_delta        │ recall / Frontier
                           │              ▼
USER REQUEST ───────────► MAIN LLM ◄────────── Observation / Material
                           │
                           ▼
                          ECC
                  ┌────────┼────────┐
               Explore    Build   Conclude
                  │        │        │
                  └────────┼────────┘
                           ▼
                         RUNTIME
              physical truth / safety / state
                           │
                    Capability Provider
                           │
                          World

LLM traffic:
Eyle ──► local Adapter :8080 ──► remote OpenAI-compatible provider
```

### ECC: three universal moves

- **Explore (`explorar`)** — inspect, read, calculate, test, recall, search, or continue exact Frontiers. Independent read-only operations may be batched in one cognition turn.
- **Build (`construir`)** — request one lasting world change through Runtime safeguards. After a successful write, Main receives the verified post-write reality before it may conclude.
- **Conclude (`concluir`)** — answer when Main judges the request sufficiently resolved.

Every cognition turn may also emit `memory_delta`, so learning is continuous rather than a separate tool call.

## Memory that can change its mind

Eyle stores learned meaning in one SQLite-backed **Memory Graph v8**. Memory is epistemic and temporal rather than a database of eternal facts.

A node or relation may carry Main-authored metadata such as:

```text
nature       observation / hypothesis / preference / belief / decision / ...
confidence   current strength of the interpretation, not “percentage of truth”
volatility   how likely the represented state is to change
temporal     when the state/evidence applies
context      where the interpretation applies
retention    temporary | persistent
supports     exact request / Material / Memory provenance
recall       aliases / concepts / cues authored by Main
```

`retention=persistent` means **preserve this representation**, not **this is permanently true**. Old and new states can coexist and be related (`changed_from`, `supports`, `contradicts`, etc.) so a person, preference, belief, or world state can evolve without erasing history.

### Consolidation stays in the same brain

There is no second memory LLM. Main can incrementally:

- `remember` atomic knowledge;
- `revise` a node as evidence/context changes;
- `relate` memories;
- `revise_relation` as a relationship strengthens or weakens;
- `supersede`, `archive`, or `retire_relation` without deleting history;
- inspect node/relation history;
- derive higher-level hypotheses or patterns from active memories.

Artifacts remain external **Material**. A long document can support hundreds or thousands of small reusable memory nodes without copying the whole artifact into one memory blob.

## Scalable recall without hidden relevance

Memory recall is lexical/literal and DB-backed:

```text
Main-authored query / aliases / concepts / cues
                    │
                    ▼
               SQLite FTS5
             (SQL fallback)
                    │
                    ▼
          exact persisted selection
                    │
                    ▼
          page + public Frontier
```

The Frontier stores a database cursor, not the full matching `mem-*` universe in Session. Page size controls **what is materialized now**, never what Main is allowed to know. Main may follow the Frontier repeatedly.

The current implementation has been exercised with hundreds of thousands of memory nodes. Host storage and provider context remain physical limits; Runtime does not turn them into semantic relevance rules.

## Observation, Coverage and Frontier

Capabilities return objective observations. Large finite results are exposed as:

```text
Observation
├── Material
├── Coverage
└── Frontier (fr-*)
```

- **Coverage** says what was physically examined. It never says “this is enough”.
- **Frontier** means “the exact continuation after what you have seen”. It is not a stop signal.
- **Material** is observed source reality. Memory is Main's learned interpretation of reality.

This separation prevents old conversation text or previous interpretations from silently becoming universal truth.

## Provider-neutral LLM boundary

Eyle does not run or require a local LLM. It always talks to a local transport Adapter on port `8080`; the Adapter talks to the configured remote OpenAI-compatible API.

```text
Eyle -> http://127.0.0.1:8080 -> Adapter -> remote provider -> model
```

The Adapter is intentionally semantically blind. It owns:

- provider URL/key/model routing;
- OpenAI-compatible request/response transport;
- JSON transport mode negotiation;
- cache/usage metadata;
- syntactic JSON recovery;
- readiness and the formal Eyle handshake.

Eyle alone owns wire canonicalization and ECC/Memory semantic validation. A malformed cognition envelope is returned to the **same Main execution as feedback** rather than automatically killing the job.

## Safety and execution model

Runtime owns mechanically enforceable boundaries:

- constrained project paths and protected resources;
- isolated command/test execution;
- fresh hashes and post-write verification;
- dry-run + explicit confirmation for real writes;
- atomic transactions and rollback;
- execution-wide generated-token fuse;
- absolute task deadline;
- logical execution continuity across confirmation/resume;
- exact Coverage/Frontier state;
- fail-closed schema and release verification.

Changing configuration while a task waits for confirmation cannot grant that already-running logical execution a larger token fuse or a later deadline.

## Bundled capabilities

The included `standard` provider currently focuses on project/software work:

- project inspection and statistics;
- tree/file reading with exact continuation;
- code search and symbol relations;
- calculation/token counting;
- Git status/diff inspection;
- isolated command execution;
- test execution;
- transactional workspace edits;
- sandbox ZIP export.

The provider layer is replaceable. Core does not hardcode Python, Git, repositories, robots, browsers, networks, or another domain planner.

## Quick start

### Requirements

- Python **3.11+**
- a remote **OpenAI-compatible API**
- Windows, Linux, or another host supported by the selected sandbox backend

### 1. Install Eyle

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1

python -m pip install -r requirements.lock
```

### 2. Configure the Adapter

```bash
python -m pip install -r server/requirements.txt
```

Copy `server/.env.example` to `server/.env` and set at least:

```dotenv
UPSTREAM_BASE_URL=https://your-provider.example/v1
UPSTREAM_API_KEY=YOUR_KEY
DEFAULT_MODEL=YOUR_MODEL_ID
PORT=8080
```

Then start it:

```bash
python server/server.py
```

The Eyle side remains pointed at `http://127.0.0.1:8080`.

### 3. Start Eyle

Web UI + persistent Worker:

```bash
python main.py serve
```

Open `http://127.0.0.1:5000`.

CLI:

```bash
python main.py perguntar "Analyze the current project architecture"
python main.py status
```

## Project layout

```text
eyle/
├── core/          Main loop, ECC, Memory integration, Session/Evidence
├── runtime/       execution, persistence, continuation, observations, Memory Graph
├── providers/     deterministic capability bodies
├── capabilities/  capability registry/contracts
└── devtools/      benchmark and release verification
llm/               wire protocol, canonical schemas, Adapter client
server/            provider-neutral transport Adapter
web/               optional Flask UI/API
context/           generated runtime context/telemetry state (ignored)
memory/            conversation + Memory Graph state (ignored)
workspace/         selected/copied workspace state (ignored)
tests/             regression and architectural tests
```

## Verification

```bash
python -B -m eyle.devtools.release_identity
python -m pytest -q
python -m pytest -q server/tests
```

GitHub Actions also runs the verifier and both test suites on Python 3.11/3.12 across Linux and Windows.

The release verifier is fail-closed and checks identity, architectural boundaries, Memory/Frontier contracts, structured transport, Adapter blindness, execution continuity, and generated-artifact cleanliness.

See [docs/verification.md](docs/verification.md) for the full verification model.

## Documentation

- [Architecture](docs/architecture.md)
- [Intrinsic Memory Graph](docs/memory-kernel.md)
- [Model / wire surface](docs/model-surface.md)
- [Configuration](docs/configuration.md)
- [Capability providers](docs/capability-providers.md)
- [Benchmarks](docs/benchmark.md)
- [Verification](docs/verification.md)
- [Changelog](CHANGELOG.md)

## Current scope

Eyle's **architecture is general-agent oriented**, while the bundled body is still primarily a software/project agent. Current recall is intentionally literal/lexical rather than embedding-ranked; arbitrary mid-generation process checkpointing and exact in-flight provider token interruption are also outside the current implementation.

These are explicit boundaries, not hidden claims.

## License

Eyle is **source-available, not open source**. The repository is published under the [Eyle Personal Use License](LICENSE.md). Personal, non-commercial use and private modification are allowed under its terms; commercial use and redistribution require separate permission.

Contributions are governed by [CONTRIBUTING.md](CONTRIBUTING.md). Security reports should follow [SECURITY.md](SECURITY.md).
