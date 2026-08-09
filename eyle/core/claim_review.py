"""Semantic verification over runtime-owned EvidenceRecords.

Semantic interpretation stays in LLM calls while every boundary that can be
proven deterministically stays in the runtime:
- omitted Claims config resolves to self_check; off is explicit;
- verifier citations are confined to the EvidenceRecords actually shown;
- file Evidence is checked for freshness before and after verification;
- reviewer debt is returned to the Main LLM through runtime-owned follow-up state;
- the runtime never rewrites a semantic conclusion on behalf of the Main LLM.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .request_policy import requested_finding_constraints
from .security import _resolver_caminho_seguro
from .text_hash import hash_texto, normalizar_quebras

CLAIM_MODES = {"off", "self_check", "verified"}
_CLAIMS_FIELDS = {"mode", "verifier", "evidence", "require_supported"}
_VERIFIER_COMMON_FIELDS = {"max_tokens", "temperature"}
_VERIFIER_VERIFIED_FIELDS = _VERIFIER_COMMON_FIELDS | {"base_url", "model", "openai_compatible"}
_EVIDENCE_FIELDS = {"max_chars_per_item"}

CLAIM_VERDICTS = {"supported", "contradicted", "insufficient"}
CLAIM_KINDS = {"fact", "bug", "risk", "recommendation"}
AUDIT_FINDING_TYPES = {"bug", "risk", "recommendation", "fact"}
SEMANTIC_GAP_TYPES = {"material_omission", "conflicting_evidence", "scope_gap"}


ANSWER_ANCHOR_MAX_CHARS = 700


def build_answer_anchors(answer: str, *, max_chars: int = ANSWER_ANCHOR_MAX_CHARS) -> List[Dict[str, Any]]:
    """Create deterministic literal anchors over the provisional answer.

    Anchors are transport coordinates, not semantic claims. The runtime splits
    only on observable text boundaries (line/sentence punctuation) and never
    decides which anchor is important. Every ``text`` value is an exact
    substring of ``answer``.
    """
    text = str(answer or "")
    cap = max(120, int(max_chars or ANSWER_ANCHOR_MAX_CHARS))
    anchors: List[Dict[str, Any]] = []
    cursor = 0
    length = len(text)
    boundary = re.compile(r"(?:[.!?;:](?=\s|$)|\n)")

    while cursor < length:
        while cursor < length and text[cursor].isspace():
            cursor += 1
        if cursor >= length:
            break

        hard_end = min(length, cursor + cap)
        match = boundary.search(text, cursor, hard_end)
        if match is not None:
            end = match.end()
        elif hard_end < length:
            # Prefer a whitespace cut before the hard cap when available.
            whitespace = max(text.rfind(" ", cursor, hard_end), text.rfind("\t", cursor, hard_end))
            end = whitespace if whitespace > cursor else hard_end
        else:
            end = length

        # Keep the anchor literal while excluding transport-only surrounding
        # whitespace. ``start:end`` remains a true substring of the answer.
        start = cursor
        while end > start and text[end - 1].isspace():
            end -= 1
        if end <= start:
            cursor = max(cursor + 1, hard_end)
            continue

        anchor_id = f"a{len(anchors) + 1}"
        anchors.append({
            "id": anchor_id,
            "text": text[start:end],
            "start": start,
            "end": end,
        })
        cursor = max(end, cursor + 1)

    return anchors


def answer_anchor_map(answer_anchors: Optional[Sequence[Dict[str, Any]]]) -> Dict[str, str]:
    """Return the deterministic ``answer_ref -> literal text`` map."""
    result: Dict[str, str] = {}
    for item in answer_anchors or []:
        if not isinstance(item, dict):
            continue
        anchor_id = str(item.get("id") or "").strip()
        text = str(item.get("text") or "")
        if anchor_id and anchor_id not in result:
            result[anchor_id] = text
    return result


def verifier_answer_anchors(
    answer: str, target_claims: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Build anchors for initial review or exact local Claim reverify.

    Reverify reuses the original Claim anchor reference and maps it to the
    target literal quote. This keeps semantic scope local without resending
    unrelated answer text.
    """
    if not target_claims:
        return True, "ok", build_answer_anchors(answer)

    anchors: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}
    for index, item in enumerate(target_claims, start=1):
        if not isinstance(item, dict):
            return False, f"CLAIM_REVERIFY_TARGET_INVALID:{index}", []
        anchor_id = str(item.get("answer_ref") or "").strip()
        quote = str(item.get("answer_quote") or "")
        if not anchor_id or not quote:
            return False, f"CLAIM_REVERIFY_ANCHOR_REQUIRED:{index}", []
        previous = seen.get(anchor_id)
        if previous is not None and previous != quote:
            return False, f"CLAIM_REVERIFY_ANCHOR_CONFLICT:{anchor_id}", []
        if previous is None:
            seen[anchor_id] = quote
            anchors.append({"id": anchor_id, "text": quote})
    return True, "ok", anchors


