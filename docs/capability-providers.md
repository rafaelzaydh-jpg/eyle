# Capability Provider contract — Rev1.5.3

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

Optional provider-owned causal boundary:

```text
establishes[]          what a successful call can physically establish
does_not_establish[]   nearby claims/effects that this capability cannot establish
```

These fields are model guidance authored by the Provider. Registry validates only that they are well-formed string arrays; Runtime does not interpret them, route from them, or judge Final prose against them.

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
rematerialize
evidence_selector
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

## Causal effect literacy

A capability result and task completion are different facts. Main must compare the active objective with the returned physical effect:

```text
resource      what was affected
operation     what physically occurred
persistence   call | job | persistent
changed       whether that resource state changed
```

A successful execution in one resource or lifetime does not imply a different resource/lifetime changed. A Provider should make this boundary explicit when confusion is plausible. For example, a simulator may establish simulated behavior while explicitly not establishing external-device state.
