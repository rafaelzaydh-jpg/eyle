# Publishing Eyle Rev5.2.9

## Repository description

```text
Local-first coding agent with deterministic tools, directed evidence investigation, adaptive structured LLM contracts and supervised transactional writes.
```

## Before publishing

1. Review `config.json`; never commit API keys or secrets.
2. Keep only `.gitkeep` inside `context/`, `memory/`, and `workspace/`.
3. Remove SQLite databases, capability caches, logs, traces, Python caches and test caches.
4. Run:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q .
python -m pytest -q
node --check web/static/app.js
```

5. Verify `release_manifest.json`, both READMEs, `CHANGELOG.md`, `SECURITY.md`, `LICENSE.md`, and `CONTRIBUTING.md`.
6. Confirm public history exposes sanitized runtime facts only, never raw chain-of-thought/prompts/source bodies/secrets.
7. Keep the project description **source-available, not open source**, unless licensing intentionally changes.
8. Tag the release as `v2.7.4-rev5.2.9`.

## Suggested Git commands

```bash
git add -A
git commit -m "Rev5.2.9 - Progress-Earned Authority"
git tag -a v2.7.4-rev5.2.9 -m "Eyle 2.7.4 Rev5.2.9"
git push origin HEAD
git push origin v2.7.4-rev5.2.9
```
