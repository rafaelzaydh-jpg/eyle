## 2.7.5 Rev1.3 — Task Memory — 2026-08-12

- Added a separate Main-owned recursive `Task` contract: exactly `id`, `parent_id`, `description`, `status=open|completed|dropped`, and `result`.
- Added `AgentSession.tasks` as canonical persisted intentional state. Omitted tasks remain unchanged across turns; Runtime validates structure but never infers semantic completion.
- Added parent existence/self-reference/cycle validation while allowing parent and child creation in the same Main update batch. Tree structure never determines execution order.
- Closed tasks require a concise result, preserving Main's semantic account of what was accomplished or why work was dropped.
- Main's structured envelope is now exactly `{action,investigation_updates,task_updates}`. Investigation remains epistemic; Tasks are intentional; Observation remains physical.
- Open Tasks are deliberately **not** a Final/write gate. Runtime does not auto-close tasks from child status, tool success, observations or `exit 0`.
- Accepted/rejected task mutations are recorded in DecisionLedger only for observability/history; DecisionLedger does not own Task semantics.
- Renamed the old physical `task_id` carried by AgentSession/ExecutionContext/service boundaries to `execution_id`, reserving “Task” for Main's semantic state.
- No Planner, scheduler, focus queue, priorities, generic cognitive ledger, associative memory, loop detector or automatic convergence rule was added.
- The Rev1.2.3.2.2 Microsandbox Windows guest-filesystem staging and all 16 public capability contracts remain intact.
- Clean-break identity advances to `2.7.5-r1.3`; older config/Session/queue/project-memory state is rejected rather than migrated.
- Offline deterministic suite during release preparation: **295 passed, 1 Flask-dependent skip**.

## 2.7.5 Rev1.2.3.2.2 — Windows Guest Filesystem Staging — 2026-08-12

- Native Windows Microsandbox execution no longer bind-mounts the Eyle snapshot through virtio-fs. The disposable snapshot is staged into the microVM private rootfs via `Sandbox.fs.copy_from_host`, avoiding the Windows EACCES/ELOOP passthrough defects observed in live execution.
- Linux/macOS keep the writable disposable bind mount for performance; the platform branch remains a Runtime-only physical choice. Main, Claim, Observation and the public capability surface remain unchanged.
- Microsandbox results now expose `workspace_transport=guest_fs_copy|bind_mount` so the physical transport is observable without leaking provider details into semantic control.
- The live regression covered the exact failure mode: a corrected Python program had already exited 0, but Windows bind-mount reads emitted `Permission denied` / `Too many levels of symbolic links`, provoking unnecessary semantic investigation. The fix removes the faulty physical signal rather than adding a semantic stop gate.
- Clean-break identity advances to `2.7.5-r1.2.3.2.2`; no aliases or migration bridges were added.

## 2.7.5 Rev1.2.3.2.1 — Microsandbox API Closure — 2026-08-12

- Fixed the live Windows failure caused by calling the non-existent Microsandbox 0.6.8 `Network.public_only()` helper. Normal `run_command` now uses the pinned SDK's canonical `Network.from_profiles("public")`; explicitly isolated supervised execution continues to use `Network.none()`.
- Added first-use Runtime bootstrap: when the Python SDK is present but the local `msb`/`libkrunfw` payload is not prepared, the existing Microsandbox event loop executes the official `await install()` flow and verifies `is_installed()` before VM creation.
- Audited the complete active SDK surface against the 0.6.8 contract: `Sandbox.create/remove`, `Volume.bind`, `shell_stream`, stream event fields, `ExecHandle.wait/kill`, and CPU/AS/NPROC/NOFILE/FSIZE rlimits.
- Added explicit SDK-shape validation so an incompatible package fails at the physical boundary with the missing method names instead of surfacing an arbitrary AttributeError halfway through sandbox creation.
- First OCI image pull gets a 600-second physical startup allowance; command timeout remains independently bounded per execution.
- No Main, Claim, Observation, capability or public-tool semantics changed. Public tools remain 16.
- Clean break identity advances to `2.7.5-r1.2.3.2.1`; no compatibility aliases or migrations were added.

## 2.7.5 Rev1.2.3.2 — Microsandbox Runtime — 2026-08-12

- Added Microsandbox 0.6.8 as the preferred strong physical backend for `run_command`; `auto` resolves Microsandbox → Docker → Bubblewrap.
- One embedded Microsandbox microVM persists per physical job, preserving sandbox-local package/build state while the real workspace remains outside the writable guest mount.
- The only writable host bind is Eyle's disposable workspace snapshot at `/workspace`; supervised tests use Microsandbox only when explicitly configured with a test-capable OCI image, then use a separate one-off VM so network/snapshot policy does not leak across execution modes.
- Runtime applies VM vCPU/memory limits plus command timeout and CPU/address-space/process/open-file/file-size limits; unrestricted command networking uses `public_only`, while supervised blocked-network execution uses `Network.none()`.
- Clean break: `sandbox.imagem_docker` is removed and replaced by provider-neutral `sandbox.imagem_oci`; current config/Session/queue/project-memory identity advances to `2.7.5-r1.2.3.2`.
- Docker and Bubblewrap remain explicit/automatic physical fallbacks; Main, Claim, Investigation, Observation and the 16 public capability contracts are unchanged.

## 2.7.5 Rev1.2.3.1 — Claim Contract Closure — 2026-08-12

- Claim remains intelligent but its **interface** is now physically bounded: at most 3 independent blockers, 4 grounding coordinates per blocker and one concise reason.
- Removed `agent.claims.verifier.max_tokens`; Claim output reservation is derived from the maximum canonical schema size plus physical margin, eliminating the inconsistent 520-token magic ceiling.
- A truncated or structurally invalid Claim receives exactly one canonical protocol recovery. A second failure remains fail-closed; truncation is never converted into acceptance.
- Main interaction contract now states that a user message need not be a formal task. Ordinary/social conversation may return `final`; `needs_user` is only for genuinely blocking information or choices that must come from the user.
- LLM transport telemetry records request start after physical preflight and preserves failure status (`read_timeout`, `model_output_truncated`, etc.) instead of inferring `preflight_blocked` from missing response metadata.
- Fixed stream-progress eligibility and callback wiring to use the real `ExecutionContext` rather than the config dict; non-structured job streaming can now activate when configured.
- Clean break: config/Session/queue/project-memory schema advances to `2.7.5-r1.2.3.1`; older state is rejected rather than migrated.

## 2.7.5 Rev1.2.3 — Operational Self-Observation — 2026-08-12

- Added bounded `operational_feedback` derived from canonical DecisionLedger, Observation, Claim and ExecutionContext facts; no new semantic agent or recovery ledger.
- Main can see recent challenges/rejections, selected Final grounding IDs, available Material IDs, replay-only preflights, executed observations, open Frontiers, workspace epoch and physical token headroom.
- Provisional Final decision events now retain only generic observable grounding/workspace facts needed for later self-audit.
- Main prompt clarifies that observed physical claims should explicitly select supporting `mat-*`, while pure reasoning/conversation needs no artificial grounding.
- Replay remains memoization rather than a fatal loop detector; Runtime reports the observable consequences and Main decides retry/change/finish.
- Task-wide physical `max_total_tokens` default and maximum changed from the experimental 1,000,000 to **90,000**. No turn/call/tool quotas were introduced.
- Clean break: config/Session/queue/project-memory schema advances to `2.7.5-r1.2.3`; older persisted Core state is rejected rather than migrated.
- Added offline regression reproducing Claim challenge → repeated cached observation → Main sees zero new physical observation and can finish with already available Material.

## 2.7.5 Rev1.2.2 — Physical Contract Closure — 2026-08-11

