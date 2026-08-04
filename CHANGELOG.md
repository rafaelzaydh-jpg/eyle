# Changelog

All notable changes to Eyle are documented here.

## Unreleased

### Documentation

- Simplified both landing-page READMEs and removed repeated explanations.
- Moved implementation details to focused documents under `docs/`.

### Repository

- Added a bilingual, GitHub-focused README.
- Added CI, contribution, security, issue, and pull-request templates.
- Added runtime-safe `.gitignore`, `.gitattributes`, and editor settings.
- Moved legacy planning and release notes out of the repository root.

## 2.7.0 — 2026-08-04

### Supervised agent default

- Enabled the full guarded agent workflow by default for projects inside `workspace/`.
- Kept explicit user confirmation mandatory for every real write.
- Enabled project test execution through the configured sandbox after confirmed edits.
- Kept external project paths in read-only mode until explicitly added to `trusted_project_paths`.
- Declared **LFM2.5-8B-A1B** (or a compatible quantized derivative) as the minimum recommended model for supervised agent use.
- Updated the GitHub landing page, configuration guide, benchmark guide, security notes, repository description, and discovery topics.

## 2.6.1 — 2026-08-03

### Agent safety and reliability

- Added fresh file and range evidence with canonical hashes.
- Isolated project indexes and internal traces from write detection.
- Added deterministic negative results for missing symbols.
- Added stale-patch reread and reconfirmation flow.
- Kept write confirmation, dry run, atomic application, tests, final reread,
  and rollback as explicit state-machine gates.
- Moved internal agent instructions, tool rules, state messages, and canonical
  structured output to English while preserving the user's original language.

### Validation

- 165 non-web automated tests passed in the release environment.
- The real local-LLM benchmark was not executed in that environment because it
  requires the configured model endpoint.
