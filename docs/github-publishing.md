# Publishing Eyle Rev5.1

## Repository description

```text
Local-first coding agent with deterministic tools, adaptive structured LLM contracts, evidence-grounded answers and supervised transactional writes.
```

## Before publishing

1. Review `config.json` for local/private endpoints. Never commit API keys or secrets.
2. Keep only `.gitkeep` inside generated-state directories (`context/`, `memory/`, `workspace/`).
3. Remove SQLite databases, capability caches, logs, traces, backups, Python caches and test caches.
4. Run:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q .
python -m pytest -q
node --check web/static/app.js
```

5. Review `release_manifest.json`, both READMEs, `CHANGELOG.md`, `SECURITY.md`, `LICENSE.md`, and `CONTRIBUTING.md`.
6. Confirm public execution history contains sanitized runtime facts only, never raw reasoning/prompts/source bodies/secrets.
7. Confirm the repository is described as **source-available, not open source**, unless licensing is intentionally changed.
8. Tag the release as `v2.7.4-rev5.1`.

## Suggested Git commands

```bash
git add -A
git commit -m "Rev5.1 - context boundaries and investigation continuity"
git tag -a v2.7.4-rev5.1 -m "Eyle 2.7.4 Rev5.1"
git push origin HEAD
git push origin v2.7.4-rev5.1
```
