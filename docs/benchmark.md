# Benchmarks — Rev3.7.5.1

Eyle benchmarks both **behavioral correctness** and **execution efficiency**.

The objective is not to minimize tokens at any cost. The objective is to avoid duplicated/irrelevant materialization while preserving the information and capabilities required to complete the task correctly.

## Principles

A benchmark is meaningful only when it measures the whole request path:

```text
request
+ semantic system
+ capability/runtime surface
+ materialized conversation
+ explicit Memory
+ observations/feedback
+ Adapter structured contract
+ provider usage
```

Provider-reported token usage is the accounting authority. Local estimates are diagnostic decompositions.

## Required scenarios

### Conversation and identity

1. **Greeting** — a trivial `"hi"` should normally require one Eyle cognition and no tools.
2. **Immediate reference** — recent references such as `money -> it` resolve from native-role conversation.
3. **Topic return** — switching topics and returning to a prior adjacent subject preserves causality.
4. **Negative history** — asking for an entity never mentioned does not produce a nearby unrelated fact.
5. **Active-request boundary** — a new trivial request after a context-heavy turn answers the new request.
6. **Self identity** — `"analyze your code"` targets `source="eyle"` while `"analyze the project"` targets `source="workspace"`.
7. **Ordinal reference** — references such as `"the second function"` preserve the ordering established by the previous answer.

### Context scaling

8. **200+ message conversation** — recent slice stays within configured materialization budget; omitted count remains correct.
9. **Memory scaling** — compare a trivial request with 100, 1,000, and 10,000+ Memory nodes; prompt size must not grow proportionally.
10. **Explicit old Memory recall** — old state absent from baseline prompt remains reachable when Main asks for recall.
11. **Large physical result** — a large search/file result is bounded in the prompt while exact remainder remains reachable through Material/Coverage/Frontier.

### Structured boundary

12. **First-pass valid wire** — normal structured calls should usually validate without Adapter repair.
13. **Mechanical repair** — malformed representation gets at most one Adapter repair.
14. **Repair isolation** — repair does not replay Eyle conversation/Memory/tools/Task context.
15. **Invalid after repair** — Eyle can make one fresh decision without losing Session/observations.
16. **Truncation** — provider `finish_reason=length` is reported directly and does not start a format repair.

### Memory and execution

17. **Invalid Memory sidecar** — valid ECC still executes with no extra LLM call solely for Memory repair.
18. **Recall single materialization** — activated node bodies appear once in the next cognition.
19. **Valid fixed point** — repeated identical action/result without progress terminates through Runtime fixed-point safety.
20. **Long useful cognition** — many turns with genuinely new Runtime information remain allowed.

### Persistent Build

21. **Sandbox candidate** — isolated command/test work does not mutate the real workspace.
22. **Promotion** — exact staged bytes/hash/freshness/confirmation are verified before real mutation.
23. **Transaction rollback** — failed persistent mutation does not leave a partial workspace state.

## Per-provider-call metrics

```text
provider_prompt_tokens
provider_completion_tokens
provider_total_tokens
cached_prompt_tokens
uncached_prompt_tokens
cognition_reason
adapter_upstream_attempts
adapter_structured_repairs
adapter_schema_enforcement
adapter_structured_contract_characters
adapter_repair_context_mode
prompt_estimated_tokens
```

Prompt component estimates should include at least:

```text
conversation
current_request
ecc/capability surface
runtime environment
memory environment
memory view
latest observations
runtime effects
runtime feedback
```

## Per-execution metrics

```text
total_provider_tokens
normal_cognition_tokens
wire_retry_tokens
number_of_llm_calls
number_of_wire_retries
physical_capability_calls
operation_replays
memory_rejections
conversation_messages_materialized
conversation_messages_omitted
older_history_available
failure_code
duration
```

## Structured-repair rate

Adapter repair is an exception path, not the normal path.

Track:

```text
repair_rate =
logical structured calls requiring Adapter repair
/
all structured logical calls
```

A rising repair rate is a provider-boundary regression even if requests eventually succeed, because it increases latency, provider generations, and token use.

## Static cognitive floor

Measure the composed floor, not isolated files:

```text
semantic system
+ current capability/runtime contract
+ minimal Runtime packet
+ Adapter-delivered structured schema
```

A larger Memory Graph must not raise this floor merely because more knowledge exists.

## Token-efficiency rule

Optimization should remove duplication before it removes reachable information.

Prefer:

```text
one canonical representation
one body materialization
one repair context
```

over:

```text
smaller prompt
but missing context/capabilities
```

The target transformation is:

```text
multiple competing copies
        ↓
one canonical copy
```

not:

```text
multiple copies
        ↓
lost capability
```

## Fixed-point benchmark

A valid Eyle investigation is bounded by **progress**, not by an arbitrary turn count.

Synthetic tests should prove both sides:

```text
same valid action/result repeatedly
-> bounded termination
```

and:

```text
many turns + genuinely new Runtime facts
-> allowed to continue
```

This distinguishes execution-loop safety from a hidden `MAX_TURNS` ceiling.

## Comparing releases

Use the bundled CLI:

```bash
python main.py benchmark
python main.py compare-coverage <baseline.json> <candidate.json>
python main.py compare-efficiency <baseline.json> <candidate.json>
```

Coverage regressions take priority over superficial token reductions. Efficiency comparisons are useful only after the candidate still reaches the same required behavior.

## Rev3.7.8 long-task cases

The canonical benchmark matrix now includes `long_file_2k`, `long_file_10k` and `multi_file_long`.
Targets are deliberately placed near the end of the physical source so a successful run exercises
bounded materialization, search/Frontier reachability, or another valid physical path rather than
assuming the first read page is the whole file. These cases are model-facing benchmarks, not
Runtime semantic completion gates.

## Rev4 cognitive-surface benchmark

Rev4 measurements must compare the Rev3.7.8 combined cognition baseline with the purpose-bounded `navigation`, `explore`, and `build` surfaces. `eyle.devtools.cognitive_floor.measure_static_cognitive_floor()` reports each current surface independently instead of summing them into a fictional mega-prompt.

Required representative cases remain:

- trivial direct conclusion (one Navigation cognition, no Task);
- contextual conversation;
- short exploration;
- long repeated exploration with an active Task;
- Explore → Build → verification → Conclude;
- the same long task across recoverable restart.

The target for long/tool-heavy cases is at least 25% lower prompt input than the Rev3.7.8 baseline without lowering task success. Rev4.0.0 does not remove `current_request` or aggressively strip conversation solely to hit this number; further context minimization is accepted only when measured.
