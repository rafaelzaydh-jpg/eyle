## Rev4.0.0 — Task-Anchored Cognitive Surfaces

- Closes Rev3 with Rev3.7.8 as the physical recoverable-continuity baseline and begins Rev4 as an architectural prompt-surface revision.
- Adds explicit Main-authored `AgentSession.active_task_id` binding to the existing Memory Graph Task; no Task DB/TaskFrame/planner is introduced.
- Adds exact-ID Active Task projection with no recall, ranking or neighbor expansion.
- Splits the current structured cognition protocol into `navigation`, `explore`, and `build` surfaces while preserving exactly three ECC movements: explorar/construir/concluir.
- Navigation receives a compact capability directory; detailed schemas are materialized only for the selected Explore or Build family.
- Explore exposes only observe/execute capabilities and can batch independent Main-authored operations; Build exposes only one mutate operation attempt.
- Surface transitions return through Navigation; Runtime never chooses the next ECC movement from meaning.
- Checkpoints persist `active_task_id` and `cognitive_surface`; execution continuity advances to `execution-continuity-v6` and pending schema to `16-ecc`.
- Adds surface/token telemetry (`navigation_calls`, `explore_calls`, `build_calls`, `task_bind_count`, `surface_transitions`).
- Fixes fixed-point checkpoint serialization so the `checkpointed_blocks` mark is persisted before checkpoint creation, preventing duplicate recovery checkpoints after restart.
- Current Session/config schema: `2.7.5-r4.0.0-ecc`.

## Rev3.7.8 — Reality-Bound Recoverable Continuity

- Binds live-source `read_file` Frontiers to the canonical whole-file `source_version` already used by file Materials.
- Binds each pending `search_code` range to the exact source revision that produced its locator.
- A continuation whose live source changed is rejected mechanically as `FRONTIER_SOURCE_REVISED`; the Frontier becomes `stale` while historical Evidence/Materials remain intact.
- Mechanical file coverage is revision-aware and never merges line ranges from different source revisions.
- Separates stable Host-owned `provider_identity` from mutable provider execution context for persisted continuation binding.
- Replaces `provider_context_hash` with `provider_identity_hash` in the current pending schema.
- Makes `recoverable_execution` storage singular per `execution_id`, using atomic replacement and monotonic `checkpoint_generation`.
- Keeps human confirmation/semantic-choice continuations independently addressed.
- Runs post-write `compileall` with Python `-S` so deterministic syntax verification cannot execute unrelated host `sitecustomize` hooks.
- Current schemas: Session/config `2.7.5-r3.7.8-ecc`, pending `15-ecc`, execution continuity `execution-continuity-v5`.
- No semantic wandering detector, automatic Frontier consumption, Evidence usefulness score, or Runtime planning was added.

## Rev3.7.7 — Persisted Recoverable Continuity

- Adds `recoverable_execution` as a Runtime-owned, non-interactive continuation kind for canonical checkpoint/resume.
- Persists `AgentSession` recovery state including hot pending Runtime results, Evidence, Observation Ledger, Frontiers, `reality_epoch`, fixed-point blocks and execution budget accounting.
- Budget salvage and first local fixed-point block can create a durable checkpoint when the execution has a stable identity; Service resumes it automatically and a restarted worker can rehydrate it by `execution_id`.
- Adds **Execution Convergence Signals** (`operations_since_task_state_progress`, `provider_tokens_since_task_state_progress`, `fixed_points_blocked`, `coverage_advanced`, `physical_mutations`). These are mechanical facts only; Main interprets whether they indicate legitimate investigation, consolidation pressure, wandering or a need for more exploration.
- Adds accumulated mechanical file coverage/open-Frontier state to the model and diagnostic surfaces.
- Recovery guidance is factual/coordinate-based rather than a Runtime semantic recommendation.
- Adds telemetry for recovery checkpoints/resumes and avoided observation replays.
- Keeps the Runtime boundary explicit: no heuristic relevance, sufficiency or semantic wandering detector was added.

# Changelog

## Rev3.7.6 — Recoverable Fixed-Point Runtime

- Fixed P0 no-progress behavior: deterministic fixed points now block only the repeated action instead of terminating the whole ECC task.
- Cached Observation replay now preserves active `evidence_ids` and open `frontiers`, allowing `recall`/`continue` recovery after an accidental repeated read.
- Added explicit `recovery_required` results for blocked actions; blocked actions are not physically re-executed/replayed.
- Real observable progress clears the local fixed-point block and resumes normal exploration.
- Added `BUDGET_SALVAGE` feedback in the final 15% of provider token budget so Main can consolidate evidence and conclude before hard exhaustion.
- Added regressions for large-file replay/Frontier recovery, fixed-point recovery, alternate-path recovery, and budget salvage.


