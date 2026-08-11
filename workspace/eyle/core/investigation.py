"""Canonical Investigation Contract.

Investigation exists only when the Main LLM declares semantic debt. The runtime
never decides that a target is necessary; it only preserves target identity,
validates structural transitions/Evidence references, and applies reviewer
feedback that explicitly names an existing target.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

TARGET_STATUSES = {"open", "established", "dismissed"}
_TARGET_FIELDS = {"id", "goal", "status", "evidence_ids", "reason"}


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


def apply_investigation_updates(
    raw: Any,
    *,
    previous: Sequence[Dict[str, Any]] | None = None,
    evidence: Dict[str, Any] | None = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Apply Main-LLM target deltas to the canonical Investigation state.

    Omitted targets remain untouched. Existing goals are immutable. Evidence is
    additive per target. ``established`` requires at least one real Evidence ID;
    ``dismissed`` requires a reason. The runtime does not score usefulness,
    necessity or semantic sufficiency.
    """
    canonical = [dict(item) for item in (previous or []) if isinstance(item, dict)]
    if not isinstance(raw, list):
        return canonical, [], [{"id": None, "reason": "INVESTIGATION_UPDATES_LIST_REQUIRED"}]

    known_evidence = set((evidence or {}).keys())
    index_by_id = {
        str(item.get("id") or ""): index
        for index, item in enumerate(canonical)
        if str(item.get("id") or "")
    }
    counts: Dict[str, int] = {}
    for item in raw:
        if isinstance(item, dict):
            target_id = str(item.get("id") or "").strip()
            if target_id:
                counts[target_id] = counts.get(target_id, 0) + 1

    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for position, item in enumerate(raw, start=1):
        target_id = str(item.get("id") or "").strip() if isinstance(item, dict) else ""

        def reject(reason: str) -> None:
            rejected.append({"id": target_id or None, "reason": reason, "position": position})

        if not isinstance(item, dict) or set(item) != _TARGET_FIELDS:
            reject(f"INVESTIGATION_TARGET_SHAPE_INVALID:{position}")
            continue
        if not target_id or len(target_id) > 80:
            reject(f"INVESTIGATION_TARGET_ID_INVALID:{position}")
            continue
        if counts.get(target_id, 0) > 1:
            reject(f"INVESTIGATION_TARGET_ID_DUPLICATE:{target_id}")
            continue

        goal = str(item.get("goal") or "").strip()
        status = str(item.get("status") or "").strip()
        reason = str(item.get("reason") or "").strip()
        raw_evidence = item.get("evidence_ids")
        if not goal or len(goal) > 500:
            reject(f"INVESTIGATION_TARGET_GOAL_INVALID:{target_id}")
            continue
        if status not in TARGET_STATUSES:
            reject(f"INVESTIGATION_TARGET_STATUS_INVALID:{target_id}")
            continue
        if not isinstance(raw_evidence, list) or any(
            not isinstance(value, str) or not value.strip() for value in raw_evidence
        ):
            reject(f"INVESTIGATION_TARGET_EVIDENCE_INVALID:{target_id}")
            continue

        incoming_evidence = _ids(raw_evidence)
        missing = [evidence_id for evidence_id in incoming_evidence if evidence_id not in known_evidence]
        if missing:
            reject(f"INVESTIGATION_UNKNOWN_EVIDENCE:{target_id}:" + ",".join(missing))
            continue

        current = canonical[index_by_id[target_id]] if target_id in index_by_id else None
        if current is not None:
            if str(current.get("goal") or "").strip() != goal:
                reject(f"INVESTIGATION_TARGET_GOAL_MUTATED:{target_id}")
                continue
            evidence_ids = _ids(current.get("evidence_ids") or [])
        else:
            evidence_ids = []
        for evidence_id in incoming_evidence:
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)

        if status == "established" and not evidence_ids:
            reject(f"INVESTIGATION_ESTABLISHED_REQUIRES_EVIDENCE:{target_id}")
            continue
        if status in {"established", "dismissed"} and not reason:
            reject(f"INVESTIGATION_STATUS_REQUIRES_REASON:{target_id}:{status}")
            continue
        if len(reason) > 500:
            reject(f"INVESTIGATION_TARGET_REASON_TOO_LONG:{target_id}")
            continue

        normalized = {
            "id": target_id,
            "goal": goal,
            "status": status,
            "evidence_ids": evidence_ids,
            "reason": reason,
        }
        changed = current != normalized
        if current is None:
            index_by_id[target_id] = len(canonical)
            canonical.append(normalized)
        elif changed:
            canonical[index_by_id[target_id]] = normalized

        accepted.append({
            "id": target_id,
            "changed": bool(changed),
            "status": status,
            "evidence_ids": evidence_ids,
        })

    return canonical, accepted, rejected


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
    return [
        str(item.get("id") or "")
        for item in (investigation or [])
        if isinstance(item, dict) and item.get("status") == "open" and str(item.get("id") or "")
    ]


def reopen_targets_from_review(
    investigation: Sequence[Dict[str, Any]] | None,
    review: Dict[str, Any] | None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Reopen only an existing target explicitly challenged by Claim Review."""
    result = [dict(item) for item in (investigation or []) if isinstance(item, dict)]
    by_id = {str(item.get("id") or ""): item for item in result if str(item.get("id") or "")}
    reopened: List[str] = []

    issues: List[Dict[str, Any]] = []
    if isinstance(review, dict):
        issues.extend(item for item in (review.get("semantic_gaps") or []) if isinstance(item, dict))
        issues.extend(
            item for item in (review.get("claims") or [])
            if isinstance(item, dict) and item.get("verdict") in {"insufficient", "contradicted"}
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
        required_property = str(issue.get("required_property") or "").strip()
        reason = str(issue.get("reason") or "").strip()
        directed = "; ".join(part for part in (required_property, reason) if part)
        if directed:
            target["reason"] = directed[:500]
        if target_id not in reopened:
            reopened.append(target_id)
    return result, reopened
