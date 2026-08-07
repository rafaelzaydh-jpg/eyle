# Benchmark — Eyle Rev4.11.7

The benchmark is a development tool under `eyle/devtools/`; it is not part of the agent core.

A useful Rev4.11.7 benchmark must measure the public behavior of the one active loop:

- request preservation;
- correct tool use and fresh evidence;
- factual correctness;
- answer usefulness and evidence quality;
- typed claim coverage and claim-to-evidence mapping;
- explicit result-limit compliance;
- retention of relevant source across intervening tool calls;
- explicit write confirmation;
- dry-run, hashes, atomic apply, tests, rollback, and reread;
- false success rate;
- logical LLM calls, backend requests, raw/cached/effective tokens, and latency;
- completion of common multi-file writes within three logical calls;
- patch-only enforcement after the investigation phase;
- semantic blocking of overlapping reads.

```bash
python main.py benchmark --output context/benchmark_latest.json
```

The packaged suite uses deterministic doubles. A real Qwen benchmark must run in the deployment environment because model interpretation, tool selection, patch quality, latency, token usage, and JSON conformance cannot be proven offline.

Coverage and efficiency comparisons remain development commands:

```bash
python main.py compare-coverage baseline.json candidate.json
python main.py compare-efficiency baseline.json candidate.json --tolerance 0.10
```
