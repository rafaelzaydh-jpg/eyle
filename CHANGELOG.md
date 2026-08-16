# Changelog

This file summarizes the public architectural evolution of Eyle. Detailed construction audits from the pre-Rev3 development cycle were intentionally removed from the public tree; Git history remains the detailed historical record.

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