- Frontier pagination no longer deep-copies the retained snapshot payload on every page; only the requested slice is detached.
- Coverage is now a mechanically enforced universal contract (`scope`, `examined`, `complete`, `boundaries`, optional `facts`); malformed capability Coverage fails closed.
- `find_symbol` exhausts the safe source scope, reports objective scan Coverage and exposes matches beyond the first 32 through Frontier.
- Continuation Coverage distinguishes snapshot exhaustion from source-materialization completeness, including vanished/unreadable source pages.
- Capability-specific public/model projections, normalization, covering lookup and resource-failure lookup moved behind registry hooks; generic dispatch has no tool-name branches.
- Redundant `category + effects + effect` metadata collapsed to one physical `effect = observe|execute|mutate` field.
- Claim Runtime fact compaction is domain-neutral and no longer knows code/file/symbol-specific result vocabulary.
- Sandbox tests now cover process-tree cleanup, backend fallback, Docker initialization failure and persistent-container cleanup; Transaction hardening from Rev1.2.1 remains intact.
- Clean break: config/Session/queue/project-memory schema advances to `2.7.5-r1.2.2`; older persisted Core state is rejected rather than migrated.
- Deterministic suite: 259 passed, 1 skipped in the implementation environment before release packaging.

# Changelog

## 2.7.5 Rev1.2.1 — Physical Observation Maturity — 2026-08-11

- Matured the physical observation plane around **Material + Coverage + Frontier** without adding a new semantic authority or compatibility layer.
- Replaced file-era Material `source_hash` with opaque `source_version`; Observation now treats locator/version semantics as capability-owned physical provenance.
- Standardized capability Coverage into one physical contract: `scope`, `examined`, `complete`, optional `boundaries` and capability-owned physical `facts`. Coverage is completeness of declared physical scope, never semantic sufficiency.
- Made every public capability explicitly own the same registry hook surface: execution, memoization signature policy, Material observation, Coverage and Frontier projection.
- Reworked Frontier storage so large continuation payloads live once in Runtime-private immutable snapshots; handles/frontiers are lightweight cursors referencing the snapshot, with garbage collection after the final cursor is consumed.
- Made continuation materialization source-capability-owned: e.g. a `search_code` Frontier now materializes real file-range Material instead of exposing generic range-address blobs.
- Removed remaining file/filesystem semantics from Observation and removed the final public-capability name from Agent. Adding a capability no longer requires catalog branches in either module.
- Hardened Sandbox snapshots by omitting all repository symlinks from host-executed copies; added regressions for cwd escape, timeout termination and bounded output tails.
- Hardened multi-file transactions so apply failures report whether rollback was actually confirmed; rollback failure is surfaced as `PATCH_TRANSACTION_ROLLBACK_FAILED` instead of being hidden. Added stale-state, create/delete and multi-file rollback regressions.
- Clean break: config/Session/queue/project-memory schema advances to `2.7.5-r1.2.1`; Rev1.2 persisted state is rejected rather than migrated.
- Deterministic suite at release preparation: **248 passed, 1 Flask-dependent skip** in the available offline environment.

## 2.7.5 Rev1.2 — Capability Clean Break — 2026-08-11

- Applied the ObjectiveScope ownership pattern to the capability layer: capability-specific observation identity, Material extraction, Coverage and compact model projection now live with the capability registry rather than Agent/Observation.
- Deleted `Observation.material_candidates_from_tool`, tool-specific Observation signatures/resource-failure lookup and Agent capability-specific presentation/model branches.
- Generalized `mat-*` Material from mandatory `file/file_hash` identity to `locator + content_hash`; file provenance is one physical locator kind rather than a Core assumption.
- Removed Claim access to Investigation, automatic Investigation grounding injection, Investigation target coordinates/telemetry and Claim-owned filesystem freshness checks. Physical freshness is resolved before semantic review.
- Reduced DecisionLedger to factual observability by deleting `required_properties`, rejection fingerprints and repeated-rejection counters.
- Preserved useful bounded `find_symbol`, `symbol_relations` and project-inspection projections by moving them into capability-owned functions instead of deleting behavior.
- Clean break: strict config/Session/queue/project-memory identity advances to `2.7.5-r1.2`; Rev1.1 state is rejected rather than migrated.

## 2.7.5 Rev1.1 — Semantic Freedom Reset — 2026-08-11

- Established the governing rule: **Eyle constrains effects, not thought.** Main is the sole task-semantic authority; Runtime enforces only physically decidable contracts/effects; Claim is a critic rather than a second planner.
- Replaced the Main system prompt with a domain-neutral capability contract and removed audit/search recipes and Runtime `RESOURCE_PRESSURE` strategy advice.
- Collapsed Claim to `{verdict: accept|challenge, issues:[...]}` with a small fixed per-call output ceiling. Removed material-satisfaction/answer-consistency/semantic-gap planning machinery from the current Claim contract.
- Made `Investigation` an optional Main-owned notebook instead of a completion/permission state machine. Goals may be revised; established/dismissed are Main semantic choices; open targets do not block Final, patches or writes. Supplied `mat-*` IDs remain physically validated.
- Rejected Investigation updates no longer cancel an independent valid action from the same model turn.
- Changed repeated objective observation into memoization: cache hits reuse canonical Observation, increment replay telemetry without appending duplicate Observation events, and no longer trigger `OBSERVATION_REPLAY_LOOP`.
- Made multi-tool validation independent: one malformed sibling does not cancel valid siblings. Recoverable physical capability failures are returned to Main instead of semantically failing the whole task.
- Removed specialized repeated-invalid/patch-dry-run behavioral punishment and fixed LLM-turn/LLM-call/tool-call quotas. The total-token fuse and deadline contain runaway execution without dictating how many reasoning cycles a valid task may use.
- Removed cumulative `max_prompt_tokens` and `max_completion_tokens` contracts. The current deployment has one hard llama-server context ceiling of **38,000 tokens per call**, plus a distant `max_total_tokens=1,000,000` runaway fuse and task deadline.
- Removed the hidden secondary 32,768-token cap in token budgeting so configuration and request compilation share the same 38,000-token physical window.
- Advanced strict config/Session/queue/project-memory identity to `2.7.5-r1.1`. Older persisted Core state is rejected; no migration or aliases are provided.
- Current deterministic validation: **227 passed, 1 Flask-dependent skip** in the offline build environment.

## 2.7.5 Rev1 — Grounded Context Hardening — 2026-08-11

- Removed the zero-grounding Claim bypass: when Claim is enabled, every provisional Final is audited. Missing Observation can now become semantic debt instead of an automatic acceptance path.
- Strengthened Claim guidance so current-workspace/current-runtime/external assertions requested for inspection or verification are insufficient when the packet contains no objective support. Pure explanation/writing can still pass without Observation.
- Fixed a continuation privacy/ergonomics bug where a Runtime `handle:*` could leak through Frontier `at`; continued pages now receive fresh public `fr-*` coordinates only.
- Preserved old still-open Frontiers through prompt recency compaction using one compact Frontier bundle, while keeping opaque handles Runtime-private.
- Added progressive fresh-result projection: raw tool bodies are one-turn working material and tighten as turns/token pressure rise; canonical Observation remains complete.
- Reduced Main prompt repetition by bounding conversation background, grounding index and recent Observation navigation more aggressively.
- Added physical resource-pressure feedback so Main prefers retained material/narrow observations instead of opening broad new scans near budget exhaustion.
- Changed long-string/source compaction to preserve both head and tail, protecting terminal diagnostics that Rev0 could crop away.
- Reconciled local prompt reservations with provider-reported physical token usage and allowed conservative downward calibration with a 0.75 safety floor, preventing phantom `MAX_PROMPT_TOKENS_EXCEEDED` from overly pessimistic local estimates.
- Added regressions for zero-grounding Claim review, provider-token reconciliation, long-job reservation accounting, Frontier retention/privacy and progressive context compaction.
- Advanced strict config/Session/queue/project-memory identity to `2.7.5-r1`; Rev0 persisted Core state is not migrated.

## 2.7.5 Rev0 — Clear Full Drive — 2026-08-11

