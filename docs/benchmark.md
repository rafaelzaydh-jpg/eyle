# Benchmark — Eyle Rev5.1

Benchmarks measure public AgentSession behavior, grounding and operating cost. They are not a hidden reasoning subsystem.

## Canonical AgentSession benchmark

Use the real configured model:

> Analise o projeto e explique onde AgentSession é definida, onde ela é utilizada e qual é o papel dela no fluxo real da Eyle. Mostre apenas conclusões sustentadas por evidências do código, citando arquivos, símbolos e trechos relevantes. Se alguma conclusão não puder ser confirmada com as evidências disponíveis, diga que é insuficiente e investigue mais antes de responder. Não faça nenhuma alteração no projeto.

Record agent turns, tools, administrative capability probes, Claim Review/Repair/recovery calls, token usage, duration, Evidence/Claim counts, Semantic Gaps and final outcome.

Expected invariants:

1. structured capability is behaviorally verified and locally validated;
2. the verifier returns the canonical `claims/findings/semantic_gaps` envelope;
3. malformed local Claims or Semantic Gaps are recovered in isolation without discarding valid siblings;
4. definition-only Evidence cannot justify claims about runtime usage/flow;
5. incomplete scope must return `insufficient`/`scope_gap` feedback, preserve `investigation_map`, and allow the Main Agent to investigate a different source/range instead of repeating the same search;
6. no fixed Claim or Evidence count quota exists;
7. final acceptance requires supported surviving material Claims and no unresolved material Semantic Gap;
8. prior conversation may provide ongoing instructions but must not silently replace the current request as the active task;
9. no agent turn may silently truncate more than four requested tool calls.

## Bug-audit benchmark

Use: `Procure bugs no projeto.` A marker-only search (`TODO`, `FIXME`, `BUG`) may locate candidates but cannot support a broad absence conclusion. Claim Review should detect a scope gap if executable behavior/error paths were not investigated enough.

## Write benchmark

Test one single-file and one multi-file edit. Both must use the same path:

`action=patches → transaction dry-run → confirmation → apply → compile/tests/reread → rollback on failure`.
