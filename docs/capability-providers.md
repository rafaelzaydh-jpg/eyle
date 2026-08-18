# Capability Providers under ECC

Providers remain deterministic mechanics. They do not plan, rank semantic relevance or call another reasoning agent.

Each capability declares an effect class: `observe`, `execute` or `mutate`. ECC maps `observe/execute` to Explorar and `mutate` to Construir.

A provider may set an optional presentation-only `ecc_name`. Core reads it generically; Core contains no bundled provider IDs. If aliases collide, ECC falls back to provider-qualified names.

Provider availability is not evidence of execution. Results become Observation/Material only when Runtime actually executes the capability.

## Sandbox staging and promotion

The bundled `standard` provider separates experimental execution from persistent workspace mutation. `run_command` operates in a persistent isolated job copy. Main can use that copy to assemble, download/clone, execute, test and repair a candidate without writing into the real workspace.

When the candidate is ready, `promote_sandbox` freezes the exact selected file/subtree as a private ZIP staging artifact, records byte hashes and the expected current workspace hashes, and returns one confirmation gate. The sandbox itself no longer needs to remain alive after preparation.

On confirmation Runtime mechanically verifies:

- the staged archive SHA-256;
- workspace freshness since preparation;
- protected-resource policy;
- every staged member hash;
- the exact post-promotion bytes;
- requested deletes for explicit `mirror` mode.

`merge` is the safe default and does not delete unrelated files. `mirror` is explicit because it may remove target files absent from the staged subtree. Promotion is a Build capability; Runtime never decides that a candidate is semantically "good enough" to promote.
