# Capability Providers under ECC

Capability providers are Eyle's physical interface to the outside world.

They expose deterministic mechanics to Runtime. They do not plan, rank semantic relevance, or call another reasoning agent.

## Provider contract

Each public capability declares:

- a public name;
- an argument contract;
- an effect class;
- deterministic validation/execution mechanics;
- a public result projection;
- optional observation/material/provenance behavior.

A provider can add mechanics without teaching Core domain-specific semantics.

## Effect classes

Capabilities declare one of three effect classes:

| Effect class | Meaning | ECC movement |
|---|---|---|
| `observe` | obtain information without persistent mutation | Explore |
| `execute` | run an operation whose result is observational/transient | Explore |
| `mutate` | persistently change the external world/workspace | Build |

Availability does not prove execution. Only Runtime execution creates an Observation.

## Registry

The Registry is the canonical physical capability surface.

Core consumes provider metadata generically. It should not contain bundled capability IDs or language/framework-specific routing rules.

A provider may expose a presentation-only `ecc_name`. If public aliases collide, Runtime falls back to provider-qualified names instead of guessing semantic intent.

## Bundled Standard provider

The current bundled provider is:

```text
eyle.providers.standard
```

It contains the workspace-oriented capabilities shipped with Eyle.

Current public mechanics include:

- calculations;
- project statistics;
- token counting;
- project inspection;
- tree listing;
- code search;
- symbol discovery/relations;
- paged continuation;
- file reading;
- sandbox command execution;
- sandbox export;
- sandbox promotion;
- test execution;
- Git status/diff;
- workspace transactions.

The exact current public list is also recorded in `release_manifest.json` and validated by release tests.

## Source identity

Capabilities that inspect source distinguish:

```text
source="workspace"
source="eyle"
```

`workspace` is the user's project.

`eyle` is the source tree of the Eyle instance currently running.

This distinction is physical. Main decides from conversation meaning which source is intended.

## Observations and Material

Capability results can produce:

- compact observations;
- exact Material (`mat-*`);
- grounding/provenance references;
- Coverage;
- Frontier continuation.

Large results should be paged/materialized without becoming unreachable.

## Sandbox execution

`run_command` executes in a persistent isolated copy of the current source surface.

For the user workspace, the real workspace is not mounted read-write into the unrestricted command sandbox.

The sandbox can be used to:

- install/download dependencies inside the copy;
- run scripts;
- compile;
- run tests;
- inspect generated artifacts;
- iteratively repair a candidate.

A sandbox result does not mutate the real workspace.

## Network boundary

A network-enabled sandbox protects host/workspace integrity, not confidentiality of source visible inside that sandbox.

Any process that can read source and access a network can potentially transmit that source.

Configure network blocking according to the task and threat model.

## Sandbox staging and promotion

When an isolated candidate is ready, `promote_sandbox` can stage exact bytes for a confirmed persistent mutation.

The current flow is:

```text
sandbox candidate
      │
      ▼
freeze selected subtree/file
      │
      ├─ archive SHA-256
      ├─ member hashes
      ├─ expected current workspace hashes
      └─ target/mode
      │
      ▼
user confirmation
      │
      ▼
Runtime freshness + protected-resource checks
      │
      ▼
promotion
      │
      ▼
post-write byte verification
```

The sandbox itself does not need to remain alive after the staging artifact is created.

### Merge

`merge` is the safe default.

It writes staged files without deleting unrelated target files.

### Mirror

`mirror` is explicit because it may delete target files that are absent from the staged subtree.

Runtime never decides that a candidate is semantically "good enough" to promote. Main makes that judgment; Runtime only verifies the exact requested physical mutation.

## Workspace transactions

Direct persistent workspace changes use the transaction path rather than unrestricted command execution.

The transaction contract protects:

- expected/fresh source state;
- protected resources;
- exact proposed mutation;
- confirmation when required;
- atomic application;
- rollback on failure;
- post-write re-observation.

## Adding a provider

A new provider should answer a concrete physical question:

> What can Runtime deterministically observe, execute, or mutate through this boundary?

It should not introduce:

- another semantic planner;
- a relevance model hidden below Main;
- a provider-specific concept inside Core unless it is universal to Eyle;
- an alternate persistence authority;
- an implicit compatibility layer for old capability contracts.

If semantic choice is required, expose the physical capability and let Main choose it.
