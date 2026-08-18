# Model Surface — Rev3.7.2

Main receives a compact deterministic packet and emits tolerant wire JSON. Eyle owns canonical ECC/Memory validation.

## Context packet

The normal packet is assembled only by `ContextMaterializer` from physical inputs:

```text
required system/wire contract
current_request
recent current-conversation slice
current task mechanics
incremental observations/effects
explicit Main Memory activation
runtime feedback
compact coverage/frontiers
capability wire surface
```

Conversation/observation materialization is token-budgeted. Global Memory bodies are not projected automatically.

## Preferred ECC wire

```json
{"type":"explorar","operations":[{"operation":"read_file","arguments":{"source":"workspace","path":"calc.py"}}],"memory_delta":[]}
```

```json
{"type":"construir","operation":"transaction","arguments":{},"memory_delta":[]}
```

```json
{"type":"concluir","response":"Final answer","memory_delta":[]}
```

Eyle may recover safe representation variants and then validates the strict internal `{decision, memory_delta}` envelope. Canonicalization may repair representation, never missing meaning.

## Memory wire

Memory uses the same cognition response sidecar. Current nodes may carry `scope`, `domain`, `context_key`, retention, epistemic fields, revision history, supports and Main-authored recall cues.

`scope=all` searches user + current world. `scope=global` searches all current worlds. These are current reachability contracts, not compatibility modes.

Memory activation is explicit. Runtime does not maintain a hot tier, hidden activation list or semantic working set.

## Sidecar isolation

Decision and Memory are validated independently:

```text
valid ECC + invalid memory_delta
        =
execute ECC + record Memory rejection
```

A Memory parser/storage failure does not trigger a paid LLM retry solely to rescue the sidecar.

## Protocol repair

A malformed ECC protocol response may return structured feedback to the same logical execution. Repeated identical protocol fingerprints are mechanically bounded. The repair packet is minimal and does not rematerialize unrelated observation bodies.

## Capability surface

Main receives the compact public operation/argument wire surface. Full provider validation remains in the canonical Registry. Hiding documentation detail is not hiding a capability: every registered public capability remains addressable.

## Frontier

A `fr-*` handle means exact continuation exists after the materialized page. Page size is physical presentation, not a semantic knowledge limit.
