# Model-facing surface — Rev1.5.1

Main sees a domain-neutral surface.

## Fixed guidance

The fixed prompt teaches physical distinctions, not domain workflows:

- Main owns meaning;
- capabilities come from independent providers and self-describe what they can do;
- capability availability/request/planning is not execution;
- Observation/Material/effects represent current physical results;
- capabilities are optional resources;
- if information sufficiency is uncertain, prefer observing before answering;
- actions are `capability_calls`, `await_user`, `complete`;
- Investigation/Task are optional and should be omitted when unused.

No bundled-provider capability name or keyword router belongs in the fixed prompt.

## Active task context

```text
request            immutable task origin
request_context    authoritative answers/refinements for this active task
prior_conversation bounded background/reference context
```

`request_context` prevents a resumed task from remaining semantically frozen on the pre-clarification wording while preserving original-request provenance.

## Dynamic capability catalog

Every available capability is projected as:

```text
name = provider.local
provider
purpose
effect
inputs
returns
caveats
limits
confirmation
```

The provider contract is the authority for capability meaning.

## Environment

`environment.providers` is supplied by installed Providers. Core has no special `project` projection.

## Epistemic coordinates

- `runtime_observations`: compact current observations;
- `current_material`: selected `mat-*` coordinates;
- `runtime_effects`: executed `eff-*` coordinates;
- `prior_conversation` and persistent Memory: context, not automatic current-world proof.

## Completion

`complete` carries `grounding_ids` and `effect_ids` as optional coordinates. Runtime validates coordinate identity/existence only; Main remains responsible for what its answer claims those coordinates establish.