- Reset the revision counter for Eyle 2.7.5.
- Collapsed physical grounding into Observation `mat-*` material.
- Deleted `SourceRecordLedger`, `EvidenceLedger`, `source_record.py`, `evidence.py` and promotion protocol.
- Kept Coverage and Frontier as physical Core concepts; opaque handles are Runtime-private.
- Replaced `expand_observation(handle=...)` with `continue_observation(frontier=fr-*)`.
- Removed Projection as a Core contract while preserving bounded materialization and truthful Coverage/Frontier state.
- Simplified Investigation to direct `mat-*` grounding.
- Claim now reports semantic debt without mutating Investigation.
- Removed `agent_info` and Main-facing `execution_trace` from the public tool registry (18 → 16 tools).
- Fixed completion-clamp propagation so task-budget truncation is not mislabeled as provider truncation.
- Removed Rev5.x-specific tests that existed only to preserve deleted protocol; retained useful scope, reachability, safety, Frontier, retry and transaction regressions under neutral tests.
- Rewrote current architecture/release documentation around Rev0 and removed the Rev5.9.1 follow-up document.

## Rev5.9.1 — Scope & Investigation Contract Hardening — 2026-08-11

- Fixed the Rev5.9 Objective Scope regression: `search_code(include_paths=["eyle/core"])` now resolves a literal directory recursively instead of applying raw `fnmatch` and scanning zero files. Literal files are exact, literal directories are recursive subtrees, and only wildcard-bearing selectors are explicit globs.
- Added explicit Scope Resolution before Coverage. `search_scope` records capability-universe size, resolved file count, selector resolution, readable files scanned and protected files. Missing/unsafe literal include paths and explicit includes outside the capability boundary fail closed rather than becoming misleading empty complete searches.
- Hardened Investigation as a discriminated structured contract. Every explicit `established` update must carry at least one canonical `src-*`/`ev-*` grounding ID and a non-empty reason; `dismissed` also requires a reason. Provider JSON Schema, local structured parser and Runtime transition validation now share this invariant.
- Canonicalized Investigation rejection identity so free-form `reason` wording does not make the same rejected transition appear new when objective state is unchanged. Materially different grounding attempts remain distinguishable.
- Split output truncation diagnostics: a provider `length` stop caused by Runtime task-budget clamping is now `MAX_COMPLETION_BUDGET_EXHAUSTED`; `MODEL_OUTPUT_TRUNCATED` is reserved for an unclamped backend/output ceiling. Physical token limits were not increased.
- Preserved Rev5.9's single Agent Decision Envelope, bounded Agent/Claim protocol retry, Claim Material Satisfaction audit, Objective Projection, SourceRecord/Evidence separation and protected-resource identity boundary.
- Clean break: config, Session, queue and project-memory schemas advance to 5.9.1. Rev5.9 state is rejected rather than migrated or dual-read.
- Added deterministic regressions reproducing the failed Rev5.9 scoped-search path and the latent `established`-without-Evidence schema/runtime split.

## Rev5.9 — Decision Integrity & Epistemic Completion — 2026-08-11

- Replaced the Rev5.8 Agent shape with one clean-break discriminated Decision Envelope: `{action, investigation_updates}` with exactly one `action.kind` (`tool_calls`, `patches`, `needs_user`, or `final`). Provider JSON Schema and local parser now describe the same legal state space, eliminating the latent `AGENT_PAYLOAD_AMBIGUOUS` contract split.
- Added one bounded fresh structured-protocol retry for Main and one for Claim. Rejected structured payloads execute zero actions, are never repaired or interpreted by Runtime, and a second violation fails closed.
- Claim Runtime Facts now preserve bounded Coverage, Projection, Frontiers and Handles so semantic review can see objective continuation/limitations even when large tool payloads are truncated.
- Strengthened Material Satisfaction guidance: facts present in Evidence/Runtime coordinates are not considered delivered unless the provisional answer actually communicates the material distinction. Request anchors remain literal coordinates, not Runtime-parsed requirements or quantity checklists.
- `search_code` accepts Main-declared literal `include_paths` / `exclude_paths`, records the scope in observation identity and applies it mechanically without semantic relevance inference.
- `list_tree` no longer erases every dot-directory. Nonignored hidden directories remain structurally visible; protected resources inside them may expose existence while content remains restricted.
- Clean-break config, Session, queue and project-memory schemas advance to 5.9. Rev5.8 persistent state and the old nullable action envelope are rejected rather than migrated or dual-read.
- Added Rev5.9 regressions for Decision Envelope integrity, bounded Agent/Claim protocol retries, scoped search identity, epistemic Claim projection and hidden-directory structural visibility.

## Rev5.8 — Objective Projection & Evidence Admission — 2026-08-11

- Live provider benchmark rerun of the message-contract audit completed in 4 Main turns / 8 tools with 52 SourceRecords, only 2 admitted Evidence, 0 structurally unreferenced Evidence, 27,503 provider prompt tokens and 33,747 estimated physical tokens. The +3.9% physical-token delta versus Rev5.7.7 is documented as an acceptable trade when it preserves truthful objective Coverage, provenance and continuation handles.
- Public benchmark policy now explicitly prioritizes epistemic integrity over raw token minimization: optimization may remove duplication/repeated inference, but must not hide objective state or remove Main's ability to expand an incomplete projection.
- Clean-break schemas advance to 5.8 for config, Session, queue and project memory. No Rev5.7.7 persistent state is migrated or dual-read.
- Added `SourceRecordLedger`: successful capability materializations become objective `src-*` records instead of automatic Evidence. Main explicitly selects material SourceRecords in Investigation/Final; Runtime only performs deterministic identity-preserving promotion into `ev-src-*`.
- `search_code` now exhausts the readable literal-match universe before model-facing limits, groups all ranges, diversifies deterministically across files before truncation, separates `coverage_complete` from `projection_complete`, and exposes omitted objective ranges through handles. No semantic relevance ranking was added.
- Claim contract text now matches the strict JSON Schema, supports literal `request:rN` anchors, scales physical output budget with request/answer/Investigation packet size, and rejects structurally contradictory review states such as `satisfied` with semantic gaps or `blocked` without Runtime grounding.
- Main contract explicitly preserves the authority boundary: capabilities may compute objective properties over large state; only Main decides relevance, Evidence admission, frontier materiality and semantic sufficiency. Final synthesis must not invent facts to satisfy requested quantity or erase material distinctions established by reality.
- Prompt accounting now distinguishes SourceRecord materialization from Evidence admission instead of treating source-range fan-out as semantic Evidence amplification.


## Rev5.7.7 — Protected Resource Identity Integrity — 2026-08-11

- Public documentation now leads with concrete coding-agent use cases and records the live message-contract benchmark showing the difference between a protection-induced investigation loop and the corrected Rev5.7.7 run. Benchmark documentation also records remaining Evidence-admission/material-satisfaction headroom instead of presenting only favorable metrics.
- Protected resources are identified by explicit path semantics plus physical resource identity; symlink and hard-link aliases cannot bypass read/search/diff/sandbox boundaries.
- `.env` templates such as `.env.example`, `.env.sample`, `.env.template` and `.env.dist` are readable contract examples. Public PEM/certificate/public-key resources remain readable and discoverable.
- `search_code` distinguishes complete readable-scope coverage from complete whole-workspace coverage. Protected exclusions become explicit non-expandable frontiers and negative Evidence preserves the same scope.
- Stable protected-resource denials are persisted as resource-scoped physical failures and can be rehydrated across different read ranges without disabling the capability.
- Canonical patch dry-runs never read an existing protected resource or physical alias. Sandbox and Git inspection use the same protected-resource identity policy.
- The removed content-based secret scanner remains prohibited; normal source content never changes resource visibility.

Clean break: current config/session/queue/project-memory schemas are exact 5.7.7. Pending continuation and benchmark schemas remain independently exact at version 1.

**Public release baseline:** `v2.7.4-rev5.7.1` is the first supported public Eyle release. Entries below Rev5.7.1 are preserved as **pre-public engineering history**; they document the path to the current architecture but are not supported public releases and should not be recreated as public Git tags. Git commit history remains the canonical record of those development milestones.

