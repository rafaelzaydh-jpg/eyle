# Publishing Eyle 2.7.5 Rev1.4.1

Canonical identity:

```text
app          2.7.5
schema       2.7.5-r1.4.1
revision     rev1.4.1-semantic-freedom
tag          v2.7.5-rev1.4.1
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
git commit -m "Eyle 2.7.5 Rev1.4.1 - Semantic Freedom"
git push

git tag -a v2.7.5-rev1.4.1 -m "Eyle 2.7.5 Rev1.4.1 — Semantic Freedom"
git push origin v2.7.5-rev1.4.1
```
