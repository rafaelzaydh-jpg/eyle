# Publishing Eyle Rev5.6

Validate the extracted artifact, not only the development tree:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q eyle llm main.py
python -m pytest -q
node --check web/static/app.js
```

Generated runtime files must be absent: SQLite runtime databases, memory/session locks, `__pycache__`, `.pytest_cache` and other generated runtime caches.

Suggested tag:

```bash
git tag -a v2.7.4-rev5.6 -m "Eyle 2.7.4 Rev5.6"
git push origin v2.7.4-rev5.6
```

Rev5.6 never resumes or migrates previous Eyle persistent state. Do not add compatibility code during packaging.
