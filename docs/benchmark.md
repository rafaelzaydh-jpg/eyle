# Rev5.6 benchmark contract

Benchmarks measure whether work follows task necessity rather than infrastructure pressure.

## Mandatory simple regression

```text
quantos tokens tem o projeto?
```

Expected:

```text
Main LLM
→ count_tokens
→ Observation/Evidence
→ Final
```

Acceptance: no Investigation unless the Main LLM independently declares real debt, one physical `count_tokens`, no router/classifier, no workspace classification, no semantic phase scheduler.

## Complex audit regression

For a usage/reachability audit:

- Main LLM creates only material Investigation debts it identifies;
- Runtime never creates a target;
- open declared targets block Final;
- Claim can challenge support/closure or return omitted scope with `target_id=null`;
- Main LLM decides whether that feedback requires a target or only answer correction.

## Resource interpretation

Semantic sufficiency is never inferred from token count, but training executions are physically contained. One message/job may spend at most **98000 physical estimated tokens**, with at most 90000 prompt and 8000 completion tokens, and each backend request must fit the 32768-token Llama Server window. Exhausting a fuse fails the task; the Main LLM does not earn extensions.

Record completion/accuracy, physical tools, replays, repeated rejected decisions, prompt duplication, Investigation use, post-sufficiency work and Evidence/Observation growth. The audit regression should demonstrate that the Main LLM creates persistent Investigation debt for unresolved multi-turn candidate conclusions rather than consuming the entire envelope in broad search.
