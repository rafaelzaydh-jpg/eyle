# Publishing on GitHub

## Repository metadata

**Repository name**

```text
eyle
```

**Description**

```text
Local-first autonomous code agent with supervised writes with external memory, BM25 retrieval, grounded answers, guarded patches, tests, rollback, telemetry and cycle protection.
```

**Topics**

```text
local-llm
coding-agent
ai-agent
code-assistant
developer-tools
offline-ai
privacy
rag
retrieval
bm25
llama-cpp
ollama
lm-studio
python
agentic-ai
lfm2-5
liquid-ai
tool-use
function-calling
```

## Before publishing

1. Review `config.json` for local paths, model names, and secrets.
2. Confirm that `memory/`, `context/`, and `workspace/` contain only `.gitkeep`.
3. Confirm there are no `__pycache__`, `.pytest_cache`, `.pyc`, SQLite, trace, log, or JSONL runtime files.
4. Run release identity, compilation, and tests.
5. Review the license. The repository currently defaults to all rights reserved.
6. Upload `assets/eyle-social-preview.png` under **Settings → General → Social preview**.
7. Add the description and topics above.
8. Create a GitHub release for `v2.7.3` and attach the clean ZIP if desired.

## Updating the existing repository to 2.7.3

Copy the contents of this package over the local clone, then run:

```bash
git checkout main
git pull --ff-only origin main
git add -A
git commit -m "Release Eyle 2.7.3 revision 53"
git tag -a v2.7.3 -m "Eyle 2.7.3 revision 53"
git push origin main
git push origin v2.7.3
```

Before committing:

```bash
python engine/release_identity.py
python -m compileall -q .
python -m pytest -q
git status --short
```

## First publication only

```bash
git init
git add .
git commit -m "Initial public release"
git branch -M main
git remote add origin https://github.com/rafaelzaydh-jpg/eyle.git
git push -u origin main
```