This file records Eyle's public architectural evolution. Current behavior is documented in `README.md` and `docs/`; Git history remains the detailed implementation record.

## Rev3.7.5.1 — Provider Contract Delivery

- Delivers Eyle's caller-supplied JSON Schema exactly once to the provider as the representation contract; Adapter remains branch-neutral and owns no Eyle semantics.
- Removes the duplicated textual ECC wire reminder from Core/Main prompt construction.
- Keeps exactly one format-only repair, now isolated to schema + previous candidate + validation errors instead of replaying conversation, Memory, tools and Task context.
- Treats provider `finish_reason=length` as truncation, not as a formatting defect; no second repair generation is launched for a truncated candidate.
- Adds Adapter boundary telemetry for structured-contract characters and repair context mode.
- Adds no new prompt/output token ceiling; token savings come from removing repeated/irrelevant context.
- Documentation refresh: reorganized the root README around the project rather than the revision, added a documentation index and commercial-use summary, aligned the closed-contribution policy with the current project, and removed stale/duplicated documentation contracts.

## Rev3.7.5 — Simple Adapter Boundary

- Restored the component rule: Adapter owns connection/mechanical wire conformance; Core owns Eyle logic.
- Current ECC wire is strict at the caller-supplied JSON Schema boundary, while Memory sidecar semantics remain independently validated by Eyle.
- Adapter performs mechanical JSON recovery and at most one format-only provider repair; no Eyle-specific aliases/capability negotiation/global execution budget live there.
- If the repaired candidate remains invalid, Eyle preserves Session/observations and may request one fresh current decision; this allowance resets only after real execution progress.
- Removed cognition/protocol episode tracking from Core. Runtime retains only deterministic valid-execution fixed-point detection.
- Removed static handshake/model-discovery surfaces; `/health` and `/ready` report only facts they can actually prove.

## Rev3.7.4 — Bounded Cognition & Episode Safety

Corrective release on Rev3.7.3 focused on the execution loop exposed by a trivial greeting.

- replaces consecutive-fingerprint recovery with execution-local cognition episodes;
- protocol error budget is preserved across syntactically valid decisions until Runtime proves observable progress;
- different structured-error shapes cannot rotate to evade the repair bound;
- repeated deterministic action/result fixed points terminate with `ECC_NO_PROGRESS_UNRECOVERABLE` after one feedback opportunity;
- long cognition with novel Runtime results remains allowed; no `MAX_TURNS` ceiling was added;
- Memory read/navigation success no longer masquerades as physical progress;
- `memory_activate` dispatch now accepts its documented `domain` and `context_key` filters;
- first-call token telemetry is correctly classified as `normal`, not `continuation`;
- ordinary conversational requests are explicitly directed to `concluir` without manufacturing body/Memory evidence.

## Rev3.7.3 — Coherence, Continuity & Self-Identity

Corrective release on Rev3.7.2. No planner, semantic router, embedding ranker or hidden Memory projection was added.

- current request is transported as the final user message after native-role recent conversation;
- Eyle's operational self identity is explicit (`source="eyle"`), distinct from the user's `workspace`;
- conversational guidance avoids default help-desk closings and requires safe negative historical lookup;
- Memory v12 projection preserves `domain` and `context_key`, and `memory_activate` can filter by those physical fields;
- activated Memory bodies are materialized only in `memory_view`; operation observations remain compact;
- Runtime feedback is materialized under a physical token budget;
- conversation materialized/omitted counters are surfaced in diagnostics.


## Rev3.7.2 — Canonical Cut Review

Rev3.7.2 is intentionally a cut-only consolidation release. It introduces no new cognitive move, semantic router, provider or Memory tier.

- promoted `eyle.providers.standard` to the single bundled Standard provider package and removed the dynamic facade/`standard_impl` path;
- removed in-runtime configuration identity upgrades; only the exact Rev3.7.2 config schema is accepted;
- made Session and pending-continuation persistence current-schema only;
- made Memory Graph v12 the only runtime graph format; retained v11→v12 solely as an explicit one-shot devtool;
- removed cognitive deadline/generated-token compatibility fields and kept provider usage as the execution ledger authority;
- removed fixed conversation snapshot count and automatic Temporary/global Memory projection paths;
- removed old search/file paging knobs and canonicalized current public paging fields;
- removed old Service/Web confirmation aliases and global pending migration path;
- removed Adapter local `max_tokens` input alias and old Adapter environment aliases; current local transport uses `max_completion_tokens`, `UPSTREAM_API_KEY` and `MODEL`;
- removed obsolete root implementation-note files; current docs describe only active paths while this changelog preserves history;
- replaced historical release-verifier rules with fail-closed Rev3.7.2 canonical-path gates.

