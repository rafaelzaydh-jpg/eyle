# Rev4.11.4 — Factual response quality

Rev4.11.4 improves analysis quality without restoring the old mission court, finding lifecycle, or a second evaluator agent.

## Real-read requirement

When a request asks about concrete project or code facts, the runtime requires real read evidence before accepting the final response. The LLM returns natural prose plus an internal claim ledger. Every `fact`, verified `bug`, and contextual `risk` must cite one or more active evidence IDs produced by `read_file`, `read_range`, `search_code`, or `find_symbol`.

Recommendations use their own type. This prevents a possible improvement from being presented as a confirmed defect.

## Bounded findings

Requests containing explicit limits such as `até 3`, `up to 5`, or `como máximo 2` produce deterministic claim caps. A single limit becomes the overall maximum. Multiple typed limits, such as `até 3 bugs e até 5 recomendações`, produce both an overall cap and per-kind caps. The model may return fewer findings when the evidence proves fewer, but the runtime rejects a response that exceeds any requested maximum.

## Stable relevant source

The newest tool result is still compact and replaceable. In addition, the session retains a bounded, deduplicated set of useful source snippets. A snippet already present in the newest tool result is not duplicated in the same prompt. A later `list_tree` or another tool result no longer erases the code needed for the conclusion. The default is four snippets with at most 8,000 characters each; prompt-budget cropping can reduce them further.

Raw source is removed from pending write-confirmation state because no later LLM call is needed after confirmation.

## Consistency checks

The quality gate rejects:

- project conclusions without real read evidence;
- `fact`, `bug`, or `risk` claims without evidence IDs;
- unknown evidence IDs;
- claims whose text is absent from the answer;
- duplicated or directly contradictory claim entries;
- lists that correct or retract themselves midway;
- answers exceeding an explicit finding limit.

The runtime stores a claim-to-evidence ledger in execution details. This ledger is diagnostic metadata and is not rendered as bureaucratic stages to the user.

## Boundary

This is structural validation, not full semantic theorem proving. The LLM still writes the answer and decides what the evidence means. The runtime proves that factual claim references exist, point to real reads, respect the requested count, and remain internally organized.

## Rev4.11.4.1 hotfix

Direct editing commands are now gated before final claim validation. When a write is requested and editing tools are available, the agent cannot finish with prose alone; it must read the affected source and produce a dry-run proposal for confirmation. This prevents unsupported completion claims from failing as `FINAL_CLAIM_REQUIRES_EVIDENCE` and, more importantly, prevents a requested write from being silently skipped.

## Rev4.11.4.2 hotfix

Failed confirmed writes now expose their real validation output instead of returning only a generic rollback sentence. The runtime preserves a structured `write_failure` record containing the failed stage, error code, affected paths, diagnostic text, and rollback result. On the next request this record becomes citable runtime-validation evidence, so questions such as `Qual foi o erro?` are answered from the failed attempt rather than inferred from files that have already been restored.

## Rev4.11.6 claim alignment

The compact Rev4.11.5 prompt accidentally shortened the claim contract to `text: exact sentence` without explicitly saying that the sentence must already appear in `answer`. Rev4.11.6 restores that instruction and adds a conservative runtime repair: close wording drift is mapped to one exact visible answer sentence. Alignment is rejected when polarity, numbers, file paths, code identifiers, or the material subject changes. The evidence ledger records the original model wording and the deterministic alignment metadata for auditability.

## Rev4.11.7 sentence references

The preferred final protocol no longer repeats each visible sentence inside `claims[].text`. Claims reference the 1-based non-heading sentence number instead. The runtime resolves that sentence into the private ledger, preserves legacy text claims for compatibility, and rejects invalid or out-of-range references. This keeps large-project conclusions compact without weakening deterministic evidence validation.
