# Technical Overview

The implementation is intentionally split by responsibility:

- `eyle/core/agent.py` — ECC loop and context fitting.
- `eyle/core/ecc.py` — generic ECC operation projection from provider effect classes.
- `eyle/core/evidence.py` — active-session Evidence contract.
- `eyle/core/memory.py` — semantic Memory sidecar application and graph-facing Core helpers.
- `eyle/core/session.py` — persisted AgentSession.
- `eyle/runtime/memory_graph.py` — SQLite graph persistence, revisions, anchors, freshness and topology.
- `eyle/runtime/ecc_runtime.py` — deterministic dispatch, Material recording and confirmation flow.
- `eyle/runtime/continuation.py` — Runtime-owned pending confirmation continuation.
- `eyle/runtime/token_budget.py` — context and token accounting mechanics.
- `eyle/capabilities/registry.py` — provider registry, schema/effect validation and dispatch.
- `eyle/providers/` — replaceable deterministic capability bodies.

Core owns the cognitive protocol. Runtime owns mechanics. Providers own body-specific world operations.