Migration policy: compatibility is not maintained in the normal runtime. Where persisted user data still requires a safe transition, use an explicit migration tool before starting the current runtime.


## Rev3.6.1 — Deterministic DeepSeek Adapter

Rev3.6.1 keeps the Rev3.6 Core/Runtime architecture and replaces the over-generalized Adapter with the older deterministic philosophy.

- one explicit `deepseek_v4` provider profile and one configured `MODEL`; no remote model discovery or `auto` resolution;
- structured cognition always uses DeepSeek `json_object`, generic JSON recovery, Draft 2020-12 validation, and at most one format-only repair;
- `reasoning_mode=off/on` maps directly to `thinking.type=disabled/enabled`; `max_completion_tokens` maps directly to `max_tokens`;
- removed runtime structured-mode negotiation, capability caches, provider-specific extra-body injection and Adapter cache negotiation;
- static handshake/readiness remain only to prove the local Eyle transport contract and configuration, never to probe the provider;
- streaming requests request provider usage explicitly, and Core accepts the final usage-only SSE chunk so the 150k per-message ledger stays provider-accounted;
- `MODEL=deepseek-v4-flash` and `UPSTREAM_BASE_URL=https://api.deepseek.com` restore the old low-friction defaults;
- fixed import/dependency regressions uncovered in the Rev3.6 `standard.py` module split, without changing the public Standard provider surface.

**Invariant:** Adapter translates transport; Eyle Core owns cognition semantics. Adding another provider means adding another explicit profile, not teaching the Adapter to guess.

## Rev3.6 — Unbounded Cognition & Provider-Billed Budget

Rev3.6 uses Reachable Paging as its base and hardens the implementation without adding another planner or semantic authority.

- split the bundled Standard provider into focused implementation modules while preserving the public `eyle.providers.standard` compatibility surface;
- removed configurable cognitive ceilings for tree/file/search/diff/project inspection and the task-wide wall-clock deadline; physical page/context/sandbox/HTTP boundaries remain mechanical safety only;
- a single user message/execution now shares a default **150,000 provider-reported total-token budget** across all LLM calls; `usage.total_tokens` is authoritative, with explicit prompt+completion fallback only when necessary;
- the physical context window is independently declared as **50,000 tokens per call**;
- pending confirmations/semantic choices are persisted per execution/pending ID rather than in one global slot;
- Memory Graph v11 adds recall-snapshot execution ownership and crash-orphan GC without TTL-based deletion of learned Memory;
- removed silent broad exception swallowing; boundary catches must surface, transform, warn, or re-raise failures;
- hardened Adapter request-field ownership, usage aggregation, HTTP body handling and health exposure;
- aligned release identity on Rev3.6.

**Invariant:** Eyle may learn/explore as much as the task genuinely needs; Runtime constrains physical safety and the explicit provider-billed per-message budget, not semantic curiosity.

## Rev3.5 — Efficient Cognition & Evidence Depth

Rev3.5 is based directly on Rev3.4. It does not add a planner, hidden relevance layer, Active Projection, or new Memory tier. The release targets intelligence-per-token: keep full physical truth reachable while sending Main a smaller, clearer cognitive surface.

- `inspect_project` keeps the complete objective scan as Material/Evidence but projects a compact structural map to Main/UI instead of replaying hundreds of import edges and test paths;
- the ECC operation catalog now sends terse wire hints instead of repeating full provider-schema prose on every cognition turn; Runtime still validates the complete provider-owned schemas;
- the stable ECC prompt and structured-wire reminder were compressed while preserving the existing authority, Memory, provenance, Frontier, source-identity and safety contracts;
- analysis/review/audit requests explicitly treat inventories as orientation rather than proof and ask Main to inspect representative implementation before making behavioral/architectural claims;
- `list_tree` uses `max_tree_entries` as a real materialization-page maximum (default 80). Larger requests remain fully reachable through exact Frontier continuation;
- `read_file` now treats both whole-file reads and large explicit ranges as reachable scopes rather than permission to dump an arbitrarily large body into one prompt: one configured page is materialized and the exact remainder is kept behind Frontier;
- Observation Frontier IDs accept their mechanically equivalent compact numeric spelling (`fr-1` for published `fr-0001`), preventing valid continuation intent from failing only because a model removed zero padding;
- Memory write count remains semantically unbounded: `memory_delta` has no `maxItems`, and Runtime applies every valid Main-authored graph action rather than imposing a small project-memory quota;
- fixed the Rev3.4 ECC dispatch mismatch where `scope=global` was advertised and supported by the Memory Graph but rejected by `memory_overview`/`memory_activate`;
- no Active Projection is reintroduced.

