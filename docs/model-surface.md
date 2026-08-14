# Model-facing surface — Rev1.5.3

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

## Task knowledge and working material

The dynamic prompt now includes compact `task_knowledge`: Main-selected EvidenceSpan coordinates plus Findings and Conclusions. Evidence bodies are not automatically replayed there. Main may request the supporting source/range again when it needs to verify or reinterpret it.

A physically observed source may be only partially presented to Main. Provider projection metadata reports that fact. When Main requests an already-covered exact range, provider-owned rematerialization can return the requested content from canonical Observation without new external I/O. Thus `current_material` is an index, not a claim that every indexed body is cognitively present in the current call.

`memory_updates` is optional on any Main turn and is not a phase. Main decides whether a result is worth retaining. Runtime mechanically validates EvidenceSpan identity/ranges and Finding/Conclusion references only.

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
establishes[]?
does_not_establish[]?
confirmation
```

The provider contract is the authority for capability meaning. `establishes` / `does_not_establish` let the Provider teach causal scope without putting domain knowledge in Core.

## Environment

`environment.providers` is supplied by installed Providers. Core has no special `project` projection.

## Epistemic coordinates

- `runtime_observations`: compact current observations;
- `current_material`: selected `mat-*` coordinates;
- `runtime_effects`: executed `eff-*` coordinates;
- `prior_conversation` and persistent Memory: context, not automatic current-world proof.

## Causal interpretation and completion

Main reads physical effects literally: `resource` says what was affected, `operation` what occurred, `persistence` how long it survives, and `changed` whether that resource state changed. Capability success is not automatically task success. Temporary, isolated, simulated or different-resource effects can support experimentation but cannot substitute for a requested persistent/external effect.

`complete` carries `grounding_ids` and `effect_ids` as optional coordinates. For claimed world changes, Main should cite only effects whose resource/state/persistence actually establish that change. Runtime validates coordinate identity/existence only; Main remains responsible for semantic interpretation.

`complete` ends the current turn/task response, not the conversation. `await_user` is reserved for active work that genuinely cannot progress without user input or supervision.
