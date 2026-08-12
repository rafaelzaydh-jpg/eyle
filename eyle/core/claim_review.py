"""Small adversarial semantic review for Eyle 2.7.5 Rev1.3.

Claim is deliberately narrow: it can accept a provisional answer or return a
small list of material blockers. It never plans, selects tools, mutates
Investigation, rewrites the answer, or expands into a second reasoning loop.
Runtime validates only physical coordinates and freshness.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .execution_context import current_execution
from llm.structured import (
    CLAIM_MAX_GROUNDING_REFS, CLAIM_MAX_GROUNDING_REF_CHARS,
    CLAIM_MAX_ISSUES, CLAIM_MAX_REASON_CHARS,
)

CLAIM_MODES = {"off", "self_check", "verified"}
CLAIM_ISSUE_KINDS = {"unsupported", "contradicted", "scope", "omission", "inconsistent"}
GROUNDING_PREFIXES = {"observation", "runtime", "answer", "request"}

_CLAIMS_FIELDS = {"mode", "verifier", "grounding"}
_VERIFIER_COMMON_FIELDS = {"temperature"}
_VERIFIER_VERIFIED_FIELDS = _VERIFIER_COMMON_FIELDS | {"base_url", "model", "openai_compatible"}
_GROUNDING_FIELDS = {"max_chars_per_item"}

ANSWER_ANCHOR_MAX_CHARS = 700
REQUEST_ANCHOR_MAX_CHARS = 700


class ClaimConfigError(ValueError):
    """Invalid Claim configuration; never silently reinterpret it."""


def _build_literal_anchors(text: str, *, namespace: str, prefix: str, max_chars: int) -> List[Dict[str, Any]]:
    value = str(text or "")
    cap = max(120, int(max_chars or 700))
    anchors: List[Dict[str, Any]] = []
    cursor = 0
    boundary = re.compile(r"(?:[.!?;:](?=\s|$)|\n)")
    while cursor < len(value):
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        if cursor >= len(value):
            break
        hard_end = min(len(value), cursor + cap)
        match = boundary.search(value, cursor, hard_end)
        if match is not None:
            end = match.end()
        elif hard_end < len(value):
            whitespace = max(value.rfind(" ", cursor, hard_end), value.rfind("\t", cursor, hard_end))
            end = whitespace if whitespace > cursor else hard_end
        else:
            end = len(value)
        start = cursor
        while end > start and value[end - 1].isspace():
            end -= 1
        if end <= start:
            cursor = max(cursor + 1, hard_end)
            continue
        anchor_id = f"{prefix}{len(anchors) + 1}"
        anchors.append({
            "id": anchor_id,
            "ref": f"{namespace}:{anchor_id}",
            "text": value[start:end],
            "start": start,
            "end": end,
        })
        cursor = max(end, cursor + 1)
    return anchors


def build_request_anchors(request: Any, *, max_chars: int = REQUEST_ANCHOR_MAX_CHARS) -> List[Dict[str, Any]]:
    return _build_literal_anchors(str(request or ""), namespace="request", prefix="r", max_chars=max_chars)


def build_answer_anchors(answer: str, *, max_chars: int = ANSWER_ANCHOR_MAX_CHARS) -> List[Dict[str, Any]]:
    return _build_literal_anchors(str(answer or ""), namespace="answer", prefix="a", max_chars=max_chars)


def _object_field(parent: Dict[str, Any], key: str) -> Dict[str, Any]:
    if key not in parent:
        return {}
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ClaimConfigError(f"agent.claims.{key} precisa ser um objeto")
    return value


def _int_field(parent: Dict[str, Any], key: str, default: int, minimum: int, prefix: str) -> int:
    if key not in parent:
        return default
    value = parent.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ClaimConfigError(f"{prefix}.{key} precisa ser inteiro >= {minimum}")
    return value


def _float_field(parent: Dict[str, Any], key: str, default: float, prefix: str) -> float:
    if key not in parent:
        return default
    value = parent.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ClaimConfigError(f"{prefix}.{key} precisa ser numérico")
    return float(value)


def claim_config(config: Dict[str, Any]) -> Dict[str, Any]:
    agent = ((config or {}).get("agent") or {})
    if not isinstance(agent, dict):
        raise ClaimConfigError("agent precisa ser um objeto")
    raw = agent.get("claims") or {}
    if not isinstance(raw, dict):
        raise ClaimConfigError("agent.claims precisa ser um objeto")
    unknown = sorted(set(raw) - _CLAIMS_FIELDS)
    if unknown:
        raise ClaimConfigError("UNKNOWN_CONFIG_FIELD:agent.claims:" + ",".join(unknown))

    mode_raw = raw.get("mode", "self_check")
    if not isinstance(mode_raw, str):
        raise ClaimConfigError("agent.claims.mode precisa ser string")
    mode = mode_raw.strip().lower()
    if mode not in CLAIM_MODES:
        raise ClaimConfigError(f"agent.claims.mode inválido: {mode_raw}")

    verifier = _object_field(raw, "verifier")
    grounding_cfg = _object_field(raw, "grounding")
    unknown_grounding = sorted(set(grounding_cfg) - _GROUNDING_FIELDS)
    if unknown_grounding:
        raise ClaimConfigError("UNKNOWN_CONFIG_FIELD:agent.claims.grounding:" + ",".join(unknown_grounding))

    transport_keys = {"base_url", "model", "openai_compatible"}
    allowed_verifier = _VERIFIER_VERIFIED_FIELDS if mode == "verified" else _VERIFIER_COMMON_FIELDS
    unknown_verifier = sorted(set(verifier) - allowed_verifier)
    if unknown_verifier:
        raise ClaimConfigError("UNKNOWN_CONFIG_FIELD:agent.claims.verifier:" + ",".join(unknown_verifier))
    if mode == "self_check" and set(verifier).intersection(transport_keys):
        raise ClaimConfigError("agent.claims.verifier em self_check herda a LLM principal")

    transport: Dict[str, Any] = {}
    if mode == "verified":
        missing = sorted(transport_keys - set(verifier))
        if missing:
            raise ClaimConfigError("agent.claims.verifier em verified exige: " + ", ".join(missing))
        if not isinstance(verifier.get("base_url"), str) or not verifier["base_url"].strip():
            raise ClaimConfigError("agent.claims.verifier.base_url precisa ser string não vazia")
        if not isinstance(verifier.get("model"), str) or not verifier["model"].strip():
            raise ClaimConfigError("agent.claims.verifier.model precisa ser string não vazia")
        if not isinstance(verifier.get("openai_compatible"), bool):
            raise ClaimConfigError("agent.claims.verifier.openai_compatible precisa ser booleano")
        transport = {
            "base_url": verifier["base_url"].strip(),
            "model": verifier["model"].strip(),
            "openai_compatible": verifier["openai_compatible"],
        }

    resolved = {
        "mode": mode,
        "verifier": {
            **transport,
            "temperature": _float_field(verifier, "temperature", 0.0, "agent.claims.verifier"),
        },
        "grounding": {
            "max_chars_per_item": _int_field(
                grounding_cfg, "max_chars_per_item", 1400, 120, "agent.claims.grounding"
            ),
        },
    }

    if mode == "verified":
        llm = ((config or {}).get("llm") or {})
        main = (str(llm.get("base_url") or "http://localhost:11434").rstrip("/"), str(llm.get("model") or "").strip())
        verifier_id = (str(transport["base_url"]).rstrip("/"), str(transport["model"]).strip())
        if main == verifier_id:
            raise ClaimConfigError("VERIFIED_REQUIRES_DISTINCT_VERIFIER")
    return resolved


def _excerpt(item: Dict[str, Any], max_chars: int) -> str:
    text = str(item.get("numbered_content") or item.get("content") or "")
    if len(text) <= max_chars:
        return text
    head = max(1, max_chars * 2 // 3)
    tail = max(1, max_chars - head)
    return text[:head].rstrip() + "\n...[cropped]...\n" + text[-tail:].lstrip()


def compact_grounding(
    grounding: Dict[str, Any], grounding_ids: Optional[Iterable[str]], *, max_chars_per_item: int,
) -> List[Dict[str, Any]]:
    wanted = [str(item) for item in (grounding_ids or []) if str(item)]
    out: List[Dict[str, Any]] = []
    for grounding_id in wanted:
        item = grounding.get(grounding_id)
        if not isinstance(item, dict):
            continue
        entry = {
            "ref": f"observation:{grounding_id}",
            "locator": dict(item.get("locator") or {}) if isinstance(item.get("locator"), dict) else {},
            "source_type": item.get("source_type"),
            "cropped": bool(
                item.get("truncated") or item.get("context_truncated")
                or len(str(item.get("numbered_content") or item.get("content") or "")) > max_chars_per_item
            ),
            "excerpt": _excerpt(item, max_chars_per_item),
        }
        out.append({k: v for k, v in entry.items() if v not in (None, "", [], {})})
    return out


def _ids(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def grounding_ids_from_refs(refs: Iterable[str]) -> List[str]:
    return _ids(str(ref).split(":", 1)[1] for ref in refs or [] if str(ref).startswith("observation:"))


def _bounded_runtime_result(value: Any, max_chars: int = 500) -> Any:
    """Compact Runtime facts without knowing any capability/domain vocabulary."""
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        raw = str(value)
    if len(raw) <= max_chars:
        return value
    if not isinstance(value, dict):
        return {"truncated": True, "preview": raw[:max_chars]}

    summary = {}
    for key in (
        "status", "ok", "executed", "changed", "error_code", "retryable",
        "failure_scope", "failure_resource",
    ):
        if value.get(key) is not None:
            summary[key] = copy.deepcopy(value.get(key))
    if isinstance(value.get("coverage"), dict):
        summary["coverage"] = copy.deepcopy(value.get("coverage"))
    if isinstance(value.get("frontiers"), list):
        summary["frontiers"] = [
            {key: copy.deepcopy(item.get(key)) for key in ("id", "kind", "at", "count", "reason") if item.get(key) is not None}
            for item in value.get("frontiers")[:8] if isinstance(item, dict)
        ]
    detail = value.get("detail")
    if detail is not None:
        try:
            detail_raw = json.dumps(detail, ensure_ascii=False, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            detail_raw = str(detail)
        if isinstance(detail, dict):
            summary["detail_keys"] = [str(key)[:80] for key in list(detail)[:24]]
        summary["detail_preview"] = detail_raw[:max(80, max_chars // 2)]
    if not summary:
        return {"truncated": True, "preview": raw[:max_chars]}
    summary["payload_truncated"] = True
    return summary


def compact_runtime_facts(observation_ledger: Dict[str, Any], *, max_items: int = 24) -> List[Dict[str, Any]]:
    events = list((observation_ledger or {}).get("events") or [])
    execution = current_execution()
    start = int(execution.observation_event_start or 0) if execution is not None else 0
    selected = events[start:][-max(1, int(max_items)):]
    out: List[Dict[str, Any]] = []
    for index, event in enumerate(selected, start=1):
        if not isinstance(event, dict):
            continue
        item = {
            "ref": f"runtime:r{index}",
            "event_id": event.get("event_id"),
            "turn": event.get("turn"),
            "tool": event.get("tool"),
            "status": event.get("status"),
            "executed": bool(event.get("executed")),
            "ok": bool(event.get("ok")),
            "error_code": event.get("error_code"),
            "result": _bounded_runtime_result(event.get("result") or {}),
        }
        out.append({k: v for k, v in item.items() if v is not None})
    return out


def _validate_grounding_refs(
    refs: Iterable[str], *, visible_grounding_ids: set[str], runtime_fact_ids: set[str],
    answer_ids: set[str], request_ids: set[str],
) -> Tuple[bool, str]:
    for raw in refs or []:
        ref = str(raw or "").strip()
        if ref == "request":
            continue
        if ":" not in ref:
            return False, f"CLAIM_REVIEW_GROUNDING_REF_INVALID:{ref}"
        prefix, value = ref.split(":", 1)
        if prefix not in GROUNDING_PREFIXES or not value:
            return False, f"CLAIM_REVIEW_GROUNDING_REF_INVALID:{ref}"
        if prefix == "observation" and value not in visible_grounding_ids:
            return False, f"CLAIM_REVIEW_GROUNDING_OBSERVATION_NOT_VISIBLE:{value}"
        if prefix == "runtime" and ref not in runtime_fact_ids:
            return False, f"CLAIM_REVIEW_GROUNDING_RUNTIME_UNKNOWN:{value}"
        if prefix == "answer" and ref not in answer_ids:
            return False, f"CLAIM_REVIEW_GROUNDING_ANSWER_UNKNOWN:{value}"
        if prefix == "request" and ref not in request_ids:
            return False, f"CLAIM_REVIEW_GROUNDING_REQUEST_UNKNOWN:{value}"
    return True, "ok"


def normalize_claim_review(
    raw: Any,
    grounding: Dict[str, Any],
    *,
    answer: str = "",
    answer_anchors: Optional[Sequence[Dict[str, Any]]] = None,
    request_anchors: Optional[Sequence[Dict[str, Any]]] = None,
    visible_grounding_ids: Optional[Iterable[str]] = None,
    runtime_facts: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    if not isinstance(raw, dict) or set(raw) != {"verdict", "issues"}:
        return False, "CLAIM_REVIEW_SHAPE_INVALID", {}
    verdict = str(raw.get("verdict") or "").strip()
    issues_raw = raw.get("issues")
    if verdict not in {"accept", "challenge"} or not isinstance(issues_raw, list):
        return False, "CLAIM_REVIEW_SHAPE_INVALID", {}
    if verdict == "accept" and issues_raw:
        return False, "CLAIM_REVIEW_ACCEPT_WITH_ISSUES", {}
    if verdict == "challenge" and not issues_raw:
        return False, "CLAIM_REVIEW_CHALLENGE_REQUIRES_ISSUE", {}
    if len(issues_raw) > CLAIM_MAX_ISSUES:
        return False, "CLAIM_REVIEW_ISSUES_TOO_MANY", {}

    anchors = list(answer_anchors or build_answer_anchors(answer))
    answer_refs = {str(item.get("ref") or "") for item in anchors if isinstance(item, dict) and item.get("ref")}
    request_refs = {str(item.get("ref") or "") for item in (request_anchors or []) if isinstance(item, dict) and item.get("ref")}
    runtime_refs = {
        str(item.get("ref") or "") for item in (runtime_facts or [])
        if isinstance(item, dict) and item.get("ref")
    }
    visible = set(_ids(visible_grounding_ids if visible_grounding_ids is not None else grounding.keys()))

    issues: List[Dict[str, Any]] = []
    for index, item in enumerate(issues_raw, start=1):
        if not isinstance(item, dict) or set(item) != {"kind", "answer_ref", "grounding_refs", "reason"}:
            return False, f"CLAIM_REVIEW_ISSUE_SHAPE_INVALID:{index}", {}
        kind = str(item.get("kind") or "").strip()
        if kind not in CLAIM_ISSUE_KINDS:
            return False, f"CLAIM_REVIEW_ISSUE_KIND_INVALID:{index}", {}
        answer_ref = item.get("answer_ref")
        if answer_ref is not None:
            answer_ref = str(answer_ref).strip()
            if answer_ref not in answer_refs:
                return False, f"CLAIM_REVIEW_ANSWER_REF_INVALID:{index}:{answer_ref}", {}
        refs = _ids(item.get("grounding_refs") or [])
        if len(refs) > CLAIM_MAX_GROUNDING_REFS:
            return False, f"CLAIM_REVIEW_GROUNDING_REFS_TOO_MANY:{index}", {}
        if any(len(ref) > CLAIM_MAX_GROUNDING_REF_CHARS for ref in refs):
            return False, f"CLAIM_REVIEW_GROUNDING_REF_TOO_LONG:{index}", {}
        ok, reason = _validate_grounding_refs(
            refs,
            visible_grounding_ids=visible,
            runtime_fact_ids=runtime_refs,
            answer_ids=answer_refs,
            request_ids=request_refs,
        )
        if not ok:
            return False, f"{reason}:issue:{index}", {}
        why = str(item.get("reason") or "").strip()
        if len(why) > CLAIM_MAX_REASON_CHARS:
            return False, f"CLAIM_REVIEW_REASON_TOO_LONG:{index}", {}
        if not refs or not why:
            return False, f"CLAIM_REVIEW_ISSUE_INCOMPLETE:{index}", {}
        issues.append({
            "kind": kind,
            "answer_ref": answer_ref,
            "grounding_refs": refs,
            "grounding_ids": grounding_ids_from_refs(refs),
            "reason": why,
        })

    summary = {"issues": len(issues)}
    for kind in CLAIM_ISSUE_KINDS:
        count = sum(1 for item in issues if item.get("kind") == kind)
        if count:
            summary[kind] = count
    return True, "ok", {"verdict": verdict, "issues": issues, "summary": summary}


def claim_grounding_ledger(review: Dict[str, Any], grounding: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for issue in (review or {}).get("issues") or []:
        if not isinstance(issue, dict):
            continue
        sources = []
        for grounding_id in issue.get("grounding_ids") or []:
            item = grounding.get(grounding_id) or {}
            sources.append({
                "grounding_id": grounding_id,
                "locator": dict(item.get("locator") or {}) if isinstance(item.get("locator"), dict) else {},
                "source_type": item.get("source_type"),
            })
        out.append({
            "kind": issue.get("kind"),
            "answer_ref": issue.get("answer_ref"),
            "reason": issue.get("reason"),
            "sources": sources,
        })
    return out


def review_prompt(
    answer: str,
    grounding_view: List[Dict[str, Any]],
    request: Any,
    *,
    answer_anchors: Optional[List[Dict[str, Any]]] = None,
    request_anchors: Optional[List[Dict[str, Any]]] = None,
    runtime_facts: Optional[List[Dict[str, Any]]] = None,
) -> str:
    anchors = answer_anchors if answer_anchors is not None else build_answer_anchors(answer)
    req_anchors = request_anchors if request_anchors is not None else build_request_anchors(request)
    payload: Dict[str, Any] = {
        "task": "challenge_or_accept",
        "request": str(request or ""),
        "request_anchors": [
            {"ref": str(item.get("ref") or f"request:{item.get('id')}"), "text": str(item.get("text") or "")}
            for item in req_anchors if isinstance(item, dict) and (item.get("ref") or item.get("id"))
        ],
        "answer_anchors": [
            {"ref": str(item.get("ref") or f"answer:{item.get('id')}"), "text": str(item.get("text") or "")}
            for item in anchors if isinstance(item, dict) and (item.get("ref") or item.get("id"))
        ],
        "observed_material": grounding_view,
        "runtime_facts": [dict(item) for item in (runtime_facts or []) if isinstance(item, dict)],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def review_followup_feedback(review: Dict[str, Any]) -> str:
    """Return only Claim's blockers. Main chooses every semantic next step."""
    issues = []
    for item in (review or {}).get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append({
            "kind": item.get("kind"),
            "answer_ref": item.get("answer_ref"),
            "grounding_refs": list(item.get("grounding_refs") or []),
            "reason": item.get("reason"),
        })
    return json.dumps({"code": "CLAIM_CHALLENGE", "issues": issues}, ensure_ascii=False, separators=(",", ":"), default=str)
