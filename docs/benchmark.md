# ECC Benchmarks

ECC must earn migration by measured behavior, not architectural aesthetics.

## Required scenarios

1. **AgentSession analysis:** find its definition, usages and real flow with Explore operations, retain useful knowledge, then Conclude.
2. **Bug search:** reason over project structure/tests/source rather than merely search `BUG`/`TODO`.
3. **Write:** inspect, request `operation=transaction` under `type=construir`, suspend for Runtime confirmation, apply, re-observe as needed, Conclude.
4. **Memory isolation:** durable conversation instruction may survive turns, while an old task never becomes the new active request.
5. **Cache bias:** background/cached material must not be treated as proof of mutable current state.
6. **Coverage union:** adjacent/overlapping observed file ranges must prevent redundant physical reads.
7. **Exact recall:** a retained `ev-*` recall returns only the selected span.
8. **Unknown provider:** a new provider (for example PetBot) must project into E/C mechanically without Core changes.

## Metrics

Track Core LOC/files, fixed prompt/schema size, LLM calls, physical operations, compact replays, knowledge/evidence creation, prompt tokens, duration, no-progress signals and answer/patch correctness.
## Conversation-background ablation

For memory-causality experiments, Eyle can suppress only the conversation background projected to the LLM while preserving the stored conversation, Memory Graph, Objective State, provider cache behavior, and capabilities.

Set the diagnostic environment variable before launching Eyle:

```text
EYLE_BENCHMARK_SUPPRESS_CONVERSATION_BACKGROUND=1
```

When enabled, `conversation_background` is projected as an empty list for every ECC call. The underlying `AgentSession.conversation_background` is not deleted or mutated. Prompt telemetry records `conversation_background_suppressed_for_benchmark`, stored item count, and projected item count.

Disable the variable after the experiment (`0`, unset, or any value other than `1/true/yes/on`) to restore normal projection. This switch exists only for diagnostic ablation and must not be used as semantic routing.


## Clean transcript, same Memory Graph

For a visually and semantically clean cross-session-memory benchmark, the Web shell exposes **limpar chat**. It calls `DELETE /conversa`, which clears only `memory/conversa.json` after verifying that no job is pending/processing. `core_memory.sqlite3` is not touched. Completed queue/job history also remains intact, while client-side tracked-job UI state is cleared.

This is intentionally a shell/diagnostic operation, not an ECC semantic action and not a Runtime memory decision. It is useful together with the background-ablation switch, but clearing the transcript already makes future `conversation_background` naturally empty.
