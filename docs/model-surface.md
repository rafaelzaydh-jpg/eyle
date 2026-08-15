# Model Surface

Main sees exactly three structured ECC variants. Objective and Memory are sidecars, not extra moves.

```json
{"type":"explorar","operation":"...","arguments":{},"objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"unchanged","operations":[]}}
{"type":"construir","operation":"...","arguments":{},"objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"unchanged","operations":[]}}
{"type":"concluir","response":"...","objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"unchanged","operations":[]}}
```

## Objective

- `unchanged + state:null` — keep the current Objective State.
- `updated + state:{summary,status,children,constraints}` — replace it.
- `cleared + state:null` — remove it.

Objective means what Main is still trying to achieve. It is not a planner, task ledger, Runtime policy, or completion gate.

## Memory

- `unchanged` requires an empty operations list.
- `updated` requires graph changes.

Memory persists across AgentSessions. Main chooses semantic content; Runtime validates and stores the requested delta.

## Context packet

The model receives separate fields for the exact current request, optional Objective, conversation background, Memory Graph, physical navigation/coverage, newest observations, Runtime effects/feedback, and the attached capability body.
