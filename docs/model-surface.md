# Model Surface — Rev4.0.0

Rev4 uses **three structured protocol surfaces** around the same three ECC movements. Surfaces are not phases and do not give Runtime semantic authority.

## Provider chronology

Every call keeps the canonical provider chronology:

```text
1. surface-specific Eyle system instruction
2. stable physical surface/catalog
3. bounded dynamic Runtime state
4. recent native user/assistant conversation
5. current_request as the final user message
```

`current_request` appears exactly once.

## Navigation

Navigation contains no detailed tool schemas. Main chooses exactly one ECC movement:

```json
{"type":"explorar","memory_delta":[]}
```

```json
{"type":"construir","memory_delta":[]}
```

```json
{"type":"concluir","response":"...","memory_delta":[]}
```

Optional `task_binding` is a semantic persistence sidecar. Trivial conversation can conclude in this one call.

## Explore

Only observe/execute capabilities are exposed:

```json
{
  "operations":[
    {"operation":"read_file","arguments":{"source":"workspace","path":"a.py"}}
  ],
  "memory_delta":[]
}
```

or:

```json
{"return_to_ecc":true,"memory_delta":[]}
```

Batches are Main-authored and should contain independent operations. Runtime never chooses a next tool.

## Build

Only mutate capabilities are exposed:

```json
{"operation":"transaction","arguments":{},"memory_delta":[]}
```

or:

```json
{"return_to_ecc":true,"memory_delta":[]}
```

After one mutation attempt, Runtime returns to Navigation.

## Active Task projection

If Main explicitly binds a `kind=task/domain=task` Memory node, Runtime can project exactly that ID as `active_task`. It performs no recall, ranking or neighbor expansion. Without an explicit binding, there is no automatic Task projection.

## Memory

Memory Graph remains explicit. `memory_view` contains explicitly activated Memory. Active Task projection is a separate exact-ID execution binding and does not turn Memory into a prompt router.

## Structured authority

Eyle owns three current schemas: `navigation`, `explore`, and `build`. Adapter receives the exact caller-supplied schema, validates representation and may perform at most one isolated format repair. The historical monolithic `ecc` structured profile is not current.

## Context policy

Rev4.0.0 first removes irrelevant **capability schemas**. It deliberately retains `current_request` on specialized surfaces. Further conversation/request/runtime minimization requires benchmark evidence and must not introduce semantic Runtime selection.
