# Model-facing surface — Rev1.4.3

The Main LLM receives fixed guidance from four places only:

1. `PROMPT_AGENTE`: semantic authority, epistemic source roles and physical boundaries.
2. Structured response transport: one short reminder; JSON Schema owns field shape.
3. Capability discovery/active contracts: factual purpose, inputs, effect, outputs and physical caveats.
4. `runtime_feedback`: factual rejection/state/expectation data after an invalid physical/structural decision.

## Laws

Fixed text may expose **paths, provenance classes and physical facts**. It must not choose the semantic path for Main.

- `prior_conversation` is retained context and may be incomplete or stale;
- Memory is persistent prior cognition and may be stale;
- `available_capabilities` describes invokable actions, not current workspace/implementation facts;
- `runtime_observations` / `current_material` carry current physically observed state;
- inference may be used, but it is not newly observed fact;
- workspace presence/emptiness is context, not a task;
- Task and Investigation are optional until Main creates them;
- tool availability does not imply tool use;
- direct Final is a first-class action.

`tests/test_rev142_epistemic_clarity.py` and the release verifier guard this surface.

## Semantic completion

The fixed prompt gives only the meaning of optional state fields: `Investigation.conclusion` is what Main says grounding establishes about its goal; `Task.result` is what Main says was achieved against `completion_criteria`. These statements do not require Main to create either object.

Established Investigation Material is not pinned solely because it was once evidence. Open Investigation grounding is pinned while unresolved; after establishment, the conclusion is the semantic compression and the canonical Material remains Runtime-owned.
