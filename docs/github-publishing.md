# Publishing Eyle 2.7.5 Rev1.3.4

Target release identity:

```text
app version  2.7.5
schema       2.7.5-r1.3.4
revision     rev1.3.4-fresh-claim-token-cleanup
tag          v2.7.5-rev1.3.4
```

## Public tag rules

- never reuse, move or rewrite a published tag;
- never mass-delete tags through a broad inverse filter;
- obsolete pre-public tags, when necessary, must be explicit reviewed exact names;
- development milestones belong in commits/branches and `CHANGELOG.md`;
- create the public tag only after verifying the extracted artifact.

## Artifact verification

Validate the extracted artifact, not only the development tree:

```bash
python -B -m eyle.devtools.release_identity
python -B -m compileall -q eyle llm web main.py
python -B -m pytest -q
node --check web/static/app.js
```

Generated/runtime state must be absent:

- `.git/`, `.pytest_cache/`, `__pycache__/`, `*.pyc`, `.coverage`;
- Runtime SQLite databases and locks;
- transient pending/session files produced by local execution.

## Documentation verification

Before publication, README/config/manifest identities must agree; current docs must describe Rev1.3 semantics; removed contracts/gates must not be presented as active; historical behavior belongs in `CHANGELOG.md`.

## Create the tag

After extracted-artifact verification:

```bash
git tag -a v2.7.5-rev1.3.4 -m "Eyle 2.7.5 Rev1.3.4 — Fresh Claim & Token Cleanup"
git push origin v2.7.5-rev1.3.4
```

Rev1.3 never resumes or migrates incompatible older Core state.
