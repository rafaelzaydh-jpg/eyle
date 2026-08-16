# Security Policy

## Reporting a vulnerability

Do not open a public issue for security vulnerabilities. Use GitHub's private **Security → Report a vulnerability** flow and remove secrets from logs or reproduction material.

## Physical security boundaries

Eyle fails closed around project paths, real workspace writes, protected resources, and unrestricted command execution:

- project paths are constrained to the configured project root;
- real writes require fresh observed material/hashes, dry-run, explicit confirmation, and a transaction;
- patches/writes are applied atomically and can be rolled back;
- successful writes are re-observed before Main may conclude;
- unrestricted `run_command` operates only on an isolated copied workspace;
- the real workspace is never mounted read-write into the command sandbox;
- protected credential/private-key resources are blocked by path/physical identity and omitted from readable/sandbox content surfaces;
- normal source is not blocked merely because it contains strings such as `token`, `password`, or `api_key`;
- Git status/diff capabilities are inspection-only;
- the web API uses independent authentication/rate-limit controls.

A network-enabled sandbox protects host/workspace integrity, **not confidentiality of normal source copied into that sandbox**. Code visible to an executed process can in principle be transmitted over the network.

## Semantic authority is not a security bypass

Main is free to interpret and plan, but physical restrictions remain Runtime-owned. The model cannot waive path protection, protected-resource policy, schema validation, confirmation, token/deadline budgets, sandbox restrictions, or transaction rollback through natural-language reasoning.

## Observation and provenance

`mat-*` Material represents physical observations. Memory contains Main-authored learned interpretations and may cite exact Material/Memory/request supports. Runtime preserves provenance/freshness mechanically but does not certify semantic truth.

Domain-specific validation belongs in the relevant capability/policy layer with explicit criteria. It must return findings through normal Observation rather than become a second universal semantic authority.

## Diagnostic history

Execution diagnostics must not expose chain-of-thought, raw prompts, raw model responses, protected content, patch bodies, or stored-memory bodies beyond the normal user-visible surfaces.

## Adapter boundary

Eyle only connects to the local Adapter on port `8080`. Remote provider credentials and provider-specific configuration remain in `server/.env` / Adapter environment and must not be committed.
