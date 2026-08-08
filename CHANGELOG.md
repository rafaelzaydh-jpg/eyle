# Changelog

This file tracks public release-level changes. Detailed experimental and intermediate revision notes were intentionally removed before the Rev5 Git publication; Git history is the canonical record for future development.

## Rev5.1 — Context Boundaries & Investigation Continuity — 2026-08-08

- made `request` the explicit sole active task and replaced one-turn/duplicated context behavior with stable per-job `conversation_background`;
- preserved explicit ongoing conversational instructions across tool turns while marking prior-task context as non-authoritative;
- added compact `investigation_map` derived from observable successful tool history so `CLAIM_INSUFFICIENT` follow-up keeps navigation discoveries after bulky source views are cleared;
- blocked repeated/covered reads no longer increment the executed-identical-tool loop and now return the prior observable investigation map;
- added Local Finding Recovery after Claim recovery, preserving Claims/Semantic Gaps and regenerating only Findings before global revalidation;
- capped agent batches at four tool calls in provider schema and local parser and removed silent `calls[:4]` truncation;
- tagged failed assistant jobs and excluded them from future conversation background;
- removed obsolete `agent.task_context_token_budget` from the public config schema.

## Rev5 — GitHub Release — 2026-08-08

Rev5 is the publication baseline built from the validated Rev4.13.13 runtime. It does not redesign the agent loop; it consolidates the current architecture and removes accumulated release-document clutter.

### Current architecture

- One `AgentSession` execution loop.
- 16 public deterministic agent tools.
- One model-facing write protocol: `action=patches` → transactional dry-run → confirmation → apply → validation/rollback.
- Runtime-owned Evidence with proportional model-visible views.
- Deterministic Final Gate followed by one semantic Claim Review.
- Local Claim and Semantic Gap recovery without runtime semantic invention.
- Adaptive structured handshake per connection/model: `json_schema` → `json_object` → prompt JSON.
- Provider enforcement plus authoritative local Eyle validation.
- One structured contract source in `llm/structured.py`.

### Repository cleanup

- Removed historical root implementation/validation reports.
- Removed accumulated `docs/releases/` notes and obsolete Rev4.11 engineering notes.
- Removed redundant `UPDATE_HISTORY.md`.
- Rewrote README and canonical docs around the current architecture only.
- Preserved source, tests, dependency locks, assets, governance/security files, and machine-state `.gitkeep` directories.

## Development baseline — Rev4.13.13

The Rev5 codebase inherits the completed Rev4.13 line: canonical structured profiles, adaptive capability probing, deterministic answer anchors, proportional Claims/Evidence, Semantic Gaps, local Claim Repair/Reverify, local Semantic Gap recovery, bounded runtime telemetry, and supervised transactional writes.
