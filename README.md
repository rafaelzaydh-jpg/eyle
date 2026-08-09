<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="760">
</p>

# Eyle

**Version:** 2.7.4 · **Schema:** 5.2 · **Revision:** rev5.2.3-investigation-memory-progress

## Rev5.2.3 — Investigation Memory & Progress Semantics

Rev5.2.3 keeps every Rev5.2.2 hardening rule and fixes two P0 convergence defects exposed by hostile audits. Source suppression now follows what is visible in the **current compiled prompt**, not everything the model saw historically; historical ranges are telemetry only. Evidence named by an insufficient Claim/Semantic Gap or attached to a reopened target is pinned through semantic follow-up, so the stateless Main LLM is never told to investigate while the motivating source has vanished. Progress also means an observable knowledge/state change: `ok=true` alone no longer resets the no-progress fuse, unchanged project/runtime observations are suppressed, and repeated `run_tests` for the same scope is reused until a state-changing action invalidates it. The 16 public tools and the 8-turn / 12-tool / 9k-completion limits are unchanged.

Eyle is a local-first coding agent built around one `AgentSession`, deterministic tools, runtime-owned Evidence, supervised transactional writes, and one semantic Claim Review before grounded answers are accepted.

> **The LLM decides semantics. The runtime validates contracts.**

## Why Eyle exists

Eyle lets a connected LLM investigate a real workspace without turning the runtime into a second hidden agent. The LLM decides what must be established, how to investigate, what Evidence supports its conclusions, and what to say. The runtime owns structure, state, tool execution, hashes, freshness, safety, budgets, confirmation and deterministic validation.

The core is provider-agnostic. Qwen, Llama and other compatible models use the same Eyle protocol; only `llm/` adapts to the structured-output behavior actually delivered by the connection.

## Architecture

```text
interface
→ runtime/service
→ AgentSession
   ├─ current request + conversation background
   ├─ Investigation Contract (what remains to establish)
   ├─ investigation_map (where the agent has already navigated)
   └─ Evidence + runtime state
→ administrative structured handshake
→ main LLM ↔ 16 deterministic tools + live workspace
→ deterministic Final Gate
→ Claim Review (single semantic 2FA)
   ├─ supported → response
   ├─ contradicted → local Repair → Reverify
   └─ insufficient / target gap → reopen directed investigation
```

The administrative handshake is not an agent tool. It behaviorally verifies `json_schema`, then `json_object`, then prompt-driven JSON, and Eyle always validates structured output locally.

## Investigation Contract

Rev5.2 replaces the old free-form `plan` with a persistent semantic ledger. The Main LLM declares only materially necessary targets:

```json
{
  "id": "T3",
  "goal": "Establish AgentSession's role in the real execution path",
  "status": "open",
  "evidence_ids": [],
  "reason": ""
}
```

Targets may be `open`, `established`, or `dismissed`. Existing target IDs cannot silently disappear and their goals cannot silently change. `established` requires real runtime Evidence and a reason; `dismissed` requires a reason. The runtime validates only those mechanical invariants—it never decides whether the Evidence actually proves the goal.

A grounded final cannot be accepted while a declared target is still `open`. Claim Review receives the same contract and can challenge an `established`/`dismissed` target with `target_id`, causing the runtime to reopen exactly that target. A material scope missing from the contract is reported with `target_id=null`; the Main LLM decides how to incorporate it.

`investigation` and `investigation_map` are deliberately separate: the first is **purpose**, the second is **navigation history**.

## Tools

The Main Agent still sees exactly 16 public tools:

`calculate`, `agent_info`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `find_symbol`, `read_range`, `read_file`, `memory_search`, `memory_store`, `run_tests`, `execution_trace`, `git_status`, and `git_diff`.

Rev5.2 does **not** add Planner/ResearchManager agents, callers/callees/reference tools, semantic file ranking, or a new read-range coverage system. The current benchmark showed a direction problem, not a discovery-tool problem.

Writing remains one model-facing protocol: `action=patches`. Runtime performs the transaction dry-run, confirmation, apply, compile/tests/reread and rollback path.

## Evidence and Claim Review

Full Evidence remains runtime-owned. The model receives bounded views. Evidence associated with Investigation targets is pinned only as compact metadata (`ID`, file, lines, hashes), so an early target does not lose its source pointer after many later observations.

Claim Review is still the only semantic verifier. It checks material Claims and target coverage. Local Claim, Semantic Gap and Finding recovery preserve unaffected review content; the runtime never invents verdicts, gap types, Evidence or semantic corrections.

## Context boundaries

`request` is the only active task. `conversation_background` is stable, bounded and non-authoritative across every turn of the current job. `investigation_map` preserves observable current-task discoveries across semantic follow-up. Blocked duplicate/covered reads are not counted as executed identical tools.

## Run

```bash
python -m pip install -r requirements.lock
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

Edit `config.json` for the LLM endpoint, model and runtime limits. Structured-output capability is behaviorally probed and cached machine-locally in `context/llm_capabilities.json`, which is ignored by Git.

See [Configuration](docs/configuration.md).

## Validation

Rev5.2.3 should be published only after the extracted artifact passes:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q .
python -m pytest -q
node --check web/static/app.js
```

See [Benchmarking](docs/benchmark.md) for the real AgentSession acceptance scenario.

## License

Eyle is **source-available, not open-source software**. Personal, private, non-commercial use is permitted under [LICENSE.md](LICENSE.md). Redistribution, publication of modified copies, commercial use, sublicensing, sale, or offering Eyle as a service require prior written permission.

## Documentation

- [Architecture](docs/architecture.md)
- [Technical overview](docs/technical-overview.md)
- [Configuration](docs/configuration.md)
- [Benchmarking](docs/benchmark.md)
- [Publishing to Git](docs/github-publishing.md)
- [Changelog](CHANGELOG.md)
- [Português](README.pt-BR.md)
