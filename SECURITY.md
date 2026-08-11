# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security vulnerabilities.

Use GitHub's **Security → Report a vulnerability** flow so the report can be
reviewed privately. Include the affected version, reproduction steps, expected
behavior, actual behavior, and any relevant logs with secrets removed.

## Security boundaries

Eyle is designed to fail closed around project writes and command execution:

- project paths are constrained to the configured project root;
- writes require fresh evidence, hashes, a dry run, and explicit confirmation;
- tests run through a configured sandbox and are refused when isolation cannot
  satisfy the requested policy;
- `run_command` is unrestricted only inside a strong per-job project snapshot:
  automatic selection prefers Docker and falls back to Bubblewrap; `trusted_local`
  and process-only execution are rejected for this capability;
- Docker sandbox execution uses one disposable persistent container per job; it may
  use the network, auto-pull the base image, install dependencies/toolchains, compile,
  create, modify, and delete files without user confirmation; only a sanitized copied
  snapshot is mounted read-write, never the real workspace;
- protected secret paths and files matching the secret-content policy are omitted
  from unrestricted sandbox snapshots so command execution cannot bypass the
  workspace read boundary;
- a network-enabled sandbox protects host/workspace integrity, not confidentiality
  of non-secret source copied into that sandbox; code visible to an executed process
  can in principle be transmitted over the network;
- `git_status` and `git_diff` are inspection-only; Rev5 does not expose Git mutation commands to the LLM;
- test and diff text returned to the model is bounded to reduce context flooding;
- patches are applied atomically and can be rolled back;
- the web API requires a bearer token and rate-limits invalid authentication.

These controls reduce risk, but they do not make an untrusted model or an
untrusted repository harmless. Run Eyle with the least operating-system
privileges possible and review every proposed change. The default configuration enables supervised editing only inside `workspace/`; external paths remain read-only until explicitly trusted.

## Observable execution history

The expandable job history and `execution_trace` are debugging surfaces, not reasoning transcripts. It may expose bounded tool names, sanitized arguments/results, accepted/rejected protocol decision types with reason codes, phase changes, token counters, validation stages, and rollback state. It must not expose chain-of-thought, raw prompts, raw model responses, source-file bodies, patch bodies, file hashes, secrets, or stored-memory bodies.

Reintroducing a removed architecture or compatibility path requires a concrete current failure plus a regression test or metric demonstrating the need. Git history and `CHANGELOG.md` are the historical references.

## Semantic verification boundary

Claim review is advisory semantic verification, not a new authority layer. The verifier receives typed grounding coordinates (`request`, answer anchors, source Evidence, Runtime Facts and Investigation targets) and cannot call tools or write. Runtime validates coordinate existence, not semantic sufficiency. A truthful physical blockage may be grounded by a Runtime Fact without inventing source Evidence. `self_check` is not independent verification because it uses the same configured model; `verified` may use a separate verifier backend. Deterministic write, path, hash, test and rollback controls remain runtime-enforced.
