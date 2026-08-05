# Benchmark — Eyle 2.7.4

The benchmark exercises the same public agent flow used in production. It records:

- configured and resolved provider model;
- per-call `finish_reason`, token usage, and latency;
- factual correctness, completion, grounding, workflow, and safety separately;
- confirmation, hashes, dry-run, rollback, tests, and post-write reread.

Run the complete release gate:

```bash
python main.py benchmark --output context/benchmark_latest.json
```

During development, run only the focused smoke cases to avoid spending tokens on the full suite:

```bash
python main.py benchmark \
  --cases 01_audio_14_linhas,03_dois_arquivos,06_edicao_confirmada \
  --output context/benchmark_smoke_rev4.json
```

A subset report is marked with `gate_scope=smoke`; all selected cases must pass. The ten-case report remains the release gate.

A release gate should not be approved when any critical write-safety check fails, a false success is published, or the resolved model is missing for cases that called the LLM.

The packaged test suite uses deterministic doubles. A real-model benchmark must still be run in the deployment environment because provider behavior, latency, reasoning-token accounting, and JSON conformance cannot be proven offline.
