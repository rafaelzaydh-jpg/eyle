# Capability Providers under ECC

Providers remain deterministic mechanics. They do not plan, rank semantic relevance or call another reasoning agent.

Each capability declares an effect class: `observe`, `execute` or `mutate`. ECC maps `observe/execute` to Explorar and `mutate` to Construir.

A provider may set an optional presentation-only `ecc_name`. Core reads it generically; Core contains no bundled provider IDs. If aliases collide, ECC falls back to provider-qualified names.

Provider availability is not evidence of execution. Results become Observation/Material only when Runtime actually executes the capability.
