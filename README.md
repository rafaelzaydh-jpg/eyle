<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="760">
</p>

# Eyle

**Version:** 2.7.4 · **Schema:** 5.4 · **Revision:** rev5.2.9-progress-earned-authority

## Rev5.2.9 — Progress-Earned Authority

Rev5.2.9 keeps the Rev5.2.8 architecture and removes an artificial authority ceiling rather than adding a new subsystem. The base fuse remains 12 physical tools, but every runtime-validated committed-progress epoch can unlock +4 tools exactly once when the physical gate needs them; there is no cumulative +8 ceiling. A durable global credit-once Evidence set prevents old Evidence from being remapped or reopened to mint authority again. `investigation_updates.evidence_ids` is now truly additive, so the Main Agent sends only newly material Evidence IDs and runtime retains prior committed Evidence automatically. Claim rework also receives deterministic remaining-capacity feedback so scarce LLM calls can be used for investigation first and finalization last. Normal 8-turn and 12-LLM-call limits are unchanged.

## Rev5.2.8 — Canonical Runtime Cleanup

Rev5.2.8 adds no agent, tool, ledger or budget. It tightens the existing Runtime contracts after the legacy-audit benchmark exposed a false `ADMINISTRATIVE_LOOP`: Decision Ledger identity now includes objective observed state and physical authority, while runtime progress ignores free-form Investigation `reason/status` churn. Invalid tool batches fail atomically before tool authority, and the public tool ABI uses one canonical vocabulary (`path`, `line_start`, `line_end`, `symbol`, `limit`, `depth`, `filter`) with no legacy aliases. Open Investigation targets are explicitly allowed to accumulate Evidence incrementally when the Main Agent judges it material. The retired lexical workspace/write classifiers and the old semantic-read signature compatibility wrapper are deleted. Physical limits are unchanged.

## Rev5.2.7 — Two-Brain Claim Follow-up & Loop Control

Rev5.2.7 removed the `claim_repair` semantic profile and routes `contradicted`, `insufficient` and semantic gaps back to the Main Agent through deterministic Runtime reopen/pin/feedback. Only `agent` produces task semantics and only `claim_verifier` independently judges them.

## Rev5.2.5 — Transactional Contract Authority

Rev5.2.5 keeps the 12-tool base fuse but moves progressive authority out of Claim Review and into the runtime contract administrator. The Main LLM now sends only `investigation_updates`; the runtime owns the canonical Investigation Contract, commits structurally valid target updates independently, preserves accepted siblings when another update fails, and deposits objective `committed_progress` when real Evidence is attached or a target is validly established. That deposit becomes dormant authority: only when the physical tool gate would block an atomic batch, open debt still exists, and new committed progress has appeared since the previous extension can runtime grant +4 tool calls, capped at +8 in this release. Claim Review remains only the second-brain semantic verifier of the provisional final. The history panel keeps **expand all / collapse all** and now exposes committed progress and earned extensions.

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
→ main LLM ↔ transactional Investigation updates
→ runtime contract admin ↔ 16 deterministic tools + live workspace
→ deterministic Final Gate
→ Claim Review (single semantic 2FA)
   ├─ supported → response
   ├─ contradicted → Runtime reopens mapped debt → Main Agent
   └─ insufficient / semantic gap → same directed follow-up route
```

The administrative handshake is not an agent tool. It behaviorally verifies `json_schema`, then `json_object`, then prompt-driven JSON, and Eyle always validates structured output locally.

## Investigation Contract

Rev5.2 replaces the old free-form `plan` with a persistent semantic ledger. In Rev5.2.5 the runtime owns the canonical ledger and the Main LLM sends only target deltas through `investigation_updates`. Unmentioned targets stay committed exactly as they were; accepted Evidence cannot silently disappear. The Main LLM still decides all target semantics and declares only materially necessary targets:

```json
{
  "id": "T3",
  "goal": "Establish AgentSession's role in the real execution path",
  "status": "open",
  "evidence_ids": [],
  "reason": ""
}
```

Targets may be `open`, `established`, or `dismissed`. Existing target IDs cannot silently disappear and their goals cannot silently change. `established` requires real runtime Evidence and a reason; `dismissed` requires a reason. Updates are committed independently, so one invalid sibling does not erase accepted work. The runtime validates only those mechanical invariants—it never decides whether the Evidence actually proves the goal.

A grounded final cannot be accepted while a declared target is still `open`. Claim Review receives the same contract and can challenge an `established`/`dismissed` target with `target_id`, causing the runtime to reopen exactly that target. A material scope missing from the contract is reported with `target_id=null`; the Main LLM decides how to incorporate it.

`investigation` and `investigation_map` are deliberately separate: the first is **purpose**, the second is **navigation history**.

## Tools

The Main Agent still sees exactly 16 public tools:

`calculate`, `agent_info`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `find_symbol`, `read_range`, `read_file`, `memory_search`, `memory_store`, `run_tests`, `execution_trace`, `git_status`, and `git_diff`.

Rev5.2 does **not** add Planner/ResearchManager agents, callers/callees/reference tools, semantic file ranking, or a new read-range coverage system. The current benchmark showed a direction problem, not a discovery-tool problem.

Writing remains one model-facing protocol: `action=patches`. Runtime performs the transaction dry-run, confirmation, apply, compile/tests/reread and rollback path.

## Evidence and Claim Review

Full Evidence remains runtime-owned. The model receives bounded views. Evidence associated with Investigation targets is pinned only as compact metadata (`ID`, file, lines, hashes), so an early target does not lose its source pointer after many later observations.

Claim Review is the only independent semantic verifier. It checks material Claims and target coverage after a provisional final. It does **not** grant tool authority, define `committed_progress`, rewrite the answer, or choose tools. Local Claim, Semantic Gap and Finding protocol recovery only repairs the verifier's own malformed structured output; semantic debt from `contradicted`, `insufficient` or gaps is returned to the Main Agent through runtime-owned follow-up state.

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

Rev5.2.9 should be published only after the extracted artifact passes:

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
