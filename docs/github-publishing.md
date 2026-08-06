# Publishing Eyle Rev4.11.2

## Repository description

```text
Local-first autonomous programming agent with one AgentSession, supervised writes, external memory, tests, rollback and telemetry.
```

## Before publishing

1. Review `config.json` for local endpoints and secrets.
2. Keep only `.gitkeep` inside `memory/`, `context/`, and `workspace/`.
3. Remove caches, SQLite databases, traces, logs, backups, and generated memory.
4. Run:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q .
python -m pytest -q
```

5. Review `release_manifest.json`, `CHANGELOG.md`, and the license.
6. Tag the release as `v2.7.4-rev4.11.2`.
