# Rev4.11.3 — Real post-write verification

Rev4.11.3 closes the gap between an approved dry-run and a genuinely verified live write.

## Deterministic sequence

```text
user confirmation
→ apply the single-file or multi-file transaction
→ run compileall for every changed Python file that still exists
→ detect and run pytest/npm tests, including tests created by the transaction
→ rollback the complete write if compileall or tests fail/refuse/timeout
→ reread every changed file through the workspace tool
→ reread full contents and compare exact final hashes
→ confirm promised creates and deletes
→ report verified or partial verification honestly
```

No LLM call is used after confirmation.

## Python syntax

The runtime invokes the real Python `compileall` module in a temporary copy containing the changed Python files. The live project does not receive `__pycache__` artifacts. A compile failure restores the complete transaction.

## Test detection

Pytest is detected by root configuration markers or recursively by `test_*.py`, `*_test.py`, and `tests.py`. This means a test file created in the same confirmed transaction is visible before test execution. Node projects retain detection through `package.json` with a `test` script.

The default configuration enables tests. A detected suite must pass. Sandbox refusal, timeout, missing executable, or a non-zero test result is treated as verification failure and triggers rollback.

## Final reread

Every changed or created file is read again through the normal workspace tool and then read fully for an exact hash comparison. Deleted files must be absent. Created files must exist with the promised content. Any mismatch triggers rollback.

## Truthful result states

A write is called **verified** only when an applicable test suite actually ran and passed, in addition to compile and reread checks. When no suite exists or tests were explicitly disabled, the write may remain applied, but the response labels it as **partial verification** rather than pretending a dry-run proved the live result.
