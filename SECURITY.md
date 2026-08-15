# Security Policy

## Reporting a vulnerability

Do not open a public issue for security vulnerabilities. Use GitHub's private **Security → Report a vulnerability** flow and remove secrets from logs/reproduction material.

## Physical boundaries

Eyle fails closed around project paths, real workspace writes and unrestricted command execution:

- project paths are constrained to the configured project root;
- real writes require fresh observed material/hashes, dry-run, explicit confirmation and a WriteTransaction;
- patches are applied atomically and can be rolled back;
- `run_tests` follows the configured supervised test sandbox policy;
- unrestricted `run_command` requires a strong sandbox and operates only on a copied workspace; `auto` prefers an embedded Microsandbox microVM before Docker/Bubblewrap;
- the real workspace is never mounted read-write into `run_command`; the Microsandbox backend mounts only Eyle's disposable snapshot at `/workspace`;
- protected credential/private-key resources are identified by path/physical identity and omitted from readable/sandbox content surfaces;
- normal source is not blocked merely because it contains strings such as `token`, `password` or `api_key`;
- `git_status` and `git_diff` are inspection-only capabilities;
- web API authentication/rate limits remain independent host controls.

A network-enabled sandbox protects host/workspace integrity, **not confidentiality of normal source copied into that sandbox**. Code visible to an executed process can in principle be transmitted over the network.

## Observation grounding

Observed `mat-*` material may contain source excerpts required for a task. Runtime keeps provenance/freshness and Main decides whether that material grounds a conclusion. There is no second Evidence store to bypass the physical Observation boundary.

## Grounded Completion boundary

Rev1.4.3 has no Claim or second delivery LLM. Runtime improves reliability only through physically decidable checks: open Main-owned commitments block Final, established Investigation must contain a Main-authored conclusion and real Material, grounded completed Tasks must reference real Material, and committed Material coordinates must survive into Final grounding. Runtime does not certify semantic truth or domain safety.

Domain-specific security validation requires explicit criteria owned by the relevant capability/policy layer. A future specialist LLM validator may be used behind such a capability when given a concrete rubric, but it must return findings through normal Observation rather than become universal Core authority.

## Diagnostic history

Execution trace is an internal diagnostic projection over canonical runtime history. It must not expose chain-of-thought, raw prompts, raw model responses, protected content, patch bodies or stored-memory bodies.

## Compatibility

Reintroducing a removed Core contract requires a concrete current failure and a regression test/metric demonstrating the need. Git history is the historical archive.


## Rev1.4.3 model-facing safety boundary

Safety-critical physical restrictions remain Runtime-owned even though semantic strategy is free. The prompt cleanup does not weaken path protection, sandbox isolation, write confirmation, schema validation, budgets or transaction rollback. Runtime feedback reports these physical facts without choosing the Main LLM's next reasoning step.
