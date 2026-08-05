# Proposed revision 55.23 — deployment validation and provider hardening

Revision 55.22 fixes the deterministic orchestration and measurement defects found by the first Qwen 3.8 MAX benchmark. The remaining items require the deployment environment or broader provider coverage.

## 1. Qwen 3.8 MAX rerun

Run the revision 55.22 benchmark at least three times against the exact production endpoint. Compare factual, completion, grounding, workflow, safety, resolved-model, finish-reason, reasoning-token, and P50/P95 metrics. Release qualification should require zero false success and all edit workflows completing or rolling back correctly.

## 2. Flask integration suite

Install `requirements-dev.lock` and run the Flask-dependent security and job-lifecycle tests. The offline packaging environment cannot validate these routes because Flask is not available locally.

## 3. Provider conformance corpus

Add recorded OpenAI-compatible and Ollama envelopes covering streaming, `length`, content filters, tool-call finishes, missing usage fields, provider-specific reasoning usage, and partial JSON. The deterministic adapter tests cover the known Qwen/OpenAI shape but cannot prove every provider dialect.

## 4. Public confirmation API benchmark path

The benchmark now preserves and resumes every write pending state through the agent continuation contract. A future integration benchmark should additionally drive the exact web/queue confirmation API so persistence, worker pickup, and browser polling are measured end to end.
