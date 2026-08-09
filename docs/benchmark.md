# Benchmark — Eyle Rev5.2.3

Benchmarks measure observable convergence, grounding and operating cost. They are not a hidden reasoning subsystem.

## Canonical AgentSession benchmark

Use the real configured model:

> Analise o projeto e explique onde AgentSession é definida, onde ela é utilizada e qual é o papel dela no fluxo real da Eyle. Mostre apenas conclusões sustentadas por evidências do código, citando arquivos, símbolos e trechos relevantes. Se alguma conclusão não puder ser confirmada com as evidências disponíveis, diga que é insuficiente e investigue mais antes de responder. Não faça nenhuma alteração no projeto.

Record Main Agent turns, tools, Investigation targets/status transitions, Claim Review/recovery calls, token usage, duration, Evidence/Claim counts, Semantic Gaps and final outcome.

Expected invariants:

1. the Main LLM creates material Investigation targets in the same decision that begins project investigation;
2. targets persist by ID and goal across turns and cannot silently disappear;
3. search/navigation remains freely chosen by the Main LLM—no target hardcodes a file/tool path;
4. a grounded final cannot pass with a declared `open` target;
5. Claim Review receives target-linked Evidence and may challenge a target through `target_id`;
6. a challenged target is reopened with the reviewer's reason and the existing `investigation_map` remains available;
7. a material gap absent from the contract uses `target_id=null`; runtime does not invent a target;
8. definition-only Evidence cannot justify runtime-flow Claims;
9. the task should converge before the unchanged physical fuses in a normal run; increasing tool/turn limits is not part of the Rev5.2 fix;
10. final acceptance requires supported material Claims and no unresolved material Semantic Gap.

Success is behavioral. Do not hardcode `agent.py`, `main.py`, an exact tool sequence, or a fixed number of Investigation targets.

## Broad audit benchmark

Use:

> Qual a versão disponível e verifique se tem bugs e códigos legado que devem ser removidos.

This tests whether the Main LLM declares the material audit dimensions it actually needs, updates those targets with Evidence, and avoids treating a marker search as proof of a broad absence claim.

## Context benchmark

Verify that an explicit ongoing conversational instruction survives tool turns while an older completed/failed task does not silently replace the new `request`.

## Write benchmark

Test one single-file and one multi-file edit through the unchanged path:

`action=patches → transaction dry-run → confirmation → apply → compile/tests/reread → rollback on failure`.

## Rev5.2.1 regression

Required recovery case: an initially established target is challenged by an `insufficient` Claim with `target_id`; the runtime must reopen that target even when `semantic_gaps=[]`, preserve the reviewer reason, let the Main LLM choose a different observation, and allow success only after a later supported Claim Review. Repeated blocked reads during this follow-up must not produce an instruction to stop investigating.

## Rev5.2.2 P0 regressions

Required cases include: the historical ~4526-token recovery state must admit the next 1100-token Main LLM call while reserving one 900-token verifier; `O que AgentSession faz?` cannot succeed ungrounded when a scope reviewer identifies current workspace dependency; write wording missed by legacy regex can become `workspace_scope=write` through the Main LLM; open targets block both patch proposal and confirmed resume; concurrent processes cannot lose a persisted update; `.env`/secret content cannot be exposed by read/search/Git diff; and persisted Evidence is rehydrated only when hashes remain fresh.
## Rev5.2.3 P0 regressions

Required cases: source coverage from an older prompt must not block a reread once that source body is absent from the current prompt; Evidence named by an insufficient Claim/Semantic Gap or a reopened target must remain pinned through the next semantic-follow-up prompt; alternating unchanged `project_stats`/`inspect_project` calls must stop consuming physical tools; `ok=true` without new Evidence or state mutation must not count as progress; and a repeated same-scope `run_tests` execution must be reusable until an observable state-changing action invalidates it.

