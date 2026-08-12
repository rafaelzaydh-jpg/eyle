# Architectural direction

> **Eyle constrains effects, not thought.**

Main is free to reason. Runtime constrains only physically decidable effects; Claim challenges conclusions without becoming a planner.

Eyle should become a small universal agency kernel whose domain power comes from well-designed capabilities, not domain semantics welded into Core.

## Target law

> LLM cost should scale mainly with semantic complexity and materially observed information; external-state size should scale mainly in deterministic machine work.

## Universal Core candidates

The concepts currently justified as domain-neutral are:

- Main semantic authority;
- Runtime physical authority;
- Observation;
- Coverage;
- Frontier;
- Investigation;
- Claim;
- supervised mutation/transactions;
- physical containment and safety.

Everything else must justify Core membership.

## Capability boundary

A capability may know how to inspect or operate its domain. Core must not know that “security tasks need these files,” “home automation needs these devices,” or “code analysis should prefer these modules.” Main chooses semantic direction; capabilities expose physical contracts.

`ObjectiveScope` is the reference implementation style: it resolves literal files/directories/globs mechanically, reports exactly what was resolved/scanned, and never decides whether that scope is semantically relevant. Capability-specific observation identity, Material extraction, Coverage and bounded presentation should follow the same pattern and stay beside the capability rather than leaking into Agent/Observation.

## Frontier rule

A Frontier describes **available continuation**, not an instruction to continue. Runtime never says “this is relevant, expand me.” Main decides whether the unresolved reality matters.

Opaque cursors remain capability/Runtime implementation detail.

## No universal framework before need

Do not invent generic Planner, Router, Mutation Framework, semantic ranker or adapter hierarchy in anticipation of future domains. Add a universal abstraction only after at least two real domains demonstrate the same invariant.

## Removal rule

For every Core concept ask:

1. Who owns it?
2. Which decision changes because it exists?
3. Is that information already represented canonically elsewhere?
4. Is it domain-neutral?
5. What behavior disappears if it is deleted?

If the answer is only “it may be useful later,” it does not belong in Core.
