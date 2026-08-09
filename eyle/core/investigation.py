"""Deterministic Investigation Contract helpers.

The Main LLM owns target semantics. The runtime only preserves target identity,
validates structural transitions and Evidence references, and applies reviewer
feedback that explicitly names an existing target.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple


TARGET_STATUSES = {"open", "established", "dismissed"}
_TARGET_FIELDS = {"id", "goal", "status", "evidence_ids", "reason"}
WORKSPACE_SCOPE_MODES = {"none", "read", "write"}


def _ids(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def validate_investigation(
    raw: Any,
    *,
    previous: Sequence[Dict[str, Any]] | None = None,
    evidence: Dict[str, Any] | None = None,
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Validate one complete Investigation Contract snapshot.

    This function never decides whether a target is materially necessary or
    whether cited Evidence proves its goal. Those are semantic decisions owned
    by the Main LLM and Claim Review.
    """
    if not isinstance(raw, list):
        return False, "INVESTIGATION_LIST_REQUIRED", []

    known_evidence = set((evidence or {}).keys())
    normalized: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or set(item) != _TARGET_FIELDS:
            return False, f"INVESTIGATION_TARGET_SHAPE_INVALID:{index}", []
        target_id = str(item.get("id") or "").strip()
        goal = str(item.get("goal") or "").strip()
        status = str(item.get("status") or "").strip()
        reason = str(item.get("reason") or "").strip()
        raw_evidence = item.get("evidence_ids")

        if not target_id or len(target_id) > 80:
            return False, f"INVESTIGATION_TARGET_ID_INVALID:{index}", []
        if target_id in seen_ids:
            return False, f"INVESTIGATION_TARGET_ID_DUPLICATE:{target_id}", []
        if not goal or len(goal) > 500:
            return False, f"INVESTIGATION_TARGET_GOAL_INVALID:{target_id}", []
        if status not in TARGET_STATUSES:
            return False, f"INVESTIGATION_TARGET_STATUS_INVALID:{target_id}", []
        if not isinstance(raw_evidence, list) or any(not isinstance(value, str) or not value.strip() for value in raw_evidence):
            return False, f"INVESTIGATION_TARGET_EVIDENCE_INVALID:{target_id}", []
        evidence_ids = _ids(raw_evidence)
        missing = [evidence_id for evidence_id in evidence_ids if evidence_id not in known_evidence]
        if missing:
            return False, f"INVESTIGATION_UNKNOWN_EVIDENCE:{target_id}:" + ",".join(missing), []
        if status == "established" and not evidence_ids:
            return False, f"INVESTIGATION_ESTABLISHED_REQUIRES_EVIDENCE:{target_id}", []
        if status in {"established", "dismissed"} and not reason:
            return False, f"INVESTIGATION_STATUS_REQUIRES_REASON:{target_id}:{status}", []
        if len(reason) > 500:
            return False, f"INVESTIGATION_TARGET_REASON_TOO_LONG:{target_id}", []

        seen_ids.add(target_id)
        normalized.append({
            "id": target_id,
            "goal": goal,
            "status": status,
            "evidence_ids": evidence_ids,
            "reason": reason,
        })

    previous_items = [item for item in (previous or []) if isinstance(item, dict)]
    previous_by_id = {str(item.get("id") or ""): item for item in previous_items if str(item.get("id") or "")}
    current_by_id = {item["id"]: item for item in normalized}
    for target_id, previous_item in previous_by_id.items():
        current = current_by_id.get(target_id)
        if current is None:
            return False, f"INVESTIGATION_TARGET_DROPPED:{target_id}", []
        if str(previous_item.get("goal") or "").strip() != current["goal"]:
            return False, f"INVESTIGATION_TARGET_GOAL_MUTATED:{target_id}", []

    return True, "ok", normalized


def target_evidence_ids(investigation: Sequence[Dict[str, Any]] | None) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in investigation or []:
        if not isinstance(item, dict):
            continue
        for evidence_id in item.get("evidence_ids") or []:
            value = str(evidence_id or "").strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
    return result


def open_target_ids(investigation: Sequence[Dict[str, Any]] | None) -> List[str]:
    """Return objectively open target IDs without interpreting their goals."""
    return [
        str(item.get("id") or "")
        for item in (investigation or [])
        if isinstance(item, dict) and item.get("status") == "open" and str(item.get("id") or "")
    ]


