# Benchmark — Eyle Rev5.2.9

Benchmarks measure observable convergence, grounding and operating cost. They are not a hidden reasoning subsystem.

## Canonical AgentSession benchmark

Use the real configured model:

> Analise o projeto e explique onde AgentSession é definida, onde ela é utilizada e qual é o papel dela no fluxo real da Eyle. Mostre apenas conclusões sustentadas por evidências do código, citando arquivos, símbolos e trechos relevantes. Se alguma conclusão não puder ser confirmada com as evidências disponíveis, diga que é insuficiente e investigue mais antes de responder. Não faça nenhuma alteração no projeto.

Record Main Agent turns, tools, Investigation targets/status transitions, Claim Review/recovery calls, token usage, duration, Evidence/Claim counts, Semantic Gaps and final outcome.

Expected invariants:

1. the Main LLM creates material Investigation targets in `investigation_updates` in the same decision that begins project investigation;
2. runtime persists the canonical targets by ID/goal across turns; omitted targets do not need to be reconstructed;
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

## Rev5.2.4 historical regression

Atomic oversized batches must still execute zero tools. The reviewer-coupled bonus mechanism from Rev5.2.4 is intentionally absent from the current architecture.

## Rev5.2.5 transactional-authority regressions

Required cases: valid Investigation siblings are committed even when another sibling is structurally rejected; omitted targets remain canonical; committed Evidence cannot silently disappear; multiple valid target changes in one Main-LLM decision deposit only one progress epoch; Claim Review does not participate in extension decisions; a batch that would exceed the base fuse can continue only after runtime finds new committed progress plus open debt; the same progress epoch cannot mint two extensions; the configured +8 ceiling is enforced; no-progress target creation alone earns no extension; atomic batches remain all-or-nothing; and public history exposes committed progress/earned extensions while retaining one-click expand/collapse.

## Rev5.2.6 observation-ledger/preflight regressions

Required cases: `search A -> search B -> search A` performs only two physical executions and rehydrates A on the third request; a complete zero-match search produces citable `search_observation` Evidence; `SYMBOL_NOT_FOUND` is citable and reusable; ObservationLedger identity survives session persistence and is invalidated by `workspace_epoch`, not by session-only changes; a repeated observation does not consume or trigger `earned_extension`; authority is computed only after replay/invalid/batch-duplicate removal; source already visible in the current prompt does not cause a physical reread; repeated identical authority-rejected batches fail as `ADMINISTRATIVE_LOOP` before the general turn fuse; and Claim Review remains byte-identical to Rev5.2.5.

## Rev5.2.7 two-brain Claim-follow-up regressions

Required cases: production structured semantic profiles are exactly `agent` and `claim_verifier`; removed `agent.claims.repair` config is rejected rather than silently accepted; a contradicted Claim with `target_id` reopens exactly that target and pins cited Evidence; the same reviewer debt against the same canonical state fails as `CLAIM_REVIEW_STALLED`; Claim-directed rework can use only unused global LLM-call capacity while reserving a later verifier pass; local Claim protocol recovery preserves `target_id`; and re-establishing a reviewer-reopened target with the same Evidence creates no `committed_progress` or tool credit.


## Rev5.2.8 canonical-runtime cleanup regressions

Required cases: changing only Investigation `reason/status` with unchanged Evidence/observations must not count as objective runtime progress; the same authority-rejected payload after a new observation must not be treated as the same Decision-Ledger state; changed remaining/effective tool authority must also create a different rejection identity; a batch containing any malformed tool call must be rejected before tool authority and execute no novel call; the model-facing tool schemas must expose only the canonical English ABI and reject old aliases; open targets must be allowed to accumulate Agent-selected Evidence before `established`; and removed lexical workspace/write helpers plus the semantic-read signature compatibility wrapper must remain absent.

## Rev5.2.9 progress-earned authority regressions

The Rev5.2.9 regression set proves: target Evidence deltas are additive and monotonic; status/reason updates never require resending old Evidence; the same Evidence ID cannot mint committed progress twice even through another target; old persisted sessions backfill the credit-once Evidence set from progress history; stale/missing Evidence cannot mint progress; every unspent progress epoch grants the configured +tool step once with no cumulative ceiling; the removed `max_earned_tool_extension` key is rejected rather than kept as compatibility; Claim rework receives deterministic remaining/pending capacity; and the compact Agent prompt stays below its fixed-token regression ceiling.
