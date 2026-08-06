# Rev4.11.2 — Write-loop and token fix

## Confirmed failure pattern

Rev4.11.1 exposed `patches` to the model without describing each patch object. Common outputs such as `{path, content}` reached the transaction layer as an incomplete range update. The dry-run failed, the last source was replaced by the error, and the agent could reread and retry until `max_llm_turns`. Three failed edit requests could therefore consume roughly 24 model turns.

## Changes

- Full-file `replace` is a first-class transaction operation.
- Existing files can be proposed as `{path, content}` after a fresh whole-file read.
- Missing hashes are derived from current evidence, never invented or read from stale memory.
- New files accept `create`; deletes accept `delete`; exact range updates remain available.
- A failed dry-run keeps the last relevant source for one correction.
- A second invalid write proposal stops with the real dry-run error instead of consuming the remaining agent turns.
- Chat history is included only on the first turn.
- Patch output tokens are sized from the fresh source and capped by configuration.

## Preserved safety

The change does not bypass confirmation. The sequence remains dry-run → user confirmation → atomic/transactional apply → tests when enabled → rollback on failure → reread. Existing-file replacement requires a fresh complete read and a matching file hash.

## Validation

The deterministic suite includes the reported classes of request: creating `/amor`, splitting routes into a new module, creating tests, correcting one invalid patch, and applying a confirmed full-file replacement without another LLM call.

## Additional execution guards

- An immediately repeated identical read is not executed again; the fresh source remains available for one corrective model turn.
- A failed mandatory reread after writing now triggers rollback for both single-file and multi-file transactions.
- Patch-path inference uses the same workspace containment resolver as the write layer.
