"""Semantic verification over runtime-owned EvidenceRecords.

Semantic interpretation stays in LLM calls while every boundary that can be
proven deterministically stays in the runtime:
- omitted Claims config resolves to self_check; off is explicit;
- verifier citations are confined to the EvidenceRecords actually shown;
- file Evidence is checked for freshness before and after verification;
- reviewer debt is preserved in the canonical Claim Review and rendered back to the Main LLM;
- the runtime never rewrites a semantic conclusion on behalf of the Main LLM.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .security import _resolver_caminho_seguro
from .text_hash import hash_texto, normalizar_quebras
from .execution_context import current_execution

CLAIM_MODES = {"off", "self_check", "verified"}
_CLAIMS_FIELDS = {"mode", "verifier", "evidence"}
_VERIFIER_COMMON_FIELDS = {"max_tokens", "temperature"}
_VERIFIER_VERIFIED_FIELDS = _VERIFIER_COMMON_FIELDS | {"base_url", "model", "openai_compatible"}
_EVIDENCE_FIELDS = {"max_chars_per_item"}

SEMANTIC_GAP_TYPES = {"material_omission", "conflicting_evidence", "scope_gap"}
GROUNDING_PREFIXES = {"evidence", "runtime", "answer", "investigation"}


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


class ClaimConfigError(ValueError):
    """Invalid Claims configuration; never silently reinterpreted semantically."""


def _object_field(parent: Dict[str, Any], key: str) -> Dict[str, Any]:
    if key not in parent:
        return {}
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ClaimConfigError(f"agent.claims.{key} precisa ser um objeto")
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
    text = str(item.get("numbered_content") or item.get("content") or "")
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
            "file": item.get("file"),
            "lines": [item.get("line_start"), item.get("line_end")],
            "cropped": bool(
                item.get("truncated") or item.get("context_truncated")
                or len(str(item.get("numbered_content") or item.get("content") or "")) > max_chars_per_item
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


def _grounding_refs(value: Any) -> List[str]:
    """Normalize ordered verifier grounding coordinates without interpreting them."""
    return _ids(value)


def evidence_ids_from_grounding(refs: Iterable[str]) -> List[str]:
    out: List[str] = []
    for ref in refs or []:
        text = str(ref or "").strip()
        if text.startswith("evidence:"):
            evidence_id = text.split(":", 1)[1].strip()
            if evidence_id and evidence_id not in out:
                out.append(evidence_id)
    return out


def _bounded_runtime_result(value: Any, max_chars: int = 700) -> Any:
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        raw = str(value)
    if len(raw) <= max_chars:
        return value
    return {"truncated": True, "preview": raw[:max_chars]}


def compact_runtime_facts(observation_ledger: Dict[str, Any], *, max_items: int = 64) -> List[Dict[str, Any]]:
    """Expose physical outcomes from the current job as bounded Claim coordinates.

    This is objective projection only: no event is selected for semantic relevance.
    """
    events = list((observation_ledger or {}).get("events") or [])
    execution = current_execution()
    start = int(execution.observation_event_start or 0) if execution is not None else 0
    selected = events[start:start + max(1, int(max_items))]
    result: List[Dict[str, Any]] = []
    for index, event in enumerate(selected, start=1):
        if not isinstance(event, dict):
            continue
        item = {
            "id": f"r{index}",
            "event_id": event.get("event_id"),
            "turn": event.get("turn"),
            "tool": event.get("tool"),
            "status": event.get("status"),
            "executed": bool(event.get("executed")),
            "ok": bool(event.get("ok")),
            "error_code": event.get("error_code"),
            "result": _bounded_runtime_result(event.get("result") or {}),
        }
        result.append({k: v for k, v in item.items() if v is not None})
    return result


def _validate_grounding_refs(
    refs: Iterable[str], *, visible_evidence_ids: set[str], runtime_fact_ids: set[str],
    answer_ids: set[str], investigation_ids: set[str],
) -> Tuple[bool, str]:
    """Validate coordinates only; never decide whether a coordinate is sufficient."""
    for raw in refs or []:
        ref = str(raw or "").strip()
        if ref == "request":
            continue
        if ":" not in ref:
            return False, f"CLAIM_REVIEW_GROUNDING_REF_INVALID:{ref}"
        prefix, value = ref.split(":", 1)
        if prefix not in GROUNDING_PREFIXES or not value:
            return False, f"CLAIM_REVIEW_GROUNDING_REF_INVALID:{ref}"
        if prefix == "evidence" and value not in visible_evidence_ids:
            return False, f"CLAIM_REVIEW_GROUNDING_EVIDENCE_NOT_VISIBLE:{value}"
        if prefix == "runtime" and value not in runtime_fact_ids:
            return False, f"CLAIM_REVIEW_GROUNDING_RUNTIME_UNKNOWN:{value}"
        if prefix == "answer" and value not in answer_ids:
            return False, f"CLAIM_REVIEW_GROUNDING_ANSWER_UNKNOWN:{value}"
        if prefix == "investigation" and value not in investigation_ids:
            return False, f"CLAIM_REVIEW_GROUNDING_INVESTIGATION_UNKNOWN:{value}"
    return True, "ok"


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
        relative = str(item.get("file") or "")
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


def problematic_semantic_gaps(review: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        item for item in (review or {}).get("semantic_gaps") or []
        if isinstance(item, dict) and str(item.get("type") or "") in SEMANTIC_GAP_TYPES
    ]


def _review_summary(
    claims: Sequence[Dict[str, Any]],
    semantic_gaps: Optional[Sequence[Dict[str, Any]]] = None,
    material_satisfaction: Optional[Dict[str, Any]] = None,
    answer_consistency: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    semantic = [
        item for item in (semantic_gaps or [])
        if isinstance(item, dict) and str(item.get("type") or "") in SEMANTIC_GAP_TYPES
    ]
    summary = {
        "supported": sum(1 for claim in claims if claim.get("verdict") == "supported"),
        "contradicted": sum(1 for claim in claims if claim.get("verdict") == "contradicted"),
        "insufficient": sum(1 for claim in claims if claim.get("verdict") == "insufficient"),
        "material_satisfaction_gap": 1 if str((material_satisfaction or {}).get("status") or "") == "gap" else 0,
        "material_satisfaction_blocked": 1 if str((material_satisfaction or {}).get("status") or "") == "blocked" else 0,
        "answer_consistency_conflict": 1 if str((answer_consistency or {}).get("status") or "") == "conflict" else 0,
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
    answer: Optional[str] = None,
    answer_anchors: Optional[Sequence[Dict[str, Any]]] = None,
    visible_evidence_ids: Optional[Iterable[str]] = None,
    investigation: Optional[Sequence[Dict[str, Any]]] = None,
    runtime_facts: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Validate deterministic Claim coordinates after strict structured parsing."""
    visible_evidence = set(_ids(list(visible_evidence_ids or []))) if visible_evidence_ids is not None else set(evidence)
    investigation_ids = {
        str(item.get("id") or "") for item in (investigation or [])
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    answer_text = None if answer is None else str(answer)
    resolved_anchors = answer_anchors
    if resolved_anchors is None and answer_text is not None:
        resolved_anchors = build_answer_anchors(answer_text)
    anchors = answer_anchor_map(resolved_anchors)
    answer_ids = set(anchors)
    runtime_ids = {
        str(item.get("id") or "") for item in (runtime_facts or [])
        if isinstance(item, dict) and str(item.get("id") or "")
    }

    def normalize_grounding(value: Any, *, label: str) -> Tuple[Optional[List[str]], Optional[str]]:
        refs = _grounding_refs(value)
        ok, reason = _validate_grounding_refs(
            refs, visible_evidence_ids=visible_evidence, runtime_fact_ids=runtime_ids,
            answer_ids=answer_ids, investigation_ids=investigation_ids,
        )
        if not ok:
            return None, f"{reason}:{label}"
        return refs, None

    satisfaction_raw = raw.get("material_satisfaction") or {}
    satisfaction_refs, error = normalize_grounding(satisfaction_raw.get("grounding_refs"), label="material_satisfaction")
    if error:
        return False, error, {}
    material_satisfaction = {
        "status": str(satisfaction_raw.get("status") or "").strip(),
        "grounding_refs": satisfaction_refs or [],
        "reason": str(satisfaction_raw.get("reason") or "").strip()[:240],
    }
    consistency_raw = raw.get("answer_consistency") or {}
    consistency_refs, error = normalize_grounding(consistency_raw.get("grounding_refs"), label="answer_consistency")
    if error:
        return False, error, {}
    answer_consistency = {
        "status": str(consistency_raw.get("status") or "").strip(),
        "grounding_refs": consistency_refs or [],
        "reason": str(consistency_raw.get("reason") or "").strip()[:240],
    }

    claims: List[Dict[str, Any]] = []
    for index, item in enumerate(raw["claims"], start=1):
        answer_ref = item["answer_ref"].strip()
        target_id = item.get("target_id")
        if target_id is not None:
            target_id = str(target_id).strip()
        statement = item["statement"].strip()
        verdict = item["verdict"]
        refs, error = normalize_grounding(item.get("grounding_refs"), label=f"claim:{index}")
        if error:
            return False, error, {}
        reason = item["reason"].strip()[:160]
        if answer_ref not in anchors:
            return False, f"CLAIM_REVIEW_ANSWER_REF_INVALID:{index}:{answer_ref}", {}
        if target_id is not None and target_id not in investigation_ids:
            return False, f"CLAIM_REVIEW_UNKNOWN_TARGET:{index}:{target_id}", {}
        claims.append({
            "answer_ref": answer_ref, "answer_quote": anchors[answer_ref],
            "target_id": target_id, "statement": statement,
            "grounding_refs": refs or [],
            "evidence_ids": evidence_ids_from_grounding(refs or []),
            "verdict": verdict, "reason": reason,
        })

    semantic_gaps: List[Dict[str, Any]] = []
    for index, item in enumerate(raw["semantic_gaps"], start=1):
        gap_type = item["type"]
        gap_target_id = item.get("target_id")
        if gap_target_id is not None:
            gap_target_id = str(gap_target_id).strip()
        refs, error = normalize_grounding(item.get("grounding_refs"), label=f"semantic_gap:{index}")
        if error:
            return False, error, {}
        if gap_target_id is not None and gap_target_id not in investigation_ids:
            return False, f"CLAIM_REVIEW_UNKNOWN_TARGET:{index}:{gap_target_id}", {}
        semantic_gaps.append({
            "type": gap_type, "target_id": gap_target_id,
            "grounding_refs": refs or [],
            "evidence_ids": evidence_ids_from_grounding(refs or []),
            "required_property": item["required_property"].strip()[:300],
            "reason": item["reason"].strip()[:240],
        })

    return True, "ok", {
        "material_satisfaction": material_satisfaction,
        "answer_consistency": answer_consistency,
        "claims": claims, "semantic_gaps": semantic_gaps,
        "summary": _review_summary(claims, semantic_gaps, material_satisfaction, answer_consistency),
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
                "file": item.get("file"),
                "lines": [item.get("line_start"), item.get("line_end")],
                "source_type": item.get("source_type"),
            })
        ledger.append({
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
    answer_anchor_count: Optional[int] = None,
) -> int:
    """Choose a physical completion ceiling for the one global verifier pass."""
    base = max(128, int(base_tokens or 900))
    answer_chars = len(str(answer or ""))
    by_text = max(1, (answer_chars + 159) // 160)
    by_anchor = max(1, int(answer_anchor_count or 0)) if answer_anchor_count else 1
    desired = max(base, 720 + (max(by_text, by_anchor) * 180))
    if available_tokens is None:
        return int(desired)
    return min(int(desired), max(1, int(available_tokens)))


def review_prompt(
    answer: str,
    evidence_view: List[Dict[str, Any]],
    request: Any,
    *,
    answer_anchors: Optional[List[Dict[str, Any]]] = None,
    investigation: Optional[List[Dict[str, Any]]] = None,
    runtime_facts: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build the sole canonical semantic-review packet.

    There are no scope-only, Claim-only, Findings-only or semantic-gap repair
    tasks in Rev5.6. Claim always audits the complete provisional answer.
    """
    anchors = answer_anchors if answer_anchors is not None else build_answer_anchors(answer)
    public_anchors = [
        {"id": str(item.get("id") or ""), "text": str(item.get("text") or "")}
        for item in anchors
        if isinstance(item, dict) and item.get("id")
    ]
    payload: Dict[str, Any] = {
        "task": "verify_claims",
        "request": str(request or ""),
        "answer_anchors": public_anchors,
        "evidence": evidence_view,
        "runtime_facts": [dict(item) for item in (runtime_facts or []) if isinstance(item, dict)],
        "investigation": [dict(item) for item in (investigation or []) if isinstance(item, dict)],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def review_followup_feedback(review: Dict[str, Any]) -> str:
    """Serialize reviewer debt without adding runtime semantics.

    The Claim Verifier already decided which material statements are
    contradicted/insufficient and which semantic gaps exist. The runtime only
    preserves those coordinates (Claim id, answer_ref, target_id, Evidence ids
    and reason) so the Main LLM can choose the correction. The serialized text is
    derived from the stored Claim Review; it is not a second persisted state.
    """
    claims = []
    for claim in problematic_claims(review):
        verdict = str(claim.get("verdict") or "")
        if verdict not in {"contradicted", "insufficient"}:
            continue
        claims.append({
            "answer_ref": claim.get("answer_ref"),
            "target_id": claim.get("target_id"),
            "statement": claim.get("statement"),
            "verdict": verdict,
            "grounding_refs": list(claim.get("grounding_refs") or []),
            "evidence_ids": list(claim.get("evidence_ids") or []),
            "reason": claim.get("reason", ""),
        })
    gaps = []
    for gap in problematic_semantic_gaps(review):
        gaps.append({
            "type": gap.get("type"),
            "target_id": gap.get("target_id"),
            "grounding_refs": list(gap.get("grounding_refs") or []),
            "evidence_ids": list(gap.get("evidence_ids") or []),
            "required_property": gap.get("required_property", ""),
            "reason": gap.get("reason", ""),
        })
    material_satisfaction = dict((review or {}).get("material_satisfaction") or {})
    answer_consistency = dict((review or {}).get("answer_consistency") or {})
    payload = {
        "code": "CLAIM_REVIEW_FOLLOWUP",
        "material_satisfaction": material_satisfaction,
        "answer_consistency": answer_consistency,
        "claims": claims,
        "semantic_gaps": gaps,
        "instruction": (
            "The semantic reviewer rejected factual support, material delivery, or visible answer consistency. You remain the only producer of task semantics. "
            "You decide the next action. Decide whether the debt is answer-only or requires new investigation. If answer_consistency=conflict and retained Evidence already "
            "decides the issue, correct the final directly and reconcile the visible verdicts without new tools; otherwise continue/create the material target and investigate "
            "the missing proof. You may also narrow/remove unsupported statements or state a real limitation. "
            "Use semantic_gaps[].required_property as the precise unresolved property when present. Preserve the requested property; "
            "do not replace it with an easier proxy. The reviewer does not choose tools or rewrite your answer."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

