# Verification

Eyle treats release verification as a fail-closed architectural contract, not only a unit-test count.

## Full local verification

```bash
python -B -m eyle.devtools.release_identity
python -m compileall -q eyle llm web main.py
python -m pytest -q
python -m pytest -q server/tests
node --check web/static/app.js
```

Or:

```bash
make verify
```

## What the release verifier checks

The verifier rejects an artifact when key architecture boundaries drift, including:

- forbidden/generated runtime state or bytecode in the release tree;
- unexpected Core modules/legacy semantic sidecars;
- public capability manifest drift;
- ECC catalog drift;
- loss of tolerant wire vs strict canonical schema separation;
- reintroduction of semantic item ceilings on Explore/Memory deltas;
- loss of Epistemic Memory fields or revisable relations;
- loss of Main-authored associative recall metadata;
- Adapter cognition semantics reappearing;
- missing formal Adapter handshake mechanics;
- loss of execution continuity/fuse/deadline persistence;
- reintroduction of automatic temporary-memory trimming;
- Memory Frontier storing the full selected ID universe instead of a DB cursor;
- missing scalable recall and navigation cleanup.

## Memory regression layer

Tests cover:

- Memory Graph migration to v8 without semantic reinterpretation;
- epistemic node/relation creation and revision;
- Main-authored aliases/concepts/cues;
- FTS5 and SQL-fallback recall parity;
- multi-query recall and relation-label navigation;
- DB-backed exact Frontier continuation;
- large-batch Memory writes and index refresh;
- factual Memory overview metrics.

## Structured transport regression layer

Tests prove that:

- Main may emit simple wire JSON;
- deterministic normalization recovers safe representation variants;
- canonical ECC remains strict and Eyle-owned;
- Adapter stays semantically blind;
- semantic structured errors return to the same Main execution;
- provider-mode downgrade occurs only after technical transport rejection.

## Execution continuity regression layer

Tests prove that confirmation/resume preserves logical execution identity, generated-token fuse, provider usage, absolute deadline, request identity and terminal capability state. The persisted deadline is checked before applying a deferred mutation.

## Continuous integration

`.github/workflows/ci.yml` runs the release verifier, Eyle tests, Adapter tests, and Python compilation on Python 3.11/3.12 for both Ubuntu and Windows. CI does not replace extracted-artifact verification for a published release.

## Clean publication

Before publishing:

1. run the full suite;
2. remove `__pycache__`, `.pytest_cache`, logs and mutable runtime state;
3. run the release verifier;
4. create the archive;
5. extract it to a fresh directory;
6. run the verifier/tests again from the extracted artifact.
