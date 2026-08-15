# Verification

Run the complete test suite and the fail-closed release verifier:

```bash
pytest -q
python -m eyle.devtools.release_identity
```

The regression suite checks, among other things:

- only Explorar, Construir, and Concluir remain as cognitive moves;
- the exact request stays separate from optional Objective State;
- Runtime never interprets Objective status or gates completion;
- Memory remains persistent cognition, not a public tool;
- physical observations create Evidence independently of Memory;
- world scope stays Host-owned and opaque to Core;
- provider-owned selectors/freshness keep Memory body-agnostic;
- Runtime exposes graph mechanics without semantic importance policy;
- conversation transcript can be cleared without deleting the Memory Graph;
- benchmark background suppression does not delete stored history;
- public history exposes Objective presence without exposing hidden reasoning;
- prompt accounting includes both user and system prompt estimates.
