# Publishing Eyle 2.7.5 Rev1.4.3

Canonical identity:

```text
app          2.7.5
schema       2.7.5-r1.4.3
revision     rev1.4.3-semantic-completion
tag          v2.7.5-rev1.4.3
```

Before publishing, validate the extracted artifact rather than only the working directory:

```bash
python -m pytest -q
python -m compileall -q eyle llm main.py
node --check web/static/app.js
python -m eyle.devtools.release_identity
```

Remove generated caches before packaging (`__pycache__`, `.pytest_cache`, `.coverage`, `*.pyc`). The release verifier rejects generated artifacts and removed-contract zombies.

Publish:

```bash
git add .
git commit -m "Eyle 2.7.5 Rev1.4.3 - Semantic Completion"
git push

git tag -a v2.7.5-rev1.4.3 -m "Eyle 2.7.5 Rev1.4.3 — Semantic Completion"
git push origin v2.7.5-rev1.4.3
```
