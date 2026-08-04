# Contributing to Eyle

Thanks for helping improve Eyle.

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
