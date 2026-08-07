# Publishing Eyle Rev4.12.4.1

## Repository description

```text
Local-first coding agent with deterministic tools, supervised writes, rollback, token telemetry, and expandable execution history.
```

## Before publishing

1. Review `config.json` for local endpoints and secrets.
2. Keep only `.gitkeep` inside generated-state directories such as `memory/`, `context/`, and `workspace/`.
3. Remove caches, SQLite databases, traces, logs, backups, and generated memory.
4. Read `UPDATE_HISTORY.md` before restoring any architecture that was intentionally removed.
5. Run:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q .
python -m pytest -q
node --check web/static/app.js
```

6. Review `release_manifest.json`, `CHANGELOG.md`, `README.md`, `README.pt-BR.md`, `SECURITY.md`, `LICENSE.md`, and `CONTRIBUTING.md`.
7. Confirm the repository is described as **source-available, not open source**, unless the licensing decision has explicitly changed.
8. Confirm the README license summaries match `LICENSE.md`, and that contributor terms still grant maintainers the rights needed to use and relicense accepted contributions.
9. Do not replace the custom personal-use license with an OSI template merely because the repository is public. A licensing change must be intentional and documented in `UPDATE_HISTORY.md`.
10. Verify the expandable job history exposes only sanitized runtime facts.
11. Confirm Git/test tools are read-only/bounded and the decision history contains no raw reasoning.
12. Tag the release as `v2.7.4-rev4.12.4.1`.

## Suggested Git commands

```bash
git add -A
git commit -m "Rev4.12.4.1 - context budget hardening"
git tag -a v2.7.4-rev4.12.4.1 -m "Stable functional Rev4.12.4.1"
git push origin HEAD
git push origin v2.7.4-rev4.12.4.1
```
