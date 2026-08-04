# Benchmark and validation

Eyle includes a controlled ten-scenario benchmark for real agent behavior:

```bash
python main.py benchmark
```

The report is written to `context/benchmark_latest.json` by default.

The gate checks:

- correct project reading;
- factual and grounded answers;
- invented references;
- false success states;
- unauthorized writes;
- confirmation, hashes, dry run, rollback, and post-write reread.

Run the benchmark several times with the exact model and quantization used in production. Deterministic tests validate the orchestration, but they do not prove that a model will reliably choose the correct tool sequence.

## Automated tests

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q
```

The 2.7.0 release passed 167 non-web tests. The local-model benchmark must be run
on the machine hosting the configured model endpoint.