def validate_workspace_scope(
    raw: Any,
    *,
    previous: Dict[str, Any] | None = None,
    project_available: bool,
    investigation: Sequence[Dict[str, Any]] | None = None,
    project_action: bool = False,
    patches_requested: bool = False,
) -> Tuple[bool, str, Dict[str, str]]:
    """Validate the Main LLM's semantic workspace declaration as a contract.

    The LLM decides whether the active request depends on the live workspace and
    whether that dependency is read-only or write-oriented. The runtime only
    enforces shape, monotonic authority and consistency with observable actions.
    """
    if not isinstance(raw, dict) or set(raw) != {"mode", "reason"}:
        return False, "WORKSPACE_SCOPE_SHAPE_INVALID", {}
    mode = str(raw.get("mode") or "").strip()
    reason = str(raw.get("reason") or "").strip()
    if mode not in WORKSPACE_SCOPE_MODES:
        return False, "WORKSPACE_SCOPE_MODE_INVALID", {}
    if not reason or len(reason) > 300:
        return False, "WORKSPACE_SCOPE_REASON_INVALID", {}
    if not project_available and mode != "none":
        return False, "WORKSPACE_SCOPE_PROJECT_UNAVAILABLE", {}

    previous_mode = str((previous or {}).get("mode") or "").strip()
    if previous_mode == "write" and mode != "write":
        return False, "WORKSPACE_SCOPE_DOWNGRADE:write", {}
    if previous_mode == "read" and mode == "none":
        return False, "WORKSPACE_SCOPE_DOWNGRADE:read", {}

    if investigation and mode == "none":
        return False, "WORKSPACE_SCOPE_INVESTIGATION_REQUIRES_PROJECT", {}
    if project_action and mode == "none":
        return False, "WORKSPACE_SCOPE_PROJECT_ACTION_REQUIRES_PROJECT", {}
    if patches_requested and mode != "write":
        return False, "WORKSPACE_SCOPE_PATCH_REQUIRES_WRITE", {}
    return True, "ok", {"mode": mode, "reason": reason}


def reopen_targets_from_semantic_gaps(
    investigation: Sequence[Dict[str, Any]] | None,
    semantic_gaps: Sequence[Dict[str, Any]] | None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Apply reviewer-directed reopenings without inventing target semantics."""
    result = [dict(item) for item in (investigation or []) if isinstance(item, dict)]
    by_id = {str(item.get("id") or ""): item for item in result if str(item.get("id") or "")}
    reopened: List[str] = []
    for gap in semantic_gaps or []:
        if not isinstance(gap, dict):
            continue
        target_id = gap.get("target_id")
        if target_id is None:
            continue
        target_id = str(target_id or "").strip()
        target = by_id.get(target_id)
        if target is None:
            continue
        target["status"] = "open"
        merged = _ids(list(target.get("evidence_ids") or []) + list(gap.get("evidence_ids") or []))
        target["evidence_ids"] = merged
        target["reason"] = str(gap.get("reason") or "").strip()[:500]
        if target_id not in reopened:
            reopened.append(target_id)
    return result, reopened


def reopen_targets_from_review(
    investigation: Sequence[Dict[str, Any]] | None,
    review: Dict[str, Any] | None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Apply only reviewer-declared target mappings from an insufficient review.

    The verifier owns the semantic mapping. The runtime merely reopens an
    existing target explicitly named by either a Semantic Gap or an
    ``insufficient`` Claim. ``target_id=null`` never invents a new target.
    """
    result = [dict(item) for item in (investigation or []) if isinstance(item, dict)]
    by_id = {str(item.get("id") or ""): item for item in result if str(item.get("id") or "")}
    reopened: List[str] = []

    issues: List[Dict[str, Any]] = []
    if isinstance(review, dict):
        issues.extend(item for item in (review.get("semantic_gaps") or []) if isinstance(item, dict))
        issues.extend(
            item for item in (review.get("claims") or [])
            if isinstance(item, dict) and item.get("verdict") == "insufficient"
        )

    for issue in issues:
        target_id = issue.get("target_id")
        if target_id is None:
            continue
        target_id = str(target_id or "").strip()
        target = by_id.get(target_id)
        if target is None:
            continue
        target["status"] = "open"
        target["evidence_ids"] = _ids(
            list(target.get("evidence_ids") or []) + list(issue.get("evidence_ids") or [])
        )
        reason = str(issue.get("reason") or "").strip()
        if reason:
            target["reason"] = reason[:500]
        if target_id not in reopened:
            reopened.append(target_id)
    return result, reopened