class ClaimConfigError(ValueError):
    """Invalid Claims configuration; never silently reinterpreted semantically."""


def _object_field(parent: Dict[str, Any], key: str) -> Dict[str, Any]:
    if key not in parent:
        return {}
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ClaimConfigError(f"agent.claims.{key} precisa ser um objeto")
    return value


def _bool_field(parent: Dict[str, Any], key: str, default: bool, prefix: str) -> bool:
    if key not in parent:
        return default
    value = parent.get(key)
    if not isinstance(value, bool):
        raise ClaimConfigError(f"{prefix}.{key} precisa ser booleano")
    return value


def _int_field(
    parent: Dict[str, Any], key: str, default: int, minimum: int, prefix: str,
) -> int:
    if key not in parent:
        return default
    value = parent.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        comparator = "não negativo" if minimum == 0 else f">= {minimum}"
        raise ClaimConfigError(f"{prefix}.{key} precisa ser inteiro {comparator}")
    return value


def _float_field(parent: Dict[str, Any], key: str, default: float, prefix: str) -> float:
    if key not in parent:
        return default
    value = parent.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ClaimConfigError(f"{prefix}.{key} precisa ser numérico")
    return float(value)


def claim_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve and validate the single active Claims configuration.

    Syntactic normalization is intentionally small and deterministic (for
    example ``SELF_CHECK`` -> ``self_check``). Invalid semantics are rejected
    instead of being silently converted to defaults.
    """
    agent = ((config or {}).get("agent") or {})
    if not isinstance(agent, dict):
        raise ClaimConfigError("agent precisa ser um objeto")
    if "claims" in agent and not isinstance(agent.get("claims"), dict):
        raise ClaimConfigError("agent.claims precisa ser um objeto")
    raw = dict(agent.get("claims") or {})
    unknown_claims = sorted(set(raw) - _CLAIMS_FIELDS)
    if unknown_claims:
        raise ClaimConfigError("UNKNOWN_CONFIG_FIELD:agent.claims:" + ",".join(unknown_claims))

    mode_raw = raw.get("mode", "self_check")
    if not isinstance(mode_raw, str):
        raise ClaimConfigError("agent.claims.mode precisa ser string")
    mode = mode_raw.strip().lower()
    if mode not in CLAIM_MODES:
        raise ClaimConfigError(f"agent.claims.mode inválido: {mode_raw}")

    verifier = _object_field(raw, "verifier")
    evidence = _object_field(raw, "evidence")
    unknown_evidence = sorted(set(evidence) - _EVIDENCE_FIELDS)
    if unknown_evidence:
        raise ClaimConfigError("UNKNOWN_CONFIG_FIELD:agent.claims.evidence:" + ",".join(unknown_evidence))
    transport_keys = {"base_url", "model", "openai_compatible"}
    present_transport = sorted(set(verifier) & transport_keys)
    allowed_verifier = _VERIFIER_VERIFIED_FIELDS if mode == "verified" else _VERIFIER_COMMON_FIELDS
    unknown_verifier = sorted(set(verifier) - allowed_verifier)
    if unknown_verifier:
        raise ClaimConfigError("UNKNOWN_CONFIG_FIELD:agent.claims.verifier:" + ",".join(unknown_verifier))
    if mode == "self_check" and present_transport:
        raise ClaimConfigError(
            "agent.claims.verifier em self_check herda diretamente a LLM principal; remova: "
            + ", ".join(present_transport)
        )
    verified_transport = {}
    if mode == "verified":
        missing = sorted(transport_keys - set(verifier))
        if missing:
            raise ClaimConfigError(
                "agent.claims.verifier em verified exige configuração explícita: " + ", ".join(missing)
            )
        base_url = verifier.get("base_url")
        model = verifier.get("model")
        openai_compatible = verifier.get("openai_compatible")
        for key, value in (("base_url", base_url), ("model", model)):
            if not isinstance(value, str) or not value.strip():
                raise ClaimConfigError(f"agent.claims.verifier.{key} precisa ser string não vazia")
        if not isinstance(openai_compatible, bool):
            raise ClaimConfigError("agent.claims.verifier.openai_compatible precisa ser booleano")
        verified_transport = {
            "base_url": base_url.strip(),
            "model": model.strip(),
            "openai_compatible": openai_compatible,
        }

    resolved = {
        "mode": mode,
        "verifier": {
            **verified_transport,
            "max_tokens": _int_field(verifier, "max_tokens", 900, 128, "agent.claims.verifier"),
            "temperature": _float_field(verifier, "temperature", 0.0, "agent.claims.verifier"),
        },
        "evidence": {
            "max_chars_per_item": _int_field(
                evidence, "max_chars_per_item", 2200, 200, "agent.claims.evidence"
            ),
        },
        "require_supported": _bool_field(raw, "require_supported", True, "agent.claims"),
    }

    if mode == "verified":
        llm = ((config or {}).get("llm") or {})
        if not isinstance(llm, dict):
            raise ClaimConfigError("llm precisa ser um objeto")
        main_base = str(llm.get("base_url") or "http://localhost:11434").rstrip("/")
        main_model = str(llm.get("model") or "").strip()
        verifier_base = str(verified_transport["base_url"]).rstrip("/")
        verifier_model = str(verified_transport["model"]).strip()
        if (verifier_base, verifier_model) == (main_base, main_model):
            raise ClaimConfigError("VERIFIED_REQUIRES_DISTINCT_VERIFIER")

    return resolved


def _excerpt(item: Dict[str, Any], max_chars: int) -> str:
    text = str(item.get("trecho_numerado") or item.get("conteudo") or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...[evidence excerpt cropped]"


def compact_evidence(
    evidence: Dict[str, Any], evidence_ids: Optional[Iterable[str]], *,
    max_chars_per_item: int,
) -> List[Dict[str, Any]]:
    """Build only the explicitly selected bounded verifier view.

    An empty selection stays empty. The canonical contract removes the old
    semantic fallback that exposed the last N runtime EvidenceRecords.
    """
    wanted = [str(item) for item in (evidence_ids or []) if str(item)]
    if not wanted:
        return []
    result: List[Dict[str, Any]] = []
    for evidence_id in wanted:
        item = evidence.get(evidence_id)
        if not isinstance(item, dict):
            continue
        entry = {
            "id": evidence_id,
            "file": item.get("arquivo"),
            "lines": [item.get("linha_inicio"), item.get("linha_fim")],
            "cropped": bool(
                item.get("truncado") or item.get("context_truncated")
                or len(str(item.get("trecho_numerado") or item.get("conteudo") or "")) > max_chars_per_item
            ),
            "excerpt": _excerpt(item, max_chars_per_item),
        }
        result.append({key: value for key, value in entry.items() if value not in (None, "", [], [None, None])})
    return result


def _ids(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    seen = set()
    result = []
    for item in value:
        value_str = str(item or "").strip()
        if value_str and value_str not in seen:
            seen.add(value_str)
            result.append(value_str)
    return result


def validate_file_evidence_freshness(
    evidence: Dict[str, Any], evidence_ids: Iterable[str], project_root: Any,
) -> Tuple[bool, str]:
    """Recheck live file hashes for the EvidenceRecords selected for review."""
    if not project_root:
        return True, "ok"
    root = os.path.realpath(os.fspath(project_root))
    for evidence_id in _ids(list(evidence_ids or [])):
        item = evidence.get(evidence_id)
        if not isinstance(item, dict):
            continue
        relative = str(item.get("arquivo") or "")
        expected = str(item.get("file_hash") or "")
        if not relative or not expected or relative.startswith("<"):
            continue
        absolute = _resolver_caminho_seguro(root, relative)
        if absolute is None or not os.path.isfile(absolute):
            return False, f"EVIDENCE_STALE:{evidence_id}"
        try:
            with open(absolute, "r", encoding="utf-8", errors="replace") as handle:
                current = hash_texto(normalizar_quebras(handle.read()))
        except OSError:
            return False, f"EVIDENCE_STALE:{evidence_id}"
        if current != expected:
            return False, f"EVIDENCE_STALE:{evidence_id}"
    return True, "ok"


def _finding_signature(finding_type: str, evidence_ids: Sequence[str], reason: str, target_id: Any = None) -> str:
    """Build an observational semantic-gap signature without interpreting it."""
    normalized_reason = re.sub(r"\s+", " ", str(reason or "")).strip().lower()
    payload = json.dumps({
        "type": str(finding_type or ""),
        "evidence_ids": sorted(_ids(list(evidence_ids or []))),
        "reason": normalized_reason,
        "target_id": None if target_id is None else str(target_id),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hash_texto(normalizar_quebras(payload))


def semantic_gap_findings(review: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        item for item in (review or {}).get("semantic_gaps") or []
        if isinstance(item, dict) and str(item.get("type") or "") in SEMANTIC_GAP_TYPES
    ]


def _review_summary(
    claims: Sequence[Dict[str, Any]],
    semantic_gaps: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, int]:
    semantic = [
        item for item in (semantic_gaps or [])
        if isinstance(item, dict) and str(item.get("type") or "") in SEMANTIC_GAP_TYPES
    ]
    summary = {
        "supported": sum(1 for claim in claims if claim.get("verdict") == "supported"),
        "contradicted": sum(1 for claim in claims if claim.get("verdict") == "contradicted"),
        "insufficient": sum(1 for claim in claims if claim.get("verdict") == "insufficient"),
    }
    if semantic:
        summary.update({
            "semantic_gaps": len(semantic),
            "material_omission": sum(1 for item in semantic if item.get("type") == "material_omission"),
            "conflicting_evidence": sum(1 for item in semantic if item.get("type") == "conflicting_evidence"),
            "scope_gap": sum(1 for item in semantic if item.get("type") == "scope_gap"),
        })
    return summary


def normalize_claim_review(
    raw: Dict[str, Any],
    evidence: Dict[str, Any],
    *,
    request: Any = "",
    answer: Optional[str] = None,
    answer_anchors: Optional[Sequence[Dict[str, Any]]] = None,
    visible_evidence_ids: Optional[Iterable[str]] = None,
    expected_claim_ids: Optional[Iterable[str]] = None,
    investigation: Optional[Sequence[Dict[str, Any]]] = None,
    enforce_finding_coverage: bool = True,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Apply deterministic authority checks after strict structured parsing.

    JSON shape, required fields, enums and primitive types belong exclusively
    to ``llm.structured``. This function validates relationships the schema
    cannot know: anchors, Evidence authority/visibility, identity uniqueness,
    requested Finding limits and verdict/Evidence consistency.
    """
    visible = set(_ids(list(visible_evidence_ids or []))) if visible_evidence_ids is not None else None
    expected = set(_ids(list(expected_claim_ids or []))) if expected_claim_ids is not None else None
    investigation_ids = {str(item.get("id") or "") for item in (investigation or []) if isinstance(item, dict) and str(item.get("id") or "")}
    answer_text = None if answer is None else str(answer)
    resolved_anchors = answer_anchors
    if resolved_anchors is None and answer_text is not None:
        resolved_anchors = build_answer_anchors(answer_text)
    anchors = answer_anchor_map(resolved_anchors)

    claims: List[Dict[str, Any]] = []
    claim_ids: set[str] = set()
    for index, item in enumerate(raw["claims"], start=1):
        claim_id = item["id"].strip()
        answer_ref = item["answer_ref"].strip()
        target_id = item.get("target_id")
        if target_id is not None:
            target_id = str(target_id).strip()
        statement = item["statement"].strip()
        kind = item["kind"]
        verdict = item["verdict"]
        evidence_ids = _ids(item["evidence_ids"])
        reason = item["reason"].strip()[:160]

        if claim_id in claim_ids:
            return False, f"CLAIM_REVIEW_ID_DUPLICATE:{index}", {}
        if expected is not None and claim_id not in expected:
            return False, f"CLAIM_REVIEW_UNEXPECTED_CLAIM:{claim_id}", {}
        if answer_ref not in anchors:
            return False, f"CLAIM_REVIEW_ANSWER_REF_INVALID:{index}:{answer_ref}", {}
        answer_quote = anchors[answer_ref]
        if target_id is not None and target_id not in investigation_ids:
            return False, f"CLAIM_REVIEW_UNKNOWN_TARGET:{index}:{target_id}", {}

        missing = [evidence_id for evidence_id in evidence_ids if evidence_id not in evidence]
        if missing:
            return False, "CLAIM_REVIEW_UNKNOWN_EVIDENCE:" + ",".join(missing), {}
        if visible is not None:
            invisible = [evidence_id for evidence_id in evidence_ids if evidence_id not in visible]
            if invisible:
                return False, "CLAIM_REVIEW_EVIDENCE_NOT_VISIBLE:" + ",".join(invisible), {}
        if verdict == "supported" and kind in {"fact", "bug", "risk"} and not evidence_ids:
            return False, f"CLAIM_REVIEW_SUPPORTED_REQUIRES_EVIDENCE:{index}", {}
        if verdict == "contradicted" and kind in {"fact", "bug", "risk"} and not evidence_ids:
            return False, f"CLAIM_REVIEW_CONTRADICTED_REQUIRES_EVIDENCE:{index}", {}

        claim_ids.add(claim_id)
        claims.append({
            "id": claim_id,
            "answer_ref": answer_ref,
            "answer_quote": answer_quote,
            "target_id": target_id,
            "statement": statement,
            "kind": kind,
            "evidence_ids": evidence_ids,
            "verdict": verdict,
            "reason": reason,
        })

    if expected is not None and claim_ids != expected:
        missing_claims = sorted(expected - claim_ids)
        return False, "CLAIM_REVIEW_EXPECTED_CLAIMS_MISSING:" + ",".join(missing_claims), {}

    findings: List[Dict[str, Any]] = []
    finding_ids: set[str] = set()
    for index, item in enumerate(raw["findings"], start=1):
        finding_id = item["id"].strip()
        finding_type = item["type"]
        refs = _ids(item["claim_ids"])
        if finding_id in finding_ids:
            return False, f"CLAIM_REVIEW_FINDING_ID_DUPLICATE:{index}", {}
        unknown = [claim_id for claim_id in refs if claim_id not in claim_ids]
        if unknown:
            return False, "CLAIM_REVIEW_UNKNOWN_CLAIM:" + ",".join(unknown), {}
        findings.append({"id": finding_id, "type": finding_type, "claim_ids": refs})
        finding_ids.add(finding_id)

    semantic_gaps: List[Dict[str, Any]] = []
    semantic_ids: set[str] = set()
    for index, item in enumerate(raw["semantic_gaps"], start=1):
        gap_id = item["id"].strip()
        gap_type = item["type"]
        gap_target_id = item.get("target_id")
        if gap_target_id is not None:
            gap_target_id = str(gap_target_id).strip()
        gap_evidence_ids = _ids(item["evidence_ids"])
        gap_reason = item["reason"].strip()[:240]
        if gap_id in semantic_ids:
            return False, f"CLAIM_REVIEW_SEMANTIC_GAP_ID_DUPLICATE:{index}", {}
        if gap_target_id is not None and gap_target_id not in investigation_ids:
            return False, f"CLAIM_REVIEW_UNKNOWN_TARGET:{index}:{gap_target_id}", {}
        missing = [evidence_id for evidence_id in gap_evidence_ids if evidence_id not in evidence]
        if missing:
            return False, "CLAIM_REVIEW_UNKNOWN_EVIDENCE:" + ",".join(missing), {}
        if visible is not None:
            invisible = [evidence_id for evidence_id in gap_evidence_ids if evidence_id not in visible]
            if invisible:
                return False, "CLAIM_REVIEW_EVIDENCE_NOT_VISIBLE:" + ",".join(invisible), {}
        if gap_type in {"material_omission", "conflicting_evidence"} and not gap_evidence_ids:
            return False, f"CLAIM_REVIEW_SEMANTIC_GAP_EVIDENCE_REQUIRED:{index}:{gap_type}", {}
        semantic_gaps.append({
            "id": gap_id,
            "type": gap_type,
            "target_id": gap_target_id,
            "evidence_ids": gap_evidence_ids,
            "reason": gap_reason,
            "signature": _finding_signature(gap_type, gap_evidence_ids, gap_reason, gap_target_id),
        })
        semantic_ids.add(gap_id)

    constraints = requested_finding_constraints(request)
    structured_claims = [claim for claim in claims if claim.get("kind") in {"bug", "risk", "recommendation"}]
    findings_mode = bool(findings) or bool(constraints.get("overall") is not None or constraints.get("by_kind"))
    if enforce_finding_coverage and findings_mode and structured_claims:
        coverage: Dict[str, set[str]] = {}
        for finding in findings:
            for claim_id in finding.get("claim_ids") or []:
                coverage.setdefault(claim_id, set()).add(str(finding.get("type") or ""))
        for claim in structured_claims:
            claim_id = str(claim.get("id"))
            kind = str(claim.get("kind"))
            if kind not in coverage.get(claim_id, set()):
                return False, f"CLAIM_REVIEW_FINDING_COVERAGE_REQUIRED:{claim_id}:{kind}", {}

    overall = constraints.get("overall")
    if overall is not None and len(findings) > int(overall):
        return False, f"CLAIM_REVIEW_FINDING_LIMIT_EXCEEDED:{len(findings)}>{overall}", {}
    by_kind = constraints.get("by_kind") or {}
    for kind, limit in by_kind.items():
        count = sum(1 for finding in findings if finding.get("type") == kind)
        if count > int(limit):
            return False, f"CLAIM_REVIEW_KIND_LIMIT_EXCEEDED:{kind}:{count}>{limit}", {}

    return True, "ok", {
        "claims": claims,
        "findings": findings,
        "semantic_gaps": semantic_gaps,
        "summary": _review_summary(claims, semantic_gaps),
    }


