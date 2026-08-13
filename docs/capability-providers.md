# Capability Provider contract — Rev1.5.1

Providers connect Eyle to a world without teaching that domain to Core.

## Provider

A Provider owns:

```text
provider_id
local capability map
available?        provider-owned availability
 describe?        stable/model-visible environment description
rehydrate?        provider-owned Material rehydration
validate_config?  provider-owned configuration validation
```

The Host registers Providers explicitly. There is no global provider registry.

## Local vs canonical IDs

Provider definitions use local IDs:

```python
Provider("petbot", {
    "food_level": {...},
    "dispense_food": {...},
})
```

Registry exposes `petbot.food_level` and `petbot.dispense_food`. Provider authors do not manually prefix IDs.

## Capability contract

Required semantic/physical description:

```text
description
input_schema
returns
effect = observe | execute | mutate
confirmation = none | required
```

Optional mechanical hooks include:

```text
fn
prepare / confirm
signature
observe
coverage
frontier
public_arguments
public_result
model_projection
covers
resource_failure
normalize
continue
limits
caveats
```

`confirmation_message` is intentionally absent: after confirmation the result returns to Main.

## Canonical result

Providers return the universal result envelope. Registry validates exact shape and effect coherence before Core consumes it.

A state-changing mutation must carry a physical effect, for example:

```json
{
  "resource": "petbot.feeder",
  "operation": "dispense",
  "persistence": "persistent",
  "changed": true
}
```

## Provider context

Host supplies an opaque map keyed by provider ID. A provider should read only its own namespace. Runtime may hash the complete map for continuation identity but does not interpret domain values.

## Contract-layer rule

Provider/capability infrastructure depends on `eyle/contracts`, never `eyle/core`. If a domain implementation needs Core internals, the boundary is wrong.

## Design rule

Before adding logic to Core ask:

> Is this meaning, a universal physical invariant, or domain mechanics?

Meaning → Main. Universal physical invariant → Runtime/contracts. Domain mechanics → Provider.
