# Verification — Rev3.7.2

Release verification is a current-contract check, not a museum of old runtime identities.

## Required commands

```bash
python -B -m eyle.devtools.release_identity
python -m compileall -q eyle llm server web tests main.py
python -m pytest -q
python -m pytest -q server/tests
node --check web/static/app.js
```

For a publication artifact, remove generated caches/runtime state first, create the archive, extract it to a fresh directory and repeat verifier/tests against the extracted copy.

## Canonical-cut gates

The verifier and tests must prove:

- exact Rev3.7.2 config identity; older config is rejected rather than promoted;
- current Session/pending/execution schemas only;
- Memory runtime accepts v12 only;
- v11→v12 exists only as an explicit devtool;
- no `standard.py` facade or `standard_impl` package;
- Host consumes `eyle.providers.standard` directly;
- no dynamic `globals().setdefault` export path;
- no generated-token fuse/cognitive deadline;
- no fixed conversation snapshot count;
- no automatic Temporary/global Memory projection;
- explicit Memory activation remains available;
- no removed search/file cognitive ceilings;
- public symbol paging uses `page_size`;
- Adapter local request rejects the removed `max_tokens` alias and accepts `max_completion_tokens`;
- UI uses `interaction` without a `confirmation` compatibility alias;
- ECC/Memory sidecar isolation remains intact;
- repeated protocol error fingerprint remains bounded;
- provider-token/context physical limits remain intact;
- capability/Memory reachability is not reduced by smaller materialization.

## Behavioral gates

Current regression tests must include:

1. trivial request uses one normal cognition when no continuation is required;
2. current conversation continuity resolves recent references;
3. topic switching does not let unrelated Task/Memory state invade the prompt automatically;
4. invalid Memory sidecar does not invalidate Conclude/Explore/Build;
5. repeated identical protocol failure terminates the repair episode mechanically;
6. long conversation is token-budget materialized and reports omitted history;
7. large Memory Graph does not proportionally enlarge a trivial prompt;
8. nonmaterialized Memory remains reachable through recall/activation;
9. v11 graph is rejected by runtime before explicit migration and accepted afterward;
10. Service message snapshot/job ordering remains atomic under concurrency.

## Environment-sensitive tests

Sandbox/process isolation tests must run in an environment that does not inject unrelated Python `sitecustomize` imports or exhaust the sandbox process limit before the tested command starts. A host-environment failure is reported separately; the Runtime must not be weakened to satisfy an invalid test environment.

## Documentation rule

Historical contracts live in `CHANGELOG.md` and explicit migration tools. Current architecture/configuration/model docs describe only active paths.
