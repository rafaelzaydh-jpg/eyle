# Eyle Documentation

This directory documents the **current Eyle runtime contract**. Historical behavior belongs in [`../CHANGELOG.md`](../CHANGELOG.md) and Git history unless a current migration tool explicitly requires it.

Start with the root [`README.md`](../README.md) if you want to understand what Eyle is and what it does.

## Reading paths

### I want to run Eyle

1. [`../README.md`](../README.md) — project overview and quick start
2. [`configuration.md`](configuration.md) — current runtime configuration
3. [`../server/README.md`](../server/README.md) — DeepSeek Adapter setup
4. [`../SECURITY.md`](../SECURITY.md) — security boundaries

### I want to understand the architecture

1. [`architecture.md`](architecture.md) — component ownership and request lifecycle
2. [`model-surface.md`](model-surface.md) — Main context and ECC wire
3. [`memory-kernel.md`](memory-kernel.md) — Memory Graph v12
4. [`capability-providers.md`](capability-providers.md) — capability/provider model

### I want to validate or benchmark a release

1. [`verification.md`](verification.md) — release and behavioral gates
2. [`benchmark.md`](benchmark.md) — benchmark scenarios and metrics
3. [`../CHANGELOG.md`](../CHANGELOG.md) — release history

### I want to understand licensing or project policy

- [`../LICENSE.md`](../LICENSE.md) — public personal-use license
- [`../COMMERCIAL.md`](../COMMERCIAL.md) — commercial-use boundary
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — external contributions are currently closed
- [`../CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md) — repository communication policy
- [`../SECURITY.md`](../SECURITY.md) — vulnerability reporting and security model

## Documentation ownership rule

Documentation follows the same single-responsibility rule as the code:

- `README.md` explains the project and gets a new user to a working system.
- `architecture.md` explains component ownership and invariants.
- `configuration.md` explains current configuration, not architectural history.
- `model-surface.md` explains what is physically sent to Main and what wire comes back.
- `memory-kernel.md` explains Memory.
- `capability-providers.md` explains capability mechanics.
- `verification.md` defines release gates.
- `benchmark.md` defines measurement scenarios and metrics.
- `CHANGELOG.md` keeps history.
- `LICENSE.md`, `COMMERCIAL.md`, and project-policy files define governance.

The same contract should not be independently re-specified in multiple files unless the repetition is necessary for a user-facing quick start or safety warning.
