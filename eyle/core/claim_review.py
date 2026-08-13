"""Fresh independent Final review for Eyle 2.7.5 Rev1.3.4.

Claim is a delivery gate, not a second agent.  It receives only the canonical
user request, the candidate Final and the observed material that Main selected
for that Final.  It has no conversation history, Investigation, Tasks, Runtime
events, tool authority or recovery state.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple


CLAIM_MODES = {"off", "fresh", "verified"}
CLAIM_ISSUE_KINDS = {"unsupported", "contradicted", "scope", "omission", "inconsistent", "unsafe"}

_CLAIMS_FIELDS = {"mode", "verifier", "grounding"}
_VERIFIER_COMMON_FIELDS = {"temperature"}
_VERIFIER_VERIFIED_FIELDS = _VERIFIER_COMMON_FIELDS | {"base_url", "model", "openai_compatible"}
_GROUNDING_FIELDS = {"max_chars_per_item"}


class ClaimConfigError(ValueError):
    """Invalid Claim configuration; never silently reinterpret it."""


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
    """Resolve the fresh/verified critic transport without semantic state."""
    agent = ((config or {}).get("agent") or {})
    if not isinstance(agent, dict):
        raise ClaimConfigError("agent precisa ser um objeto")
    raw = agent.get("claims") or {}
    if not isinstance(raw, dict):
        raise ClaimConfigError("agent.claims precisa ser um objeto")
    unknown = sorted(set(raw) - _CLAIMS_FIELDS)
    if unknown:
        raise ClaimConfigError("UNKNOWN_CONFIG_FIELD:agent.claims:" + ",".join(unknown))

    mode_raw = raw.get("mode", "fresh")
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
    """Project only material explicitly selected by Main for the candidate Final."""
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


def _validate_grounding_refs(refs: Iterable[str], *, visible_grounding_ids: set[str]) -> Tuple[bool, str]:
    for raw in refs or []:
        ref = str(raw or "").strip()
        if ref == "request":
            continue
        if not ref.startswith("observation:"):
            return False, f"CLAIM_REVIEW_GROUNDING_REF_INVALID:{ref}"
        value = ref.split(":", 1)[1]
        if value not in visible_grounding_ids:
            return False, f"CLAIM_REVIEW_GROUNDING_OBSERVATION_NOT_VISIBLE:{value}"
    return True, "ok"


def normalize_claim_review(
    raw: Any,
    grounding: Dict[str, Any],
    *,
    visible_grounding_ids: Optional[Iterable[str]] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Validate only Claim's canonical delivery-gate protocol and coordinates."""
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

    visible = set(_ids(visible_grounding_ids if visible_grounding_ids is not None else grounding.keys()))
    issues: List[Dict[str, Any]] = []
    for index, item in enumerate(issues_raw, start=1):
        if not isinstance(item, dict) or set(item) != {"kind", "grounding_refs", "reason"}:
            return False, f"CLAIM_REVIEW_ISSUE_SHAPE_INVALID:{index}", {}
        kind = str(item.get("kind") or "").strip()
        if kind not in CLAIM_ISSUE_KINDS:
            return False, f"CLAIM_REVIEW_ISSUE_KIND_INVALID:{index}", {}
        refs = _ids(item.get("grounding_refs") or [])
        ok, reason = _validate_grounding_refs(refs, visible_grounding_ids=visible)
        if not ok:
            return False, f"{reason}:issue:{index}", {}
        why = str(item.get("reason") or "").strip()
        if not why:
            return False, f"CLAIM_REVIEW_ISSUE_INCOMPLETE:{index}", {}
        issues.append({
            "kind": kind,
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
            "reason": issue.get("reason"),
            "sources": sources,
        })
    return out


def review_prompt(answer: str, grounding_view: List[Dict[str, Any]], request: Any) -> str:
    """Build the complete fresh Claim packet with no Main/Runtime history."""
    return json.dumps(
        {
            "request": str(request or ""),
            "candidate_answer": str(answer or ""),
            "observed_material": grounding_view,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def review_followup_feedback(review: Dict[str, Any]) -> str:
    """Return Claim blockers only; Main owns every next semantic decision."""
    issues = []
    for item in (review or {}).get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append({
            "kind": item.get("kind"),
            "grounding_refs": list(item.get("grounding_refs") or []),
            "reason": item.get("reason"),
        })
    return json.dumps({"code": "CLAIM_CHALLENGE", "issues": issues}, ensure_ascii=False, separators=(",", ":"), default=str)
