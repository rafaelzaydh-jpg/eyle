# Publishing Eyle Rev5.8

Rev5.7.1 is the **first supported public release baseline** of Eyle; Rev5.8 is the current release. Earlier repository tags were development milestones and are not part of the supported public release line. Keep their commits in Git history; do not preserve or recreate those milestones as public release tags.

## Release identity

Current public tag:

```text
v2.7.4-rev5.8
```

Future public tags use one format only:

```text
v<app_version>-rev<schema_version>
```

Examples:

```text
v2.7.4-rev5.7.2
v2.7.4-rev5.8
```

Rules:

- never reuse, move or rewrite a public tag after publication;
- do not create alternate `rXX`, `revXX.YY`, experimental or compatibility tags on the public release line;
- development milestones belong in commits/branches and, when useful, in the pre-public or development section of the changelog;
- a tag becomes public only after the extracted artifact passes release verification;
- the README continues to present Eyle as the coding agent that is actually shipped. Future architectural direction belongs in `docs/architectural-direction.md`.

## One-time pre-public tag cleanup

If the repository still contains tags older than `v2.7.4-rev5.7.1`, remove only those tag references. This does **not** delete the commits they point to.

PowerShell:

```powershell
$publicBaseline = "v2.7.4-rev5.7.1"
$currentRelease = "v2.7.4-rev5.8"
$keep = @($publicBaseline, $currentRelease)

# Remove only pre-public/obsolete local tags.
git tag | Where-Object { $_ -notin $keep } | ForEach-Object {
    git tag -d $_
}

# Remove only pre-public/obsolete remote tags.
git ls-remote --tags origin |
    ForEach-Object { ($_ -split "\s+")[1] } |
    Where-Object { $_ -notmatch "\^\{\}$" } |
    ForEach-Object { $_ -replace "^refs/tags/", "" } |
    Where-Object { $_ -notin $keep } |
    Sort-Object -Unique |
    ForEach-Object { git push origin --delete $_ }

# Verify local and remote public tags.
git tag
git ls-remote --tags origin
```

After cleanup, the public tag set should start with `v2.7.4-rev5.7.1`.

## Benchmark publication rule

Public benchmark updates should report correctness/grounding/coverage together with physical cost. Do not present a lower token number as an improvement when it came from hiding objective matches, dropping continuation state or weakening material delivery. Conversely, a modest token increase is acceptable when it is explained by truthful Coverage/provenance/continuation that makes the agent more reliable.

For Rev5.8-style runs, publish SourceRecords and admitted Evidence separately. At minimum record Main turns, physical tools, Observations, SourceRecords, promoted Evidence, Claim-cited Evidence, provider prompt tokens, estimated physical tokens, replay count and relevant Coverage/Projection behavior.

## Artifact verification

Validate the extracted artifact, not only the development tree:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q eyle llm main.py
python -m pytest -q
node --check web/static/app.js
```

Generated runtime files must be absent: SQLite runtime databases, memory/session locks, `__pycache__`, `.pytest_cache` and other generated runtime caches.

Documentation checks before publication:

- `README.md` describes current shipped behavior rather than act as a release diary; public release documentation and canonical external contracts are English-only;
- current architecture belongs in `docs/architecture.md`; future/non-shipped goals belong in `docs/architectural-direction.md`;
- README links resolve inside the extracted artifact;
- version/schema/revision identity agrees across README, configuration and release identity output;
- `CHANGELOG.md` clearly separates supported public releases from pre-public engineering history.
- `docs/benchmark.md` includes at least one current live end-to-end run with outcome, tools/turns/token accounting, what property was actually established, and known efficiency/quality headroom; README may summarize the result but must link to the benchmark rather than duplicate the full table.

## Creating a public release tag

For the current release:

```bash
git tag -a v2.7.4-rev5.8 -m "Eyle 2.7.4 Rev5.8"
git push origin v2.7.4-rev5.8
```

For future releases, substitute the verified app/schema version and keep the same naming convention.

Rev5.8 never resumes or migrates previous Eyle persistent state. Do not add compatibility code during packaging.
