# Model-facing surface — Rev1.4.1

The Main LLM receives fixed guidance from four places only:

1. `PROMPT_AGENTE`: semantic authority, available paths, physical boundaries.
2. Structured response transport: one short reminder; JSON Schema owns field shape.
3. Capability discovery/active contracts: factual purpose, inputs, effect, outputs and physical caveats.
4. `runtime_feedback`: factual rejection/state/expectation data after an invalid physical/structural decision.

## Law

Fixed text may expose **paths and facts**. It must not choose the semantic path for Main.

Therefore:

- workspace presence/emptiness is context, not a task;
- Task and Investigation are optional until Main creates them;
- tool availability does not imply tool use;
- no-action/retry feedback reports valid envelopes/state, not strategy;
- physical safety restrictions remain mandatory Runtime facts;
- direct Final is a first-class action.

`tests/test_rev141_semantic_freedom.py` and the release verifier guard this surface against prescriptive regressions.