Measured on the Rev3.4 failure scenario (`inspect_project + project_stats + list_tree(depth=3,limit=200)` over the Eyle tree), the local semantic packet estimate fell from about **19.9k to 6.0k tokens (~70%)** before provider tokenization, with full detail still reachable through Evidence/Frontier.

**Invariant:** compact presentation may reduce what is materialized now, never what Main can eventually reach.

## Rev3.4 — Provenance & Global Reachability Consolidation

Rev3.4 promotes the high-return Rev3.X provenance/reachability work into mainline while deliberately excluding the Active Projection experiment. The release keeps Main as the sole semantic authority and improves Memory lineage and reachability without introducing a working-set subsystem.

- Memory/relation semantic supports freeze the exact referenced source revision;
- relations can themselves be revision-pinned semantic support;
- history exposes revision-specific provenance plus changeset execution/turn;
- `scope=global` explicitly reaches all worlds while `scope=all` keeps Rev3.3 compatibility;
- neighbour recall records physical expansion lineage (`from_node`, `via_relation`) without treating navigation as semantic support;
- v9→v10 migration preserves legacy Memory anchors as `legacy_unpinned` instead of inventing source revisions;
- Rev3.X v10-exp stores are promoted identity-only to the final v10 physical schema;
- Rev3.X configs are accepted as migration inputs and their retired `task_active_projection` lab field is removed during normalization;
- Active Projection is not part of Rev3.4: there is no task-derived prompt projection, activation list, hot tier, semantic relevance scorer, or hidden task-local recall boundary.

**Invariant:** optimization may change materialization cost/order but may never make previously reachable Memory inaccessible.

## Rev3.3 — Task Memory consolidation

Rev3.3 consolidates Rev3.2.1 without adding a hot/working-set memory tier. It introduces Task Memory as a first-class structure inside the existing Memory Graph.

- Memory Graph schema advances to v9 with one mechanical `memory_tasks` lifecycle table; existing v8 node/edge content migrates without semantic rewriting.
- a task is still an ordinary `mem-*` node authored as `kind=task`; Runtime initializes only its lifecycle state and state revision.
- `task_status` changes only `active|blocked|resolved|cancelled`; semantic task/problem/result content continues to use ordinary `revise` and open `relate`.
- normal Memory is current-by-default: revise the same continuing node when current understanding changes; revision history preserves earlier states.
- `memory_overview` exposes body-free task counts/recent task IDs, and normal Memory projections expose task lifecycle metadata.
- Task Memory never narrows recall. `memory_activate` remains global by default and can reuse knowledge from any other task/project.
- retired `memory_focus` from the cognitive surface. No hot-memory/working-set subsystem is carried into Rev3.3.
- general Memory edits alone no longer reset task stagnation; physical/navigation progress or an explicit Task Memory lifecycle transition does.

## Rev3.2.1 — Thinking-off transport hotfix

Rev3.2.1 keeps the Rev3.2 architecture unchanged and fixes one cost-control issue observed with DeepSeek V4 Flash: provider-native thinking/reasoning was enabled by default and could dominate completion usage.

- `llm.reasoning_mode` now defaults to `off`; `provider_default` is the explicit opt-in escape hatch.
- Eyle sends only the provider-neutral reasoning mode to the local Adapter.
- Adapter Rev3.2.1 translates `off` to the provider transport. DeepSeek V4 model IDs use the documented `thinking.type=disabled` mapping; other providers can define `UPSTREAM_REASONING_OFF_BODY_JSON`.
- The formal handshake advertises `client_reasoning_control`, so an older Adapter is rejected before a paid generation.
- Per-call telemetry records `reasoning_mode_requested`.

## Rev3.2 — Durable sandbox promotion + runtime hardening

Rev3.2 closes failure modes observed in long real executions without changing the Main/ECC/Memory authority model:

