# Eyle

Eyle is a small general-agent architecture built around one idea:

> **The LLM decides what things mean. Runtime decides what is allowed to happen.**

The same cognitive core can work with different bodies: a code workspace, documents, a robot, a network, or another provider. The body changes; the basic brain does not.

## The three moves

Eyle has only three cognitive moves:

- **Explorar** — observe, read, calculate, test, or inspect.
- **Construir** — make a lasting change through Runtime safeguards.
- **Concluir** — answer the user when there is enough information.

Memory and Objective travel with these moves. They are not extra actions.

```text
                    MEMORY GRAPH
                       ↕
USER ──► MAIN LLM ── ECC ──► RUNTIME ──► CAPABILITIES ──► WORLD
                  E / C / C
```

## Main LLM

Main is the semantic brain. It decides:

- what the user means;
- what matters now;
- what should be checked next;
- what is worth remembering;
- how memories relate;
- whether an Objective is useful;
- when it has enough support to answer.

## Runtime

Runtime is deliberately non-semantic. It handles things that can be checked mechanically:

- schemas and IDs;
- persistence;
- permissions and confirmations;
- token/time limits;
- capability execution;
- Evidence recording;
- source anchors and freshness;
- transactions and rollback;
- graph structure and telemetry.

Runtime never decides what a fact means, what is important, what the user intends, or what should be remembered.

## Memory

Memory is Eyle's persistent knowledge graph. It stores compact knowledge that may help again later: user facts, preferences, decisions, rules, useful project/world facts, identifiers, relations, and conclusions.

Physical memories can be linked to the source that supported them. If the source changes, Runtime can mark that support stale without deleting the meaning. Main then decides what the change means.

## Evidence

Evidence is different from Memory:

```text
observe something
      ↓
Evidence: what was really seen
      ↓
Main interprets it
      ↓
Memory: what is worth knowing later
```

Every physical observation can become Evidence automatically. Memory changes only when Main chooses to learn something from it.

## Objective

`current_request` is the exact user input for an AgentSession. `objective_state` is optional and means only: **what is Eyle still trying to achieve?**

Objective is not a planner. It does not contain a tool sequence, phases, or Runtime routing. Simple conversation and direct answers may need no Objective at all.

## Capabilities

Providers are Eyle's replaceable body. The bundled provider works with a workspace and exposes deterministic operations for reading, searching, testing, calculating, running commands, and controlled writes.

Other products can attach different providers without changing ECC or the Memory Graph.

## Run

Requirements: Python 3.11+.

```bash
python -m pip install -r requirements.txt
python main.py serve
```

For development checks:

```bash
python -m pip install -r requirements-dev.txt
pytest -q
python -m eyle.devtools.release_identity
```

## Documentation

- `docs/architecture.md` — system boundaries and data flow.
- `docs/memory-kernel.md` — persistent Memory Graph.
- `docs/model-surface.md` — structured ECC contract seen by the model.
- `docs/capability-providers.md` — provider/body contract.
- `docs/configuration.md` — configuration and identity.
- `docs/benchmark.md` — benchmark and ablation tools.
- `docs/verification.md` — verification steps.

## License

See `LICENSE.md`.
