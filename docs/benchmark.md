# Benchmarking — Eyle 2.7.5 Rev1.4.1

Benchmarks must measure capability, reliability and physical cost rather than only whether a final string was produced.

## Record at minimum

- task outcome / failure code;
- Main LLM calls and physical attempts;
- provider prompt/cache/output tokens;
- tool requests and executions;
- Observation count and replay rate;
- Material/grounding count;
- Coverage/Frontier behavior;
- Investigation transitions;
- Task transitions and completion criteria;
- committed grounding count;
- whether Final required a Grounded Completion correction;
- wall-clock duration.

Rev1.4 has no Claim outcome/call to record.

## Reliability regressions

Canonical tests should cover:

1. simple conversational Final requires no Task/Investigation;
2. open Task blocks Final;
3. every Task requires explicit completion criteria;
4. completed parent cannot retain an open direct child;
5. open Investigation blocks Final;
6. established Investigation requires real Material;
7. committed Investigation/Task Material cannot disappear from Final grounding;
8. Runtime does not infer semantic truth or relevance;
9. capability failure/rollback remains observable rather than silently converted into success;
10. removed Claim contracts cannot reappear.

## Token regressions

Track separately:

- fixed repeated contract tax;
- active capability schema/index cost;
- fresh tool-result cost;
- observation-map cost;
- grounding-index cost;
- epistemic/intentional state cost;
- retained conversation context;
- MemoryView cost when memory is explicitly activated.

The expected Rev1.4 structural win is one fewer LLM request for every normally delivered Final because the universal Claim review no longer exists.

## Memory Kernel regressions

Keep the Rev1.3.6 proofs:

- 10,000 stored memories with <=30 materialized per view;
- correct MemoryFrontier continuation;
- cross-region relation traversal;
- atomic ChangeSet rollback on revision conflict;
- persistent append-only history;
- restart recovery without transcript;
- incompatible memory schema fails closed.

## Benchmark law

> Physical invariants belong in Runtime; semantic recovery and completion meaning belong to Main.

A benchmark should flag any optimization that reduces tokens by hiding required reality or that increases reliability only by adding another unconstrained semantic judge.