- made atomic text replacement byte-stable across LF/CRLF hosts so exact post-write verification does not fail because of platform newline translation;
- made the execution-wide generated-token fuse a per-call physical ceiling: Eyle sends only the remaining completion budget and the transport-only Adapter maps it to the configured upstream output field; internal structured retries share the same remaining budget;
- extended the formal Adapter handshake with `client_completion_ceiling`; incompatible older Adapters fail before paid generation;
- changed `runtime_feedback` into an active repair surface: resolved protocol/Memory/no-progress errors are evicted from the hot prompt instead of accumulating indefinitely;
- added Main-controlled `memory_focus` so long executions can explicitly keep an exact Memory working set hot without Runtime ranking or hidden semantic trimming;
- added `promote_sandbox`: Eyle may build, download/clone, run and repair a file or whole project inside the persistent command sandbox, freeze the exact tested candidate as a hash-bound staging artifact, then request one confirmation to promote it into the real workspace;
- promotion supports binary files, `merge` and explicit `mirror`, exact workspace freshness checks, rollback, and post-promotion byte verification;
- fixed the single-message delete button, whose UI handler previously referenced a missing JavaScript function;
- added cognitive escalation guidance: ordinary capability errors should use their explicit contract first; self-inspection of Eyle internals is reserved for Eyle work or physically inconsistent tool behavior.

## Rev3.1 — Active execution clock + structured user interaction

Rev3.1 is a focused correctness/UX update over Rev3:

- changed `task_deadline_seconds` from an absolute wall-clock deadline into accumulated **active execution time**; human wait at confirmation/choice gates no longer consumes task budget;
- kept interaction expiration as a separate wall-clock TTL;
- added Main-authored semantic choices that pause and resume the same logical ECC execution instead of starting a context-poor new job;
- replaced user-facing `confirmar <ID>` instructions with Accept/Reject UI actions while keeping IDs internal for replay/tamper safety;
- expanded deterministic Memory wire normalization for common aliases, omitted temporary retention, singular/list forms and numeric serialization variants;
- reduced structured-recovery feedback to shape-only diagnostics so malformed Memory output does not echo large rejected patches back into the next prompt.

## Rev3 — Consolidated ECC + Memory

Rev3 is the first publication-oriented consolidation release. It does not introduce a new cognitive architecture. It packages the mature state reached through the late Rev2.x development cycle into one coherent public surface.

### Consolidated architecture

- one Main LLM as semantic authority;
- exactly three ECC moves: Explore, Build, Conclude;
- one intrinsic SQLite-backed Memory Graph;
- epistemic/temporal node and relation metadata;
- same-Main memory consolidation and revision;
- Main-authored associative recall cues;
- FTS5/SQL recall with exact DB-backed Frontier cursors;
- deterministic wire canonicalization before strict local ECC validation;
- transport-only, provider-neutral Adapter on local port 8080;
- formal Adapter handshake/readiness negotiation;
- logical execution continuity across confirmation/resume;
- generated-token fuse, absolute deadline, transactions, rollback and post-write verification.

### Publication cleanup

- README rewritten as a project presentation rather than a revision report;
- architecture/configuration/memory/model docs made revision-neutral;
- obsolete Rev2.x release notes and implementation audits removed from the public artifact;
- stale pre-ECC security documentation removed;
- Adapter documentation consolidated;
- misleading local-LLM branding removed from public assets;
- generated runtime directories are represented only by ignored `.gitkeep` placeholders.

## Rev2.9 — Cognitive maturity

Added Main-authored `recall.aliases`, `recall.concepts`, and `recall.cues`, multi-query recall, exact relation-label navigation, and factual consolidation-directory signals while preserving Runtime semantic neutrality.

## Rev2.8.8 — Execution continuity

Preserved token fuse, absolute deadline, provider usage and logical execution identity across confirmation/resume; introduced the formal transport-only Adapter handshake and enforced the local Adapter boundary.

## Rev2.8.7 — Scalable recall and relation revision

Moved Memory recall to SQLite FTS5/SQL with persisted exact selections and DB cursors. Added revisable epistemic relation metadata and large-batch Memory indexing.

## Rev2.8.6 — Structured transport closure

Separated tolerant model wire from strict canonical ECC, restored deterministic normalization, made the Adapter semantically blind, and turned malformed semantic envelopes into feedback for the same Main execution instead of fatal job errors.

## Rev2.8.5 — Epistemic Memory

Separated retention from epistemic meaning with open Main-authored `nature`, `confidence`, `volatility`, `temporal`, and `context` metadata. Added historical node/relation revision semantics.

## Rev2.8.3–2.8.4 — Incremental Memory and Frontier recovery

Made Memory incremental on every cognition turn, kept artifacts as external Material provenance, restored Frontier as exact continuation rather than a reading limit, and stabilized the Memory wire contract.

## Earlier ECC line

The earlier Rev2.x line established the core rule that survives in Rev3: **Main owns semantic choice; Runtime owns physically enforceable truth and safety.** Raw conversation transcript projection and Objective-State-style semantic sidecars were removed in favor of intrinsic Memory and explicit recall.