def problematic_claims(review: Dict[str, Any], verdict: Optional[str] = None) -> List[Dict[str, Any]]:
    claims = [item for item in (review or {}).get("claims") or [] if isinstance(item, dict)]
    if verdict:
        return [item for item in claims if item.get("verdict") == verdict]
    return [item for item in claims if item.get("verdict") in {"contradicted", "insufficient"}]


def claim_evidence_ledger(review: Dict[str, Any], evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    ledger = []
    for claim in (review or {}).get("claims") or []:
        if not isinstance(claim, dict):
            continue
        sources = []
        for evidence_id in claim.get("evidence_ids") or []:
            item = evidence.get(evidence_id) or {}
            sources.append({
                "evidence_id": evidence_id,
                "file": item.get("arquivo"),
                "lines": [item.get("linha_inicio"), item.get("linha_fim")],
                "source_type": item.get("source_type"),
            })
        ledger.append({
            "id": claim.get("id"),
            "kind": claim.get("kind"),
            "answer_ref": claim.get("answer_ref"),
            "target_id": claim.get("target_id"),
            "answer_quote": claim.get("answer_quote"),
            "statement": claim.get("statement"),
            "verdict": claim.get("verdict"),
            "reason": claim.get("reason"),
            "sources": sources,
        })
    return ledger


def claim_review_output_budget(
    answer: str,
    *,
    base_tokens: int = 900,
    available_tokens: Optional[int] = None,
    target_claims: Optional[Sequence[Dict[str, Any]]] = None,
    target_semantic_gaps: Optional[Sequence[Dict[str, Any]]] = None,
    answer_anchor_count: Optional[int] = None,
) -> int:
    """Choose an elastic verifier completion ceiling.

    Claim count has no semantic quota. The runtime estimates only physical
    capacity from the answer/anchors (or the explicit local reverify targets)
    and clips that estimate solely to the job's remaining completion budget.
    Unused ceiling is never charged.
    """
    base = max(128, int(base_tokens or 900))
    if target_claims or target_semantic_gaps:
        estimated_material_claims = max(1, len(list(target_claims or target_semantic_gaps or [])))
        desired = 620 + (estimated_material_claims * 220)
    else:
        answer_chars = len(str(answer or ""))
        by_text = max(1, (answer_chars + 159) // 160)
        by_anchor = max(1, int(answer_anchor_count or 0)) if answer_anchor_count else 1
        estimated_material_claims = max(by_text, by_anchor)
        desired = 720 + (estimated_material_claims * 180)
    desired = max(base, int(desired))
    if available_tokens is None:
        return desired
    physical = max(1, int(available_tokens))
    return min(desired, physical)


_RECOVERABLE_CLAIM_PROTOCOL_PREFIXES = (
    "CLAIM_REVIEW_SUPPORTED_REQUIRES_EVIDENCE:",
    "CLAIM_REVIEW_CONTRADICTED_REQUIRES_EVIDENCE:",
)
_RECOVERABLE_SEMANTIC_GAP_PROTOCOL_PREFIXES = (
    "CLAIM_REVIEW_SEMANTIC_GAP_EVIDENCE_REQUIRED:",
)


def claim_protocol_recovery_target(
    raw: Any, reason: str, answer_anchors: Optional[Sequence[Dict[str, Any]]],
) -> Tuple[bool, str, Dict[str, Any], Optional[int]]:
    """Build one safe local reverify target for a malformed verifier Claim.

    Recovery is allowed only when Claim identity and answer_ref already provide
    an exact deterministic scope. The runtime never invents a verdict, kind or
    Evidence selection; the verifier must decide those again locally.
    """
    text_reason = str(reason or "")
    if not text_reason.startswith(_RECOVERABLE_CLAIM_PROTOCOL_PREFIXES):
        return False, "CLAIM_PROTOCOL_RECOVERY_NOT_APPLICABLE", {}, None
    match = re.search(r":(\d+)(?::|$)", text_reason)
    if not match:
        return False, "CLAIM_PROTOCOL_RECOVERY_INDEX_REQUIRED", {}, None
    index = int(match.group(1))
    claims_raw = raw["claims"]
    if index < 1 or index > len(claims_raw):
        return False, "CLAIM_PROTOCOL_RECOVERY_TARGET_MISSING", {}, None
    item = claims_raw[index - 1]
    claim_id = item["id"].strip()
    answer_ref = item["answer_ref"].strip()
    anchors = answer_anchor_map(answer_anchors)
    answer_quote = anchors.get(answer_ref, "")
    if not claim_id or not answer_ref or not answer_quote:
        return False, "CLAIM_PROTOCOL_RECOVERY_SCOPE_UNAVAILABLE", {}, None
    target = {
        "claim_id": claim_id,
        "answer_ref": answer_ref,
        "answer_quote": answer_quote,
        "target_id": item.get("target_id"),
        "kind": item["kind"],
        "evidence_ids": [],
    }
    return True, "ok", target, index


def semantic_gap_protocol_recovery_target(
    raw: Any, reason: str,
) -> Tuple[bool, str, Dict[str, Any], Optional[int]]:
    """Build one exact local re-evaluation target for a malformed Semantic Gap.

    The runtime identifies only the malformed array element. It never changes
    the gap type, chooses Evidence, or decides whether the gap should exist.
    Those semantic decisions remain with the verifier.
    """
    text_reason = str(reason or "")
    if not text_reason.startswith(_RECOVERABLE_SEMANTIC_GAP_PROTOCOL_PREFIXES):
        return False, "SEMANTIC_GAP_PROTOCOL_RECOVERY_NOT_APPLICABLE", {}, None
    match = re.search(r":(\d+)(?::|$)", text_reason)
    if not match:
        return False, "SEMANTIC_GAP_PROTOCOL_RECOVERY_INDEX_REQUIRED", {}, None
    index = int(match.group(1))
    gaps_raw = raw["semantic_gaps"]
    if index < 1 or index > len(gaps_raw):
        return False, "SEMANTIC_GAP_PROTOCOL_RECOVERY_TARGET_MISSING", {}, None
    item = gaps_raw[index - 1]
    gap_id = item["id"].strip()
    if not gap_id:
        return False, "SEMANTIC_GAP_PROTOCOL_RECOVERY_SCOPE_UNAVAILABLE", {}, None
    return True, "ok", {
        "id": gap_id,
        "type": item["type"],
        "target_id": item.get("target_id"),
        "evidence_ids": list(item["evidence_ids"]),
        "reason": item["reason"],
    }, index


def finding_protocol_recovery_target(
    raw: Any, reason: str,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Identify the Claim whose Finding coverage is inconsistent.

    The runtime only isolates the deterministic relation that failed. It never
    creates or retypes a Finding; the verifier must regenerate Findings.
    """
    text_reason = str(reason or "")
    prefix = "CLAIM_REVIEW_FINDING_COVERAGE_REQUIRED:"
    if not text_reason.startswith(prefix):
        return False, "FINDING_PROTOCOL_RECOVERY_NOT_APPLICABLE", {}
    parts = text_reason[len(prefix):].split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False, "FINDING_PROTOCOL_RECOVERY_TARGET_REQUIRED", {}
    claim_id, kind = parts[0], parts[1]
    claims = [item for item in raw.get("claims") or [] if isinstance(item, dict)] if isinstance(raw, dict) else []
    claim = next((item for item in claims if str(item.get("id") or "") == claim_id), None)
    if not claim or str(claim.get("kind") or "") != kind:
        return False, "FINDING_PROTOCOL_RECOVERY_SCOPE_UNAVAILABLE", {}
    return True, "ok", {
        "claim_id": claim_id,
        "kind": kind,
        "claim": dict(claim),
    }


def finding_recovery_prompt(request: Any, raw_review: Dict[str, Any], target: Dict[str, Any]) -> str:
    """Ask the verifier to regenerate only the complete Findings array."""
    claims = []
    for item in raw_review.get("claims") or []:
        if not isinstance(item, dict):
            continue
        claims.append({
            "id": item.get("id"),
            "kind": item.get("kind"),
            "statement": item.get("statement"),
            "verdict": item.get("verdict"),
        })
    payload = {
        "task": "reverify_findings",
        "request": str(request or ""),
        "target_claim_id": target.get("claim_id"),
        "target_kind": target.get("kind"),
        "preserved_claims": claims,
        "existing_findings": [
            dict(item) for item in raw_review.get("findings") or [] if isinstance(item, dict)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def review_prompt(
    answer: str,
    evidence_view: List[Dict[str, Any]],
    request: Any,
    *,
    target_claims: Optional[List[Dict[str, Any]]] = None,
    target_semantic_gaps: Optional[List[Dict[str, Any]]] = None,
    answer_anchors: Optional[List[Dict[str, Any]]] = None,
    investigation: Optional[List[Dict[str, Any]]] = None,
    workspace_scope: Optional[Dict[str, Any]] = None,
    scope_only: bool = False,
) -> str:
    """Build the minimum semantic-review packet with deterministic anchors.

    The verifier receives one literal representation of the answer: anchors.
    The full answer is not duplicated beside them. The LLM chooses ``answer_ref``
    semantically; the runtime resolves the exact quote after the call.
    """
    if answer_anchors is not None:
        anchors = answer_anchors
    else:
        anchors_ok, _anchors_reason, anchors = verifier_answer_anchors(answer, target_claims)
        if not anchors_ok:
            anchors = []
    public_anchors = [
        {"id": str(item.get("id") or ""), "text": str(item.get("text") or "")}
        for item in anchors
        if isinstance(item, dict) and item.get("id")
    ]
    if scope_only:
        task = "verify_workspace_scope"
    elif target_semantic_gaps:
        task = "reverify_semantic_gap"
    elif target_claims:
        task = "reverify_claims"
    else:
        task = "verify_claims"
    payload: Dict[str, Any] = {
        "task": task,
        "answer_anchors": public_anchors,
        "evidence": evidence_view,
        "investigation": [dict(item) for item in (investigation or []) if isinstance(item, dict)],
        "workspace_scope": dict(workspace_scope or {}),
    }
    if scope_only:
        payload["request"] = str(request or "")
        payload["instructions"] = (
            "Audit only whether workspace_scope=none is semantically valid for this request and answer. "
            "Return claims=[] and findings=[]. If current workspace facts are materially required, emit exactly one "
            "scope_gap with target_id=null and evidence_ids=[]; otherwise semantic_gaps=[]. Do not fact-check "
            "workspace-independent content in this task."
        )
    elif target_claims:
        payload["target_claims"] = [
            {
                "claim_id": item.get("claim_id"),
                "answer_ref": item.get("answer_ref"),
                "target_id": item.get("target_id"),
                "kind": item.get("kind"),
                "evidence_ids": list(item.get("evidence_ids") or []),
            }
            for item in target_claims
            if isinstance(item, dict)
        ]
    elif target_semantic_gaps:
        payload["request"] = str(request or "")
        payload["target_semantic_gaps"] = [
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "target_id": item.get("target_id"),
                "evidence_ids": list(item.get("evidence_ids") or []),
                "reason": item.get("reason", ""),
            }
            for item in target_semantic_gaps
            if isinstance(item, dict)
        ]
    else:
        # The request is needed only on the initial pass to decide materiality,
        # bounded Findings and semantic gaps in the conclusion as a whole.
        payload["request"] = str(request or "")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

def review_followup_feedback(review: Dict[str, Any]) -> str:
    """Serialize reviewer debt without adding runtime semantics.

    The Claim Verifier already decided which material statements are
    contradicted/insufficient and which semantic gaps exist. The runtime only
    preserves those coordinates (Claim id, answer_ref, target_id, Evidence ids
    and reason) so the Main LLM can choose the correction. Evidence bodies stay
    runtime-owned and are rehydrated separately when pinned.
    """
    claims = []
    for claim in problematic_claims(review):
        verdict = str(claim.get("verdict") or "")
        if verdict not in {"contradicted", "insufficient"}:
            continue
        claims.append({
            "id": claim.get("id"),
            "answer_ref": claim.get("answer_ref"),
            "target_id": claim.get("target_id"),
            "statement": claim.get("statement"),
            "verdict": verdict,
            "evidence_ids": list(claim.get("evidence_ids") or []),
            "reason": claim.get("reason", ""),
        })
    gaps = []
    for gap in semantic_gap_findings(review):
        gaps.append({
            "id": gap.get("id"),
            "type": gap.get("type"),
            "target_id": gap.get("target_id"),
            "evidence_ids": list(gap.get("evidence_ids") or []),
            "reason": gap.get("reason", ""),
            "signature": gap.get("signature"),
        })
    payload = {
        "code": "CLAIM_REVIEW_FOLLOWUP",
        "claims": claims,
        "semantic_gaps": gaps,
        "instruction": (
            "The semantic reviewer found material debt. You remain the only producer of task semantics. "
            "You decide the next action: reinterpret existing Evidence, investigate with available tools, "
            "narrow/remove an unsupported or contradicted statement, correct workspace work when required, "
            "cover the reported omission/conflict/scope gap, or explicitly state a limitation. "
            "The reviewer does not choose tools or rewrite your answer."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