## Rev5.7.6 — Protected Resource Read Boundary — 2026-08-11

Focused workspace-observation/security correction on top of Rev5.7.5. Content-based secret classification has been removed completely: ordinary source files are readable and remain structurally present regardless of names or literals such as `token`, `password`, `api_key` or private-key-looking text. Content access is restricted only for path-identified credential/private-key resources such as `.env`, known credential stores, explicit private-key filenames and private-key container suffixes. Generic `.pem`, public-key and certificate files remain readable.

Protected resources remain structurally observable in inventory/status surfaces instead of disappearing from the workspace. `read_file`, content search, symbol parsing and direct Git diff content respect the protected-resource boundary; `git_status` may still expose path/status. Sandbox snapshots omit only protected-resource paths and report the omission count. `symbol_relations` no longer drops normal source because of content heuristics and reports a protected-resource frontier if a source-like protected path is excluded. The `max_secret_scan_bytes` configuration and `SECRET_CONTENT_BLOCKED` contract are removed.

Clean break: current config/session/queue/project-memory schemas are exact 5.7.6. Pending continuation and benchmark schemas remain independently exact at version 1.

Current validation: 203 passed, 1 skipped because Flask is unavailable in the build environment.


## Rev5.7.5 — Canonical Boundary Hardening — 2026-08-11

Focused P1/P2 compatibility cleanup on top of Rev5.7.4. `search_code` now gives ripgrep and the Python fallback one deterministic file universe and one canonical ranking/truncation stage, so environment choice changes execution backend rather than observable search semantics. Conversation history is normalized at the Runtime boundary into the sole Core shape `{role, content}`; the Core no longer accepts the `text` alias. `agent_info` projection accepts only `registered_tools`. Pending continuation now has its own exact `pending_schema_version=1`, English field names, exact kind-specific shapes and exact persisted security metadata; older mixed-language/unversioned shapes are rejected rather than adapted. Pre-Python-3.8 feature-detection branches for `shlex.join` and `multiprocessing.Process.kill` were removed because Python 3.8+ is the declared runtime floor.

Compatibility that belongs behind adapters/capabilities remains intact: OpenAI-compatible/Ollama, Docker/Bubblewrap, OS-specific execution and provider normalization. No provider-protocol fallback was added to the Core.

Clean break: current config/session/queue/project-memory schemas are exact 5.7.5; pending continuation schema is independently exact at version 1; benchmark schema remains independently exact at version 1.

Current validation: 195 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.7.4 — Core Compatibility Boundary — 2026-08-10

Focused compatibility cleanup. Core contracts now accept one exact canonical representation instead of same-version shape tolerance or language aliases. `AgentSession` requires the complete 5.7.4 persisted envelope and exact ledger envelopes; project memory requires one exact envelope, entry shape and file-reference shape. Sandbox backends use one English vocabulary (`auto`, `docker`, `bwrap`, `process`, `trusted_local`) and unknown/alias values fail during configuration validation.

Benchmark artifacts now use an independent exact `benchmark_schema_version=1`. The benchmark producer and both comparators share one validator and accept only canonical English fields; historical `papel/casos/results/case_id` forms and top-level token-counter fallbacks are rejected. Coverage comparison requires explicit `read_ok`, `factual_ok`, `write_ok` and `unauthorized_write` gates, so missing fields can no longer pass by default.

The compatibility doctrine is now explicit: **compatibility inside the Core is suspicious; compatibility behind adapters/capabilities is desirable.** OpenAI-compatible/Ollama and environment portability remain. Future alternate LLM structured-output mechanisms may be supported behind provider adapters, but must normalize into the same strict Agent/Claim objects; no Core-level downgrade chain is restored.

Clean break: current config/session/queue/project-memory schemas are exact 5.7.4. Benchmark schema is independently exact at version 1.

## Rev5.7.3 — Directed Reachability Hardening — 2026-08-10

Focused correction of the negative/long-path reachability benchmark. Directed reachability now exhausts the finite resolved graph mechanically, so Main no longer escalates `max_depth`/`max_edges`. Query identity canonicalizes those obsolete tuning hints, preventing 5→12→32 variants from becoming separate physical observations. Expandable dynamic frontiers are restricted to target-directed corridor evidence; generic root-reachable dynamic dispatch is represented as one non-expandable limitation rather than paginated unrelated calls. `expand_observation` accepts only exact `handle:*` IDs, and missing/stale handle references no longer retire the capability. Main guidance now treats incomplete coverage as support for an explicitly inconclusive result, never as proof of absence.

Clean break: current config/session/queue/project-memory schemas are exact 5.7.3. No compatibility bridge is introduced.

## Rev5.7.1 — Directed Observation & Context Projection — 2026-08-10

- Hardens `symbol_relations(query="reachability")` with file-local/import/alias resolution before project-global name fallback.
- Makes unresolved frontiers query-shaped: only unresolved sites on the root-side coverage of the requested reachability property can keep the result open; unrelated project-global dynamic calls are no longer surfaced as semantic bait.
- Adds P1 Context Projection while preserving complete canonical ledgers: Evidence and Observation prompt indexes are bounded to pinned+recent windows, expanded tool contracts are limited to the two most recently requested tools, and current tool-result deltas are bounded before prompt assembly.
- Adds P1 Claim Projection: `final={answer,limitations,evidence_ids}` and Claim receives only Evidence explicitly selected by Main in the final plus Evidence Main already attached to Investigation. Runtime validates IDs/freshness and never infers semantic relevance.
- Clean break: config/session/queue/project-memory schemas are exact 5.7.1. No migration or compatibility bridge from 5.7 is added.
- Adds regressions for duplicate-name import resolution, bounded hot context projection and Main-selected Claim Evidence.
- Separates current-runtime documentation from future architectural direction and rewrites the GitHub README around the shipped coding-agent product instead of revision-by-revision engineering notes.
- Current validation: 177 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.7 — Directed Observation — 2026-08-10

- Preserves the Rev5.6.2 Main/Runtime/Claim authority boundary and does not add a semantic router, planner or new semantic state machine.
- Introduces the canonical Rev5.7 tool result envelope: physical status plus optional `observations`, `coverage`, `frontiers` and `handles` for every executable capability.
- Adds a domain-neutral registry `effect` class (`observe|execute|mutate`) while retaining concrete tool `effects` metadata as the sole physical implementation detail.
- Adds `symbol_relations(query="reachability")`, the first query-shaped observation. With no explicit roots it uses objective Python entrypoint signals, materializes the shortest structural root-to-target path and exposes edge coordinates.
- Positive directed reachability sets `coverage.objective_complete=true` and deliberately suppresses unrelated dynamic frontiers, so a discriminating positive path does not create more exploration debt.
- Negative/incomplete directed reachability exposes only physical/static continuation boundaries that can prevent completion. Large unresolved payloads remain behind handles.
- Adds generic `expand_observation(handle)`: Runtime materializes a bounded page from an opaque observation snapshot without domain interpretation. Snapshot handles are persisted by ObservationLedger and become stale after `workspace_epoch` changes.
- Extends `symbol_relations` ObservationLedger identity with the query mode and uses a 12-hop default for directed reachability (configurable up to 32) without changing the 6-hop local-relations default.
- Keeps the fixed Main prompt within the existing compact-prompt regression while teaching the Main to prefer directed reachability and not walk an already-proven path node by node.
- Adds deterministic Rev5.7 regressions for directed positive paths, unresolved frontier/handle expansion, handle staleness, capability effect classes and bounded model projection.
- Clean break: config/session/queue/project-memory schemas are exact 5.7.
- Current validation: 174 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.6.2 — Property Completion & Adaptive Budget — 2026-08-10

