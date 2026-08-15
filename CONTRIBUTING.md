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
6. Before reintroducing a removed architecture or compatibility path, document the concrete current failure and add a regression test or metric that justifies the change; consult Git history for prior removals.
7. Keep Core abstractions domain-neutral. Coding-language, repository, framework, document, network, or device semantics belong in capabilities/toolpacks unless the state is demonstrably universal to the agent protocol.
8. Keep shipped behavior and future direction distinct in documentation. `docs/architecture.md` describes the current runtime; `docs/architectural-direction.md` records non-shipped design goals.
9. Do not submit code, assets, documentation, or other material that you do not have the right to contribute under these terms.

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

Public documentation and canonical external contracts are written in English. Internal
agent instructions, tool contracts, state-machine messages, and structured JSON
schemas are kept in English for model reliability.
