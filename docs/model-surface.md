# Model Surface — Rev3.7.5.1

This document describes the deterministic surface around Main: what context is materialized, how current conversation is represented, and what structured ECC wire Main must return.

Eyle owns the semantics. The Adapter owns provider-specific transport and mechanical JSON/schema conformance.

## Provider message order

The normal provider-facing chronology is:

```text
1. Eyle semantic system instruction
2. stable/capability Runtime surface
3. bounded Runtime state
4. recent conversation as native user/assistant roles
5. current_request as the final user message
```

The active request appears exactly once.

This prevents recent historical assistant output from appearing after the current user request and becoming the apparent active instruction.

## Context packet

`ContextMaterializer` can include:

- Runtime environment;
- current Task mechanics;
- explicit Memory activation;
- latest observations;
- runtime effects;
- compact exploration/frontier state;
- Runtime feedback;
- recent conversation;
- current request.

Conversation, observations, and feedback are bounded by physical token budgets.

The packet does not contain a hidden semantic topic selector.

## Current ECC wire

### Explore

```json
{
  "type": "explorar",
  "operations": [
    {
      "operation": "read_file",
      "arguments": {
        "source": "workspace",
        "path": "calc.py"
      }
    }
  ],
  "memory_delta": []
}
```

### Build

```json
{
  "type": "construir",
  "operation": "transaction",
  "arguments": {},
  "memory_delta": []
}
```

### Conclude

```json
{
  "type": "concluir",
  "response": "Final answer",
  "memory_delta": []
}
```

The current JSON Schema is supplied by Eyle as data. There is one representation authority.

Core does not maintain a second textual copy of the same wire contract.

## Adapter representation contract

For structured calls, the Adapter:

1. receives Eyle's JSON Schema;
2. communicates that exact schema to the provider as the required output representation;
3. mechanically strips a code fence or extracts one balanced JSON object when safe;
4. validates the candidate against the same schema;
5. if necessary, performs exactly one isolated format-only repair using only:
   - schema;
   - previous candidate;
   - validation errors.

The Adapter does not translate historical Eyle aliases or wrappers.

Examples of intentionally unsupported compatibility behavior include:

- `explore` → `explorar`;
- `answer` → `response`;
- wrapper unwrapping such as `decision` / `ecc`;
- Python-literal parsing as an alternative semantic wire;
- Memory operation aliases.

If a provider needs help conforming to the wire, it receives the current schema rather than a growing compatibility dictionary.

## Truncation

Provider `finish_reason=length` is treated as output truncation.

It is not classified as a format defect and does not trigger the Adapter format-repair generation.

## Fresh Eyle decision after wire failure

If the Adapter's single repair still leaves a schema-invalid candidate, Eyle keeps the existing Session, observations, Task state, and physical progress.

Core may ask Main for one fresh current decision. This is a new Eyle cognition, not a second Adapter repair.

The allowance is replenished only after real Eyle execution progress; syntactic validity by itself is not progress.

## Memory wire

Memory travels as `memory_delta` beside the ECC decision.

Nodes may use:

- `scope`;
- `domain`;
- `context_key`;
- retention;
- epistemic fields;
- supports;
- relation/revision state.

Decision parsing and Memory parsing are isolated.

```text
valid ECC + invalid memory_delta
        =
execute ECC + record Memory error
```

## Explicit Memory activation

Memory is not injected globally.

Main explicitly requests recall/activation. Activated node bodies appear in `memory_view` once. The corresponding operation observation carries compact activation metadata rather than the same node bodies again.

## Capability surface

Main receives the compact public capability/argument surface.

The canonical Registry remains the full validation authority. Documentation compaction must not make a registered public capability unreachable.

The model may decide which capability is semantically appropriate; Runtime validates whether that concrete operation is physically allowed.

## Frontier

A `fr-*` Frontier means exact continuation exists after the currently materialized page.

A page size is a physical presentation choice, not a semantic knowledge limit.

## Observability

Per-call diagnostics can report facts such as:

- prompt component estimates;
- provider prompt/completion/total tokens;
- cached prompt tokens;
- cognition reason;
- Adapter schema enforcement;
- Adapter repair count;
- structured contract size;
- isolated repair-context mode;
- parse/validation status.

Diagnostics do not expose raw hidden prompts or raw provider responses in the normal user-facing history.
