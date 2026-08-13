# Configuration — Eyle 2.7.5 Rev1.4.3

Canonical identity:

```json
{
  "app_version": "2.7.5",
  "config_schema_version": "2.7.5-r1.4.3",
  "revision": "rev1.4.3-semantic-completion"
}
```

Unknown fields and mismatched identity are errors. Removed fields are not aliases. Session and queue use `2.7.5-r1.4.3` because Rev1.4.3 adds persisted `Investigation.conclusion`. Memory Kernel keeps its independent physical store schema `2.7.5-r1.3.6-memory-kernel-v1` because Rev1.4 does not change that database layout.

## LLM

`llm` config owns transport/model/context/retry/stream/output settings. Main uses the `agent` structured profile. Rev1.4 has no Claim/verifier profile or second delivery LLM call.

## Agent physical limits

Relevant current fields include project/tree/read/search limits, sandbox limits, `task_deadline_seconds` and `max_total_tokens`.

There is no cumulative semantic `max_prompt_tokens`/`max_completion_tokens` quota and no Claim reserve. The task-wide token fuse (maximum 90,000) and deadline are physical runaway containment; they do not define semantic completeness or strategy.

## Grounded Completion

Grounded Completion has no configuration switch. It is part of the Rev1.4 Core contract:

- open Main-owned Tasks/Investigations block Final;
- established Investigation must reference real Material and contain a non-empty Main-authored conclusion;
- completed Task grounding, when declared, must reference real Material;
- Final preserves all Material IDs explicitly committed by established Investigations and grounded completed Tasks.

Runtime validates these physical declarations only. There is no confidence threshold or semantic grader config.

## Memory

Memory Kernel uses SQLite and its own schema identity. Memory is loaded only through explicit memory capabilities; it is not automatically injected into Main's prompt.

## Future validators

No validator registry or safety-review config exists in Rev1.4. Capability-owned validators may be added later only for concrete domain criteria and should return findings through normal Observation.

## Compatibility policy

Rev1.4.3 does not accept removed `agent.claims`, Claim verifier settings or prior Claim-bearing session/queue contracts. No compatibility downgrade chain exists in Core.
