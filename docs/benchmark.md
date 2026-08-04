# Benchmark and validation

Eyle includes deterministic tests and a controlled real-agent benchmark. They answer different questions and both matter.

## Automated tests

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q
```

Packaging result for release 2.7.3 revision 53:

- 204/204 executable non-web tests passed;
- one web test module was skipped because Flask was not installed in that environment;
- `python -m compileall -q .` passed;
- `python engine/release_identity.py` passed.

Installing `requirements-dev.lock` also installs the locked web dependencies through `requirements.lock`, allowing the web module to run in CI.

## Real-model benchmark

```bash
python main.py benchmark
```

The report is written to `context/benchmark_latest.json` by default. Run it several times with the exact endpoint, model, quantization, hardware, configuration, and representative repository intended for production.

The gate covers:

- correct project reading;
- factual and grounded answers;
- invented references and unsupported anchors;
- false success states;
- tool schema and permission behavior;
- confirmation, hashes, dry run, atomic write, tests, rollback, and post-write reread;
- queue/worker timing and telemetry where available.

Deterministic tests prove orchestration properties; they do not prove that a particular model will always choose the correct tool sequence or meet a latency target. Do not claim a percentage speed improvement without measuring the final environment.

## Recommended release check

```bash
python engine/release_identity.py
python -m compileall -q .
python -m pytest -q
python main.py benchmark
python main.py status
```

Review warnings, P50/P95/P99, retries, queue blocking, cache behavior, and any fallback causes before enabling `agent.rollout_mode: "full"`.