- Treats per-call `max_tokens` as a ceiling instead of a prepaid allocation: Runtime preserves mandatory downstream Claim output and clamps the current call to the remaining completion budget. The 8000 completion fuse and 98000 total physical envelope are unchanged.
- Adds candidate-completion guidance to the Main contract: after materially investigating one chosen candidate, either polarity of the requested property is a valid result unless the request explicitly requires one.
- Directs Claim recovery to the same target/candidate through `semantic_gaps[].required_property` before broadening to another candidate.
- Keeps literal `symbol_relations` text references opt-in and tells the Main LLM to request them only when they discriminate the active property.
- Adds objective `python_main_guard` structural edges for `if __name__ == "__main__": ...` entry calls, without inferring live/dead semantics.
- Exposes bounded Claim `required_properties` in DecisionLedger history so recovery quality is observable without exposing raw prompts or chain-of-thought.
- Clean break: config/session/queue/project-memory schemas are exact 5.6.2.
- Current validation: 166 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.6.1 — Contract Fidelity & Structural Query Control — 2026-08-10

- Fixes `symbol_relations` replay identity so direction, literal-reference projection, depth and edge limits cannot collide.
- Enforces the canonical tool JSON-Schema subset before execution, including enum and array item validation.
- Exposes small enums in the progressive capability index instead of hiding values the Runtime later requires.
- Makes Claim packets expose complete literal grounding refs only (`answer:*`, `evidence:*`, `runtime:*`, `investigation:*`); Claim no longer constructs transport prefixes.
- Tightens Claim structured schemas so malformed grounding refs and noncanonical answer refs fail at the structured boundary.
- Compacts model-facing `symbol_relations` rows while preserving counts and objective coverage.
- Clarifies epistemic stopping: an open Investigation does not require indefinite search when a material, non-redundant attempt cannot establish the requested property.
- Renames ObservationLedger `semantic_signature` to `observation_signature`; no compatibility alias or migration path is retained.
- Deliberately does not add an AST/project-graph cache or a new epoch/state owner. `workspace_epoch` remains only the existing runtime invalidation coordinate for Eyle-owned writes.
- Clean break: config/session/queue/project-memory schemas are exact 5.6.1.
- Current validation: 163 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.6 — Grounded Outcomes & Docker Backend — 2026-08-10

- Replaces Evidence-only Claim grounding with typed coordinates: `request`, `answer:<anchor>`, `evidence:<id>`, `runtime:<fact>`, and `investigation:<target>`. Runtime validates coordinate existence; Claim owns semantic sufficiency.
- Makes `material_satisfaction` explicit as `satisfied|gap|blocked`; truthful physical impossibility can be a valid final outcome grounded by Runtime Facts.
- Removes the schema/validator split that allowed Claim output to be accepted structurally and rejected later for missing Evidence IDs; all Claim verdict/gap grounding arrays are non-empty in the strict schema/parser contract.
- Projects bounded Runtime Facts from ObservationLedger into Claim packets so physical failures such as `SANDBOX_UNAVAILABLE` can be verified without fabricating EvidenceLedger entries.
- Adds job-scoped terminal capabilities: a tool result with `retryable=false` is remembered physically and that capability is removed from later callable views for the same job.
- Extends `symbol_relations` with registry/assignment/callback binding edges plus directed `incoming|outgoing|both` projections and opt-in literal text references.
- Makes Docker the recommended/default strong sandbox backend in `auto`, with Bubblewrap fallback. Docker uses one persistent disposable container per job, default `python:3.12-slim`, `--pull missing`, network access and a writable sanitized snapshot; the real workspace is never mounted read-write.
- Preserves unrestricted package/toolchain installation inside the Docker sandbox while keeping real-workspace writes exclusively behind confirmed `WriteTransaction`.
- Clean break: config/session/queue/project-memory schemas are exact 5.6; previous persisted state is rejected rather than migrated.
- Current validation: 155 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5.5 — Canonical Task Input Integrity — 2026-08-10

- Makes `needs_user` a strict blocking-clarification object and prevents greetings/task-acquisition conversation from becoming false pending work.
- Folds user clarification persistently into the single canonical request instead of a transient `user_response` observation, so Main LLM and Claim audit the same task after intermediate tools.
- Validates pending TTL/project ownership before `user_input` can capture a new message and fixes clarification cancellation ordering.
- Separates per-job physical turn/tool histories from cumulative task state and adds a canonical request-identity invariant across resumed Agent/Claim calls.
- Clean break: config/session/queue/project-memory schemas are exact 5.5.5.
- Current validation: 149 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5.4 — Property-Directed Evidence & General Sandbox — 2026-08-10

- Generalizes Directed Evidence: the Main LLM must identify the actual material property requested and must not substitute easier proxies such as references, imports, compilation, tests or signatures when the stronger property is behavior/reachability/causality/compatibility/completeness/absence.
- Adds `symbol_relations`, a general structural primitive. Python uses AST-aware definitions/calls/imports/decorators/inheritance plus optional root-to-symbol call paths; other source files contribute truthful textual references. The tool never emits live/dead/legacy semantics and reports unresolved dynamic sites explicitly.
- Adds `run_command`, an unrestricted shell capability inside a writable per-job project snapshot. It may use network, install workspace-local packages, compile and test. Only Bubblewrap or configured Docker qualify; weak local-process backends fail closed. Sandbox mutations persist inside the current job but never mutate/authorize writes to the real workspace.
- Makes `find_symbol` a locator in the model view; raw source remains Runtime Evidence and `read_file` is the canonical content tool.
- Makes `inspect_project` model-facing output macro-level instead of replaying its full relation/test inventories.
- Removes Runtime freshness hashes from the Main-LLM Evidence index and slims Observation navigation; canonical ledgers still retain complete freshness/coverage state.
- Detects the exact Investigation failure seen in the `extract_symbols` benchmark: the same structurally invalid target transition repeated without objective state change now stalls/fails on the second repetition instead of burning repeated LLM calls. Runtime still never chooses Evidence IDs semantically.
- Further compresses capability discovery: 17 tools fit in roughly 508 local-estimated tokens in the current registry.
- Keeps the 98k physical message/job envelope, 32k per-call Llama context cap, 24 LLM turns and 64 tool calls as independent physical fuses.
- Clean break: config/session/queue/project-memory schemas are exact 5.5.4.
- Current validation: 143 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5.3 — Progressive Capabilities & Budget Guard — 2026-08-10

- Replaces the full expanded tool catalog on every Agent call with progressive model views: compact `capability_index` for unused callable tools and expanded `active_tools` only after actual Main-LLM requests.
- Tool activation is derived from canonical DecisionLedger events; there is no Tool Selector LLM, activation call, semantic router or persisted active-tool state. First use is directly validated against the canonical `TOOLS[name].input_schema`.
- Removes `tool_taxonomy` from the Main-LLM prompt. Registry category/effect metadata remains Runtime-owned.
- Adds a hard physical inference envelope per user message/job: 90k prompt attempts, 8k completion and 98k physical total tokens. Every backend attempt charges its full prompt even when cached; cache discount is diagnostic only.
- Hard-caps each backend request to the current Llama Server context of 32768 tokens, in both strict config validation and the physical prompt compiler.
- Exposes remaining physical token budget to the Main LLM alongside remaining tool/turn fuses so it can prioritize decisive work without Runtime deciding semantic sufficiency.
- Reframes Investigation in the Agent contract as the Main LLM's own semantic working memory. Multi-candidate audits are instructed to create/close persistent targets instead of carrying unresolved questions only in transient reasoning.
- Skips Claim Review for Finals with zero Observation, Evidence, Investigation and WriteTransaction state. This is state-derived and does not classify the task as simple/chat.
- Clean break: config/session/queue/project-memory schemas are exact 5.5.3; earlier state is rejected, never migrated.
- Current validation: 136 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5.2 — Canonical State Ownership — 2026-08-10

