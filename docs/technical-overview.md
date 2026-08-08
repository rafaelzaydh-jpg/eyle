# Technical overview — Eyle Rev5.1

Rev5.1 builds on the cleaned Rev5 Git baseline and tightens context and recovery boundaries. It keeps a provider-agnostic core and moves backend-specific capability handling to `llm/`.

## Adaptive structured handshake

The first structured use of each `transport + base_url + resolved model` performs a short behavioral probe. A cached machine-local result is tried first on later process starts. `json_schema` is accepted only when the backend demonstrably enforces the probe schema; HTTP success or a provider feature claim is insufficient. If schema enforcement is unavailable, Eyle tests `json_object`, then explicit prompt JSON.

`context/llm_capabilities.json` stores capability metadata only and is excluded from Git/releases. Administrative probes are observable separately and do not consume agent turns or tool-call limits. Transport errors do not downgrade capability; structural failures trigger bounded revalidation.

## Canonical structured contracts

`llm/structured.py` is the single source for provider schema, mandatory fields, local validation and retry guidance. `llm/executar.py` transports that contract through the verified connection mode. Raw reasoning is never executable structured output, alternate shapes are not silently translated, and embedded JSON is not scanned from prose.

## Semantic verification

The deterministic Final Gate validates only runtime-owned facts about the response contract. Claim Review then performs semantic verification. Local recovery may ask the verifier to re-evaluate one malformed Claim or Semantic Gap, but runtime never chooses semantic verdicts, Evidence, gap types or removals.

## Workspace and writes

The agent investigates through deterministic tools and bounded Evidence views. Writing is represented once as `action=patches` and executed through the transaction engine with confirmation, compile/tests/reread and rollback.

## Conversation and task continuity

Conversation background is built once from recent persisted messages and retained for every turn of the job. It is explicitly lower authority than the current request. Failed assistant jobs are tagged and excluded from future background. Task-local `investigation_map` is regenerated from observable tool history, so semantic follow-up can continue from prior discoveries without replaying bulky source bodies.

Agent tool batches are capped at four by the canonical structured contract and local parser. The runtime never silently discards excess calls.
