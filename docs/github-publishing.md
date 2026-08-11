# Publishing Eyle Rev5.7.1

Validate the extracted artifact, not only the development tree:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q eyle llm main.py
python -m pytest -q
node --check web/static/app.js
```

Generated runtime files must be absent: SQLite runtime databases, memory/session locks, `__pycache__`, `.pytest_cache` and other generated runtime caches.

Documentation checks before publication:

- `README.md` and `README.pt-BR.md` must describe current shipped behavior rather than act as release diaries;
- current architecture belongs in `docs/architecture.md`; future/non-shipped goals belong in `docs/architectural-direction.md`;
- README links must resolve inside the extracted artifact;
- version/schema/revision identity must agree across README, configuration and release identity output.

Suggested tag:

```bash
git tag -a v2.7.4-rev5.7.1 -m "Eyle 2.7.4 Rev5.7.1"
git push origin v2.7.4-rev5.7.1
```

Rev5.7.1 never resumes or migrates previous Eyle persistent state. Do not add compatibility code during packaging.
