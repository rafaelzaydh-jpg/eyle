# ECC Benchmarks

ECC earns changes through measured behavior, not architectural aesthetics.

## Required scenarios

1. **AgentSession analysis:** find its definition, usages and actual flow using bounded Explore operations and then Conclude.
2. **Bug search:** reason over project structure/tests/source rather than merely search `BUG`/`TODO`.
3. **Write:** inspect, request a Runtime-guarded transaction, survive confirmation, apply/test/re-observe and Conclude.
4. **Durable learning:** ordinary stable statements such as a user preference can become graph Memory without a special user command.
5. **No prompt contamination:** a stored unrelated preference does not appear in a fresh `Oi` prompt until Main explicitly recalls it.
6. **Explicit recall:** a later session can activate the relevant Memory region and use it.
7. **Memory pagination:** large graph selections are paged through Coverage + exact Frontier continuation and never expose private handles.
8. **Coverage union:** adjacent/overlapping observed file ranges prevent redundant physical reads where the provider can prove coverage.
9. **Source identity:** a workspace that itself contains Eyle code is still `workspace`; `eyle` remains the currently running instance source tree.
10. **Unknown provider:** attaching a new deterministic provider must not require Core domain branches.

## Metrics

Track Core LOC/files, fixed prompt/schema size, LLM calls, physical operations, continuation pages, graph deltas, Evidence creation, prompt tokens, duration, no-progress signals and answer/patch correctness.

## Clean transcript, same Memory Graph

The Web shell may clear the visible conversation transcript without deleting `core_memory.sqlite3`. This lets tests start a fresh dialogue while keeping durable learned Memory. Since raw conversation history is no longer projected as a cognitive memory layer, no background-ablation environment switch is required.
