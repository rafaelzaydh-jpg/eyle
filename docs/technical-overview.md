# Technical overview — Eyle 2.7.4 Rev4.11.2

Eyle is a single LLM-driven programming agent. One model call can answer directly or request a tool. Tool results return to the same session until it answers, asks a genuinely blocking question, or produces a dry-run-approved write proposal.

The core does not pre-classify intent. Greetings, architecture analysis, formulas, unfamiliar functions, and code edits all reach the same agent. Optional planning is a field in the agent decision rather than a separate pipeline.

External memory is available through tools and is never inserted automatically. Workspace discovery is direct; no ingest/index stage exists.

The web queue, worker isolation, telemetry, and progress reporting are adapters outside the reasoning core.