- Applies the ObservationLedger ownership pattern across runtime state: one factual responsibility, one canonical owner, all histories/counters/views derived.
- Adds canonical `DecisionLedger`; deletes parallel persisted decision history and repeated-rejection counters.
- Adds run-scoped `ExecutionContext` with canonical LLMCallLedger; logical prompt metadata and provider attempts now live in the same record.
- Deletes `prompt_snapshots`, separate `llm_responses`, `correlate_prompt_attempts`, logical-call sequence state and mutable `_runtime_agent_budget` hidden in configuration.
- Makes `runtime/history.py` project the canonical `ExecutionTrace` instead of reconstructing the job independently; Prompt Accounting reads LLMCallLedger directly.
- Adds canonical `EvidenceLedger` for Evidence registration, persistence, rehydration, freshness and indexing; Agent no longer owns Evidence lifecycle helpers.
- Evolves `ObservationLedger` into the single owner of physical tool events, replay/coverage identity, pending model-facing results and public tool history. Hot source/replay bodies are not serialized.
- Adds canonical `WriteTransaction`; patches, attempts, validation, failure and rollback live once in the Session. Confirmation pending state stores only `transaction_id` and canonical Session state.
- Stops persisting derived Claim summaries.
- Removes arbitrary fixed item-count truncation from Observation/Decision ledgers.
- Clean break: config/session/queue/project-memory schemas are exact 5.5.2; 5.5.1 state is rejected, not migrated.
- Current validation: 131 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5.1 — Second Deep Cut — 2026-08-09

- Deletes `workspace_scope` end-to-end; physical workspace use is observable from actual tools and patches rather than self-classified by the Main LLM.
- Deletes `final.evidence_ids` / `answer_evidence_ids`; Investigation owns target Evidence and the global Claim pass audits Runtime Evidence.
- Deletes the lexical `request_policy` and parallel Claim `findings[]` subsystem; material delivery remains a semantic Claim responsibility.
- Deletes generic `AGENT_NO_PROGRESS` and physical-state progress fingerprints; only deterministic repeated rejected decisions and replay-only loops are fused.
- Consolidates observation identity, file-range coverage and replay in `ObservationLedger`; removes `relevant_sources`, `visible_source_ranges`, persisted Claim feedback copies and tool-history navigation state.
- Deletes duplicate post-write tool reread; deterministic full-output verification remains the canonical post-apply verification path.
- Makes `memory_search` truly read-only and keeps writes in the canonical `memory_store` path.
- Deletes public `read_range`; `read_file(path, line_start?, line_end?)` is the single file-read ABI.
- Consolidates tool metadata into the executable `TOOLS` registry; removes parallel Agent tool-class sets, `_TOOL_CONTRACTS`, duplicate `name`, `permission`, `output_schema`, and alternate-registry injection.
- Removes Claim/Gaps recovery identities (`claim.id`, `claim.kind`, `semantic_gap.id`, signatures); atomic review records contain only coordinates still used by the global verifier.
- Requires strict JSON Schema for Agent/Claim structured calls; deletes `llm/capabilities.py`, capability cache/negotiation/revalidation, `json_object`/prompt downgrade and structural repair retries.
- Deletes automatic retry after `finish_reason=length`; truncation now fails explicitly as `MODEL_OUTPUT_TRUNCATED` instead of re-running the same inference with a larger ceiling.
- Consolidates transient backend retry policy to one `retry_max_attempts`; removes the Agent-specific transport retry override.
- Deletes dead capability-administration telemetry/history and the frontend `progress_history` block left from Progress Earned Authority.
- Keeps one public task deadline; worker hard-kill is derived with a fixed technical grace instead of exposing a second deadline knob.
- Removes artificial chat/working-set/item-count context caps; actual model window + safety margin is the physical context authority.
- Enforces strict nested configuration fields and rejects every removed key as an error rather than aliasing it.
- Current validation: 125 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.5 — Semantic Authority Reset / Clean Break — 2026-08-09

- Restores the Main LLM as the sole creator of semantic debt: `Investigation=[]` is valid and workspace read/write never implies Investigation.
- Deletes `INVESTIGATION_REQUIRED`, lexical semantic routing, the semantic phase scheduler and Progress Earned Authority; tool/turn/token/deadline limits are physical fuses only.
- Keeps declared Investigation mechanically strict: identity/goal durability, real Evidence bindings, `established` proof requirements and open-target Final blocking remain Runtime invariants.
- Collapses Claim handling to one global semantic review path; `target_id=null` reports omitted debt back to the Main LLM and never creates a Runtime target.
- Enforces one canonical Final object and deletes Final-string compatibility, old Investigation snapshot APIs, specialized Claim recovery protocols and index-based prompt/response correlation fallback.
- Makes Rev5.5 a clean break for persisted state: config, session, queue and project-memory schemas are exact 5.5 contracts; old state is rejected, never migrated.
- Deletes Rev5.2.x bridges, progress-credit backfills, write-only session fields, legacy tool aliases, dead write schemas, dead capability accessors and other confirmed no-caller code.
- Removes historical revision tests that preserved obsolete APIs and replaces them with current architectural invariants.
- Renames prompt navigation state from `investigation_map` to `observation_map` so observation history is not semantically coupled to Investigation.
- Standardizes the active internal observation/tool record ABI on one English vocabulary instead of maintaining PT/EN adapter paths.
- Current validation: 124 passed, 1 skipped because Flask is unavailable in the build environment.

## Rev5.4 — Grounding Unification — 2026-08-09

- Makes the canonical Investigation Contract the single project-grounding authority for workspace Finals.
- Removes `FINAL_PROJECT_EVIDENCE_IDS_REQUIRED`; `final.evidence_ids` are optional direct answer anchors and still reject unknown IDs when present.
- Claim packets expose `answer_evidence_ids` separately while `investigation[*].evidence_ids` remains canonical target grounding.
- Removes public `agent.final_validation_retries`; invalid Finals may only consume ordinary remaining turns.
- Preserves the concrete Final validation failure on the last normal turn instead of masking it as `MAX_LLM_TURNS_EXCEEDED`.
- Clarifies `analysis_answer_only`: close remaining targets from retained Evidence and answer without tools.
- Keeps P1/P2 context retirement, tool catalog changes and Evidence compaction out of this first Rev5.4 implementation.

## Rev5.3.4 — P0 Corrections — 2026-08-09

- Correlates every provider attempt with stable `logical_call_id`, `prompt_snapshot_id`, and `physical_attempt`, eliminating index drift after truncation retries.
- Raises only the closed-Investigation final-answer completion ceiling to 3000 tokens; task-wide completion budget is unchanged.
- Semantic follow-up pins only Evidence explicitly cited by rejected Claims/semantic gaps; reopening a target no longer repins its entire Evidence history.
- Sends full Claim rework feedback once, then a deterministic compact coordinate view on later rework turns.
- Keeps Rev5.3.3 Prompt Cost Accounting enabled so the effect can be measured directly.



## Rev5.3.3 — Prompt Cost Accounting — 2026-08-09

- Added safe prompt-cost accounting over the existing `prompt_snapshots`; no prompt/source/model bodies are exposed.
- Public history and `execution_trace` now expose per-call component sizes plus job aggregates for fixed repeated contract tax, fresh tool-result cost, retained context and Evidence/Investigation state.
- Added provider-vs-local prompt estimate ratios so accounting drift is distinguishable from context bloat.
- Claim verifier prompt snapshots now include component sizes and packet measurements: selected Evidence count, Evidence excerpt width, answer-anchor count and Investigation target count.
- Added observational diagnostics for Evidence amplification, replay-request rate and structurally unreferenced Evidence/tool actions. These counters are explicitly non-semantic and never classify work as wasted.
- Added a History UI section for prompt accounting.
- No context retirement, prompt cropping policy, tool catalog, Investigation, Claim semantics, authority, budgets, public tools or config schema changed.

## Rev5.3.2 — Answer Consistency Gate — 2026-08-09

