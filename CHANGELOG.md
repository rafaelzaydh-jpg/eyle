# Changelog

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

This file summarizes the public architectural evolution of Eyle. Detailed construction audits from the pre-Rev3 development cycle were intentionally removed from the public tree; Git history remains the detailed historical record.

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
