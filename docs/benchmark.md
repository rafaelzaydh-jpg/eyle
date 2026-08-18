# Benchmarks — Rev3.7.2

Rev3.7.2 is a cut/canonicalization release. Its benchmark goal is to prove that removing alternate paths did not reduce reachability or reintroduce cost growth.

## Required scenarios

1. `"oi"` — one normal cognition, compact static floor.
2. short conversation reference — recent physical conversation resolves naturally.
3. topic switch — unrelated Task/Memory does not enter automatically.
4. 200+ message conversation — token-budget slice stays bounded; omitted count/frontier/reachability remain correct.
5. Memory scaling — compare 100, 1,000 and 10,000+ nodes on the same trivial request; prompt size must not grow proportionally.
6. explicit old Memory recall — absent from baseline prompt, found when Main asks for it.
7. protocol repair — same fingerprint is bounded and repair does not resend large observations.
8. invalid Memory sidecar — valid ECC executes with zero extra LLM call caused solely by Memory rejection.
9. large observation/search/file result — page is bounded physically and exact remainder remains reachable.
10. sandbox Build — exact confirmed mutation, post-write verification and rollback invariants remain intact.

## Metrics

Per provider call:

```text
provider_prompt_tokens
provider_completion_tokens
provider_total_tokens
cached_tokens
cognition_reason
estimated_static_tokens
estimated_conversation_tokens
estimated_memory_tokens
estimated_observation_tokens
estimated_feedback_tokens
estimated_capability_tokens
```

Per execution:

```text
total_provider_tokens
normal_cognition_tokens
protocol_recovery_tokens
number_of_llm_calls
number_of_protocol_repairs
memory_rejections
conversation_messages_materialized
conversation_messages_omitted
```

Provider-reported usage remains the ledger authority; local component estimates are diagnostic only.

## Static cognitive floor

Measure the composed floor, not isolated components:

```text
system/wire semantics
+ current contract
+ compact capability surface
+ minimal runtime packet
```

A larger Memory Graph must not raise this floor simply because more knowledge exists.

## Cut regression criterion

A Rev3.7.2 change is not an improvement if it makes an old hidden/duplicate path disappear by also making useful state unreachable. The desired transformation is:

```text
multiple competing paths
        ↓
one canonical path
```

not:

```text
multiple paths
        ↓
lost capability
```