- Added mandatory `answer_consistency={status,reason}` to the `claim_verifier` structured contract, with `status=consistent|conflict`.
- `answer_consistency=conflict` now blocks provisional Final acceptance even when material delivery is satisfied and every individual Claim is supported.
- Consistency-only debt returns to the Main Agent as answer rework; it does not reopen Investigation or pin new Evidence by itself.
- Added Decision-Ledger fingerprinting and follow-up feedback for visible answer conflicts so repeated unchanged inconsistency cannot spend an unbounded Agent↔Claim loop.
- Targeted Claim/Semantic Gap/Finding/workspace-scope reverifies use prescribed `satisfied` + `consistent` gates and do not rerun global delivery/consistency judgment.
- Added Rev5.3.2 regression reproducing the real benchmark contradiction: one item labeled both confirmed legacy and active, followed by a zero-legacy conclusion; correction succeeds with no extra tool call when retained Evidence already suffices.
- Directed Proof, Investigation, material delivery, tool authority, budgets, public tools and config schema remain unchanged.

## Rev5.3.1 — Material Delivery Gate — 2026-08-09

- Added mandatory Claim Verifier `material_satisfaction={status,reason}` with `status=satisfied|gap`.
- A provisional Final can no longer be accepted when the verifier says the requested material result was not actually delivered, even if every factual Claim is supported.
- Delivery-only gaps return to the Main Agent for direct Final repair without forcing new Investigation or tool use; `semantic_gaps` remain the route for omitted Evidence or missing/partial investigation.
- Material-delivery debt is included in reviewer loop fingerprinting and persisted in Claim Review history.
- Targeted verifier recovery/reverification stays local and does not rerun global delivery judgment.
- Directed Proof, Investigation, committed-progress authority, public tools, budgets, and config schema remain unchanged.
- Added Rev5.3.1 regressions reproducing the benchmark failure: supported Claims plus a capado Final are rejected, repaired, reverified, and accepted with no extra tool call.

## Rev5.3.0 — Directed Proof & Material Satisfaction — 2026-08-09

- teaches the Main Agent to identify the observable fact that would actually confirm/refute a material property before choosing tools; related Evidence and surface markers no longer count as decisive proof by default;
- separates candidate discovery from verdict: names/comments/keyword markers may nominate candidates, while conclusions require Evidence that discriminates the requested property;
- makes fresh Evidence explicitly non-equivalent to epistemic progress and encourages separate Investigation targets only for material independent verdicts;
- adds minimum-sufficient-proof discipline so the Agent stops once deeper ancestry cannot materially change the verdict;
- restores a user-facing Final contract: concrete result first, no internal Runtime/Investigation/Claim narration unless requested, and audit findings surfaced as what/where/verdict/practical reason;
- expands the existing Claim Verifier, without another LLM call or schema, to judge Claim truth, material task satisfaction and material target closure; true-but-incomplete answers now produce semantic debt;
- strengthens Claim follow-up so the Agent distinguishes answer-only debt from missing investigation and preserves the requested property instead of substituting an easier proxy;
- keeps Rev5.2.9 authority, ObservationLedger, Evidence, Investigation schema, public tools and two-brain semantic ownership intact.

## Rev5.2.9 — Progress-Earned Authority — 2026-08-09

- removed the artificial cumulative `max_earned_tool_extension=8` ceiling; every runtime-validated committed-progress epoch can unlock exactly `committed_progress_extension_calls` additional physical tools once;
- keeps extension credit dormant until the physical gate needs it, but converts every still-unspent progress epoch instead of discarding older earned progress;
- makes `investigation_updates.evidence_ids` a true additive delta: previously committed target Evidence is retained automatically and never has to be resent;
- adds a durable global credit-once Evidence ledger so the same Evidence ID cannot mint committed progress again through target reopen, target cloning or later semantic remapping;
- adds deterministic Claim-rework capacity feedback (remaining Agent calls, current physical authority and pending progress-funded authority) so the Agent can spend scarce follow-up calls deliberately;
- keeps the two-brain architecture, 12-tool base fuse, 8 normal Main-Agent turns and 12 task-wide LLM calls unchanged.

## Rev5.2.8 — Canonical Runtime Cleanup — 2026-08-09

- fixes false `ADMINISTRATIVE_LOOP` equivalence by keying rejected decisions on objective observed state plus the relevant physical-authority context;
- changes runtime-cycle progress to ignore free-form Investigation `reason/status` churn and count only observed reality, Evidence bindings, committed progress and workspace mutation;
- rejects any invalid tool batch atomically before physical authority so `INVALID_ARGUMENT`/phase contracts cannot be masked by a budget rejection;
- standardizes the public tool ABI on `path`, `line_start`, `line_end`, `symbol`, `limit`, `depth`, `filter`, `query`, `scope` and related existing English fields; legacy argument aliases are not accepted;
- teaches the existing Investigation contract that `open` targets may accumulate Agent-selected Evidence incrementally and recommends separable targets for independently provable debts;
- removes dead lexical workspace/write authority helpers and the `_semantic_read_signature` compatibility wrapper; renames current-context coverage to `_source_already_visible`;
- keeps the two-brain architecture, Observation Ledger, Claim follow-up, tool-credit policy and all physical limits unchanged; adds focused Rev5.2.8 regressions.

## Rev5.2.7 — Two-Brain Claim Follow-up

- Removed the `claim_repair` semantic profile, prompt, schema, state and local answer-rewrite pipeline. Production task semantics now belong only to `agent` and `claim_verifier`; the structured capability probe remains transport-only administration.
- `contradicted`, `insufficient` and semantic gaps now use one deterministic follow-up route: Runtime reopens reviewer-declared Investigation targets, pins cited Evidence and returns the reviewer debt to the Main LLM.
- Added Claim follow-up loop protection through the existing Decision Ledger. Identical reviewer debt against an unchanged canonical state fails as `CLAIM_REVIEW_STALLED` instead of spending repeated Agent/Claim cycles.
- Added a bounded Claim rework lane that uses only unused task-wide `max_llm_calls` capacity and reserves one later verifier call; `max_llm_turns` remains the normal investigation limit.
- Tightened `committed_progress`: only newly linked runtime Evidence can mint physical tool authority; a pure `established` status flip, including re-establishing a Claim-reopened target with the same Evidence, earns no credit.
- Fixed local Claim protocol recovery so recovered Claims preserve `target_id`.
- Kept Observation Ledger, transactional Investigation authority and tool-credit rules unchanged.


## Rev5.2.6 — Observation Ledger & Unified Runtime Preflight — 2026-08-09

- adds a persistent runtime-owned `ObservationLedger` keyed by normalized observation + `workspace_epoch`;
- replays/rehydrates identical observations without consuming physical tool calls, including `A -> B -> A`;
- records complete zero-match `search_code` and `SYMBOL_NOT_FOUND` as citable negative observations without assigning semantic meaning;
- moves authority/`earned_extension` after unified preflight so replayed/invalid/batch-duplicate calls cannot consume or earn physical authority;
- adds runtime-cycle progress accounting that survives early `continue` paths and a Decision Ledger that fails repeated identical rejected batches as `ADMINISTRATIVE_LOOP`;
- removes legacy `IDENTICAL_READ_BLOCKED`, `IDENTICAL_OBSERVATION_BLOCKED`, `SEMANTIC_READ_BLOCKED` execution paths and the pre-Rev5.2.5 full `investigation` snapshot fallback;
- keeps Claim Review unchanged as the separate semantic second brain and keeps the physical limits unchanged (8 Main turns, 12 LLM calls, 12 base tools, +4 earned extension cycles capped at +8);
- adds Rev5.2.6 regressions for replay, negative Evidence, persistence, workspace epochs, preflight authority and administrative-loop rejection.

## Rev5.2.5 — Transactional Contract Authority — 2026-08-09

- removed the Rev5.2.4 coupling where Claim Review minted tool credit; Claim Review remains only the second-brain semantic verifier of provisional conclusions;
- changed the Main Agent contract from full `investigation` snapshots to `investigation_updates` deltas while runtime owns the canonical Investigation Contract;
- applies valid target updates independently, preserves committed siblings when another update is rejected, and prevents committed Evidence from silently disappearing;
- records one objective `committed_progress` epoch per productive Main-LLM update cycle instead of rewarding Claim count, target count, or tool `ok=true`;
- keeps the 12-tool base fuse and grants dormant +4 `earned_extension` only at the physical budget gate when open debt remains and new committed progress exists since the previous extension, capped at +8;
- keeps tool batches atomic and keeps the history expand-all/collapse-all control while exposing committed-progress deposits and earned extensions;
- keeps 16 public tools, 8 Main-LLM turns, 12 base tool calls and the 9k completion budget unchanged; adds 13 focused Rev5.2.5 regressions.

