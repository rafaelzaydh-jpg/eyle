# Verification Direction — Eyle 2.7.5 Rev1.4.8

Rev1.4 removes the universal Claim reviewer. Reliability is split into two layers with different authority.

## Active now: Grounded Completion

Main owns semantic commitments. Runtime enforces their physical closure.

```text
Task/Investigation declared by Main
        ↓
Capabilities → Observation / Material
        ↓
Main closes commitments
        ↓
Runtime verifies structural closure + committed Material continuity
        ↓
Complete
```

Runtime can decide:

- whether a Task/Investigation is still open;
- whether a referenced `mat-*` exists;
- whether an established Investigation contains a non-empty Main-authored `conclusion`;
- whether a closed Task contains a non-empty `result`;
- whether a completed parent retains an open child;
- whether committed Material IDs appear in Complete grounding;
- whether schemas/status transitions are valid.

Runtime cannot decide:

- whether the conclusion is intellectually correct;
- whether the selected evidence is sufficient in the world;
- whether another source would have been better;
- whether a domain operation is safe without domain criteria.

## Future: capability-owned validators

Some capabilities can define objective or explicit rubric-based validation.

Examples:

```text
code        → compile, tests, lint, type checks
database    → dry-run, integrity, rollback, permissions
network     → reachability, config invariants
security    → explicit policy/rubric, possibly specialist fresh LLM
```

The intended ownership rule is:

> **The capability owns validation criteria; Runtime owns execution of the physical contract; Main owns what the result means for the task.**

Preferred flow:

```text
Capability action
      ↓
optional capability-owned validator
      ↓
physical validator result
      ↓
normal Observation
      ↓
Main
```

The validator must not become a planner or a universal semantic gate. Core should not contain domain-specific validator names/checklists.

## Not implemented in Rev1.4

Rev1.4 deliberately does not add:

- validator registry;
- automatic risk classifier;
- mandatory safety LLM;
- universal security checklist;
- confidence score;
- second evidence ledger;
- replacement Claim under another name.

Those enter only when a concrete capability and measurable tests justify them.
