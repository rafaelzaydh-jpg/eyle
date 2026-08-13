"""Main-owned semantic notebook for Eyle 2.7.5 Rev1.3.4.

Runtime validates only shape and physical grounding references. Goals, status,
reasons and whether grounding is semantically needed belong entirely to Main.
Claim may challenge a final answer but never mutates this notebook.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

TARGET_STATUSES = {"open", "established", "dismissed"}
_TARGET_FIELDS = {"id", "goal", "status", "grounding_ids", "reason"}


def _ids(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def apply_investigation_updates(
    raw: Any,
    *,
    previous: Sequence[Dict[str, Any]] | None = None,
    grounding: Dict[str, Any] | None = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Apply Main target deltas against canonical Observation grounding.

    Omitted targets stay unchanged. Main may revise its own goal/status/reason.
    Runtime only rejects malformed fields or references to nonexistent material.
    """
    canonical = [dict(item) for item in (previous or []) if isinstance(item, dict)]
    if not isinstance(raw, list):
        return canonical, [], [{"id": None, "reason": "INVESTIGATION_UPDATES_LIST_REQUIRED"}]

    ground_store = grounding if isinstance(grounding, dict) else {}
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
        raw_grounding = item.get("grounding_ids")
        if not goal or len(goal) > 500:
            reject(f"INVESTIGATION_TARGET_GOAL_INVALID:{target_id}")
            continue
        if status not in TARGET_STATUSES:
            reject(f"INVESTIGATION_TARGET_STATUS_INVALID:{target_id}")
            continue
        if not isinstance(raw_grounding, list) or any(
            not isinstance(value, str) or not value.strip() for value in raw_grounding
        ):
            reject(f"INVESTIGATION_TARGET_GROUNDING_INVALID:{target_id}")
            continue

        incoming = _ids(raw_grounding)
        missing = [ref for ref in incoming if ref not in ground_store]
        if missing:
            reject(f"INVESTIGATION_UNKNOWN_GROUNDING:{target_id}:" + ",".join(missing))
            continue

        current = canonical[index_by_id[target_id]] if target_id in index_by_id else None
        grounding_ids = _ids(current.get("grounding_ids") or []) if current is not None else []
        for grounding_id in incoming:
            if grounding_id not in grounding_ids:
                grounding_ids.append(grounding_id)

        if len(reason) > 500:
            reject(f"INVESTIGATION_TARGET_REASON_TOO_LONG:{target_id}")
            continue

        normalized = {
            "id": target_id,
            "goal": goal,
            "status": status,
            "grounding_ids": grounding_ids,
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
            "grounding_ids": grounding_ids,
        })

    return canonical, accepted, rejected


def investigation_grounding_ids(investigation: Sequence[Dict[str, Any]] | None) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in investigation or []:
        if not isinstance(item, dict):
            continue
        for grounding_id in item.get("grounding_ids") or []:
            value = str(grounding_id or "").strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
    return result