## Rev5.2.4 — Verified Progress Budget — 2026-08-09

- kept the physical base fuse at 12 tool calls and added reviewer-earned tool credit: +4 when Claim Review confirms structurally new supported material while semantic debt remains, capped at +8 bonus in this release;
- prevented repeated supported Claims from minting duplicate budget by persisting structural support signatures in `AgentSession`;
- made tool batches atomic against the currently authorized budget: an oversized batch executes zero tools and returns the allowed batch size to the Main LLM for semantic reprioritization;
- exposed base, earned bonus, effective tool limit and bonus cycles in safe observable history/trace data;
- added `expandir tudo` / `recolher tudo` to the execution-history UI so all LLM/decision/tool accordions can be opened with one click;
- kept 16 public tools, 8 Main-LLM turns and the 9k completion budget unchanged; added seven focused Rev5.2.4 regressions.

## Rev5.2.3 — Investigation Memory & Progress Semantics — 2026-08-09

- separated current-prompt source visibility from historical source telemetry; historical reads can no longer suppress a needed reread after the body leaves the prompt;
- pinned Evidence named by insufficient Claims/Semantic Gaps and reopened Investigation targets across semantic follow-up;
- changed no-progress accounting so `ok=true` without new Evidence/state change is not progress;
- suppressed unchanged repeated `project_stats`, `inspect_project`, `count_tokens`, `agent_info` and same-scope `run_tests` until an observable state-changing action;
- kept 16 public tools and the existing 8-turn / 12-tool / 9k-completion physical limits unchanged; added eight focused Rev5.2.3 regressions.


The entries in this section are pre-public engineering milestones. They are retained for technical context only; Git history is the canonical record of the detailed development sequence.

## Rev5.2.2 — Runtime Contract Hardening — 2026-08-08

- replaced production authority from lexical `request_needs_project_evidence` / `request_requires_write` classifiers with a Main-LLM-declared `workspace_scope` contract (`none|read|write`);
- added fail-closed scope-only semantic review for non-chat finals that declare `workspace_scope=none` while a workspace is active, so semantic disagreement returns control to the Main LLM without runtime keyword inference;
- block patch proposals and confirmed write resumes while any Investigation target remains `open`;
- changed semantic-follow-up completion reservation to exactly one configured verifier call (default 900 tokens), preventing historical Claim/gap count from starving the next Main LLM turn;
- rehydrate persisted file Evidence on resume from the exact path/range only when stored file/content hashes still match; stale Evidence releases its read coverage for fresh investigation;
- replaced the process-local persistence lock with a portable OS-backed interprocess file lock;
- unified secret-path/content policy across workspace reads, code search/symbol reads, Git status and Git diff;
- kept 16 public tools and the existing 8-turn / 12-tool / 9k-completion physical limits unchanged; added eight focused Rev5.2.2 regressions.

## Rev5.2.1 — Semantic Follow-up Contract Recovery — 2026-08-08

- added nullable `target_id` to every Claim so the Claim Verifier can explicitly bind an insufficient Claim to an existing Investigation target;
- runtime now reopens targets only from reviewer-declared `target_id` mappings in Semantic Gaps or insufficient Claims; `null` never creates a target;
- replaced contradictory `NO_PROGRESS_ANALYSIS: stop using tools` behavior during semantic follow-up with `SEMANTIC_FOLLOWUP_STALLED`, which reports the stall/open targets but leaves the next semantic action to the Main LLM;
- made agent `final` canonical as exactly `answer`, `evidence_ids`, and `limitations` in both strict provider schema and local parsing;
- when Claim Review has already returned the task for follow-up, reserve an elastic verifier completion budget, sized from the already-observed prior review item count through the existing Claim Review budget logic, because another review is then a known mandatory stage;
- observable Investigation Contract decisions now expose compact `Tn=status` state summaries and explicit reopen events;
- kept 16 public tools and the Rev5.2 physical limits unchanged; added six focused Rev5.2.1 regressions.

## Rev5.2 — Investigation Contract & Directed Evidence — 2026-08-08

- replaced the free-form agent `plan` with a persistent Investigation Contract stored in `AgentSession`;
- added target states `open`, `established`, and `dismissed` with deterministic identity/Evidence invariants and no semantic runtime scoring;
- project-grounded finals cannot pass while declared material targets remain open;
- extended Semantic Gaps with nullable `target_id`, allowing Claim Review to challenge/reopen an existing target or report material scope absent from the contract without runtime-created semantics;
- target-linked Evidence is pinned as compact index metadata and included in semantic review even when it is older than the recent Evidence window;
- preserved `investigation_map` as navigation history, separate from semantic investigation purpose;
- corrected `inspect_project` observable summaries to retain the current entrypoint/test/CI/framework/relation signal schema;
- removed the lexical test-only phase shortcut; test wording no longer lets runtime semantically decide that further investigation is unnecessary;
- kept the 16 public tools and existing physical limits unchanged; no Planner/Manager agent, semantic file-ranking heuristic, new read-range coverage layer, or callers/callees/reference tools were added;
- added dedicated Rev5.2 regressions covering contract transitions, target reopening, Evidence pinning, schema integration and directed semantic follow-up.

## Rev5.1 — Context Boundaries & Investigation Continuity — 2026-08-08

- made `request` the explicit sole active task and replaced one-turn/duplicated context behavior with stable per-job `conversation_background`;
- preserved explicit ongoing conversational instructions across tool turns while marking prior-task context as non-authoritative;
- added compact `investigation_map` derived from observable successful tool history so `CLAIM_INSUFFICIENT` follow-up keeps navigation discoveries after bulky source views are cleared;
- blocked repeated/covered reads no longer increment the executed-identical-tool loop and now return the prior observable investigation map;
- added Local Finding Recovery after Claim recovery, preserving Claims/Semantic Gaps and regenerating only Findings before global revalidation;
- capped agent batches at four tool calls in provider schema and local parser and removed silent `calls[:4]` truncation;
- tagged failed assistant jobs and excluded them from future conversation background;
- removed obsolete `agent.task_context_token_budget` from the public config schema.

## Rev5 — Pre-public consolidation milestone — 2026-08-08

Rev5 was an internal consolidation milestone built from the validated Rev4.13.13 runtime. It was not the public release baseline. It consolidated the agent loop and removed accumulated development-document clutter on the path to Rev5.7.1.

### Current architecture

- One `AgentSession` execution loop.
- 16 public deterministic agent tools.
- One model-facing write protocol: `action=patches` → transactional dry-run → confirmation → apply → validation/rollback.
- Runtime-owned Evidence with proportional model-visible views.
- Deterministic Final Gate followed by one semantic Claim Review.
- Local Claim and Semantic Gap recovery without runtime semantic invention.
- Adaptive structured handshake per connection/model: `json_schema` → `json_object` → prompt JSON.
- Provider enforcement plus authoritative local Eyle validation.
- One structured contract source in `llm/structured.py`.

### Repository cleanup

- Removed historical root implementation/validation reports.
- Removed accumulated `docs/releases/` notes and obsolete Rev4.11 engineering notes.
- Removed redundant `UPDATE_HISTORY.md`.
- Rewrote README and canonical docs around the current architecture only.
- Preserved source, tests, dependency locks, assets, governance/security files, and machine-state `.gitkeep` directories.

## Development baseline — Rev4.13.13

The Rev5 codebase inherits the completed Rev4.13 line: canonical structured profiles, adaptive capability probing, deterministic answer anchors, proportional Claims/Evidence, Semantic Gaps, local Claim follow-up/Reverify, local Semantic Gap recovery, bounded runtime telemetry, and supervised transactional writes.
