# Contributing to Eyle

Thanks for helping improve Eyle.

Eyle is source-available under the **Eyle Personal Use License** in `LICENSE.md`; it is not an open-source project. Please read the license and the contributor terms below before submitting a contribution.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.lock
python -m pytest -q
```

## Pull requests

1. Keep changes focused and explain the user-visible behavior.
2. Add or update tests for every behavior change.
3. Preserve the fail-closed write, execution, and sandbox guarantees.
4. Never commit generated data from `memory/`, `context/`, or `workspace/`.
5. Run the full test suite before opening the pull request.
6. Before reintroducing a removed architecture or guardrail, read `UPDATE_HISTORY.md` and document the new evidence that makes the old tradeoff valid now.
7. Do not submit code, assets, documentation, or other material that you do not have the right to contribute under these terms.

## Contribution terms

By submitting a pull request, patch, commit, or other contribution to Eyle, you represent that you have the right to submit it.

You retain copyright in your original contribution. In addition, you grant the Eyle project maintainers a perpetual, worldwide, non-exclusive, irrevocable, royalty-free license to use, reproduce, modify, adapt, distribute, sublicense, relicense, and commercialize your contribution as part of Eyle or related works.

You also agree that accepted contributions may be made available to users under the repository's current license or under a future license selected by the project maintainers, without requiring additional permission from you.

If you do not agree to these contributor terms, do not submit a contribution.

## Commit style

Use short imperative messages, for example:

```text
Fix stale patch hash validation
Add grounded symbol-not-found result
Document llama.cpp setup
```

## Language

User-facing documentation may be written in Portuguese or English. Internal
agent instructions, tool contracts, state-machine messages, and structured JSON
schemas are kept in English for model reliability.
