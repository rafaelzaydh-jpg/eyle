"""Main-owned completion commitments for Eyle 2.7.5 Rev1.4.1.

A Task records work Main decided is required before delivery. Main owns the
meaning of the objective, completion criteria, result and evidence selection.
Runtime owns only structural integrity and enforces the commitments Main
explicitly created: open Tasks block Final, completed parents cannot retain open
children, and referenced Material must physically exist.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

TASK_STATUSES = {"open", "completed", "dropped"}
_TASK_FIELDS = {
    "id", "parent_id", "description", "completion_criteria",
    "status", "result", "grounding_ids",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ids(values: Any) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        item = _text(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _criteria(values: Any) -> List[str] | None:
    if not isinstance(values, list):
        return None
    result: List[str] = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            return None
        item = value.strip()
        if not item or len(item) > 400:
            return None
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _validate_field_shape(
    item: Any,
    *,
    position: int,
    grounding: Dict[str, Any],
) -> tuple[Dict[str, Any] | None, str | None]:
    if not isinstance(item, dict) or set(item) != _TASK_FIELDS:
        return None, f"TASK_SHAPE_INVALID:{position}"

    task_id = _text(item.get("id"))
    if not task_id or len(task_id) > 80 or re.fullmatch(r"[A-Za-z0-9._-]+", task_id) is None:
        return None, f"TASK_ID_INVALID:{position}"

    parent_raw = item.get("parent_id")
    if parent_raw is not None and not isinstance(parent_raw, str):
        return None, f"TASK_PARENT_ID_INVALID:{task_id}"
    parent_id = _text(parent_raw) if parent_raw is not None else None
    if parent_id == "":
        parent_id = None
    if parent_id is not None and (
        len(parent_id) > 80 or re.fullmatch(r"[A-Za-z0-9._-]+", parent_id) is None
    ):
        return None, f"TASK_PARENT_ID_INVALID:{task_id}"
    if parent_id == task_id:
        return None, f"TASK_PARENT_SELF_REFERENCE:{task_id}"

    description = _text(item.get("description"))
    if not description or len(description) > 500:
        return None, f"TASK_DESCRIPTION_INVALID:{task_id}"

    completion_criteria = _criteria(item.get("completion_criteria"))
    if not completion_criteria:
        return None, f"TASK_COMPLETION_CRITERIA_REQUIRED:{task_id}"
    if len(completion_criteria) > 12:
        return None, f"TASK_COMPLETION_CRITERIA_TOO_MANY:{task_id}"

    status = _text(item.get("status"))
    if status not in TASK_STATUSES:
        return None, f"TASK_STATUS_INVALID:{task_id}"

    result_raw = item.get("result")
    if not isinstance(result_raw, str):
        return None, f"TASK_RESULT_INVALID:{task_id}"
    result = result_raw.strip()
    if len(result) > 1200:
        return None, f"TASK_RESULT_TOO_LONG:{task_id}"
    if status in {"completed", "dropped"} and not result:
        return None, f"TASK_CLOSED_RESULT_REQUIRED:{task_id}"

    raw_grounding = item.get("grounding_ids")
    if not isinstance(raw_grounding, list) or any(
        not isinstance(value, str) or not value.strip() for value in raw_grounding
    ):
        return None, f"TASK_GROUNDING_INVALID:{task_id}"
    grounding_ids = _ids(raw_grounding)
    missing = [ref for ref in grounding_ids if ref not in grounding]
    if missing:
        return None, f"TASK_UNKNOWN_GROUNDING:{task_id}:" + ",".join(missing)

    return {
        "id": task_id,
        "parent_id": parent_id,
        "description": description,
        "completion_criteria": completion_criteria,
        "status": status,
        "result": result,
        "grounding_ids": grounding_ids,
    }, None


def _cycle_ids(tasks: Dict[str, Dict[str, Any]]) -> set[str]:
    cycles: set[str] = set()
    for start in tasks:
        order: list[str] = []
        seen_at: Dict[str, int] = {}
        current: str | None = start
        while current is not None and current in tasks:
            if current in seen_at:
                cycles.update(order[seen_at[current]:])
                break
            if current in cycles:
                break
            seen_at[current] = len(order)
            order.append(current)
            parent = tasks[current].get("parent_id")
            current = str(parent) if isinstance(parent, str) and parent else None
    return cycles


def _open_children(tasks: Dict[str, Dict[str, Any]], parent_id: str) -> List[str]:
    return sorted(
        task_id for task_id, item in tasks.items()
        if item.get("parent_id") == parent_id and item.get("status") == "open"
    )


def apply_task_updates(
    raw: Any,
    *,
    previous: Sequence[Dict[str, Any]] | None = None,
    grounding: Dict[str, Any] | None = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Apply Main task deltas against canonical completion commitments.

    Omitted Tasks persist. Runtime does not decide whether criteria are good or
    whether selected Material semantically proves a result. It only enforces the
    physical contract Main declared.
    """
    canonical = [dict(item) for item in (previous or []) if isinstance(item, dict)]
    if not isinstance(raw, list):
        return canonical, [], [{"id": None, "reason": "TASK_UPDATES_LIST_REQUIRED"}]

    ground_store = grounding if isinstance(grounding, dict) else {}
    current_by_id: Dict[str, Dict[str, Any]] = {
        _text(item.get("id")): dict(item) for item in canonical if _text(item.get("id"))
    }
    order = [_text(item.get("id")) for item in canonical if _text(item.get("id"))]

    counts: Dict[str, int] = {}
    for item in raw:
        if isinstance(item, dict):
            task_id = _text(item.get("id"))
            if task_id:
                counts[task_id] = counts.get(task_id, 0) + 1

    candidates: Dict[str, Dict[str, Any]] = {}
    positions: Dict[str, int] = {}
    rejected: List[Dict[str, Any]] = []

    def reject(task_id: str | None, reason: str, position: int | None = None) -> None:
        payload: Dict[str, Any] = {"id": task_id or None, "reason": reason}
        if position is not None:
            payload["position"] = position
        rejected.append(payload)

    for position, item in enumerate(raw, start=1):
        task_id = _text(item.get("id")) if isinstance(item, dict) else ""
        if task_id and counts.get(task_id, 0) > 1:
            reject(task_id, f"TASK_ID_DUPLICATE:{task_id}", position)
            continue
        normalized, error = _validate_field_shape(
            item, position=position, grounding=ground_store,
        )
        if error is not None:
            reject(task_id or None, error, position)
            continue
        assert normalized is not None
        candidates[normalized["id"]] = normalized
        positions[normalized["id"]] = position

    while True:
        available = set(current_by_id) | set(candidates)
        missing = [
            task_id for task_id, item in candidates.items()
            if item.get("parent_id") is not None and item.get("parent_id") not in available
        ]
        if not missing:
            break
        for task_id in missing:
            parent_id = candidates[task_id].get("parent_id")
            reject(task_id, f"TASK_PARENT_UNKNOWN:{task_id}:{parent_id}", positions.get(task_id))
            candidates.pop(task_id, None)

    while candidates:
        tentative = {key: dict(value) for key, value in current_by_id.items()}
        tentative.update({key: dict(value) for key, value in candidates.items()})
        cycles = _cycle_ids(tentative)
        cycle_updates = sorted(cycles & set(candidates))
        if cycle_updates:
            for task_id in cycle_updates:
                reject(task_id, f"TASK_PARENT_CYCLE:{task_id}", positions.get(task_id))
                candidates.pop(task_id, None)
            continue

        invalid_parents = []
        for task_id, item in candidates.items():
            if item.get("status") != "completed":
                continue
            open_children = _open_children(tentative, task_id)
            if open_children:
                invalid_parents.append((task_id, open_children))
        if not invalid_parents:
            break
        for task_id, open_children in invalid_parents:
            reject(
                task_id,
                f"TASK_COMPLETED_WITH_OPEN_CHILDREN:{task_id}:" + ",".join(open_children),
                positions.get(task_id),
            )
            candidates.pop(task_id, None)

    accepted: List[Dict[str, Any]] = []
    for task_id, normalized in sorted(candidates.items(), key=lambda pair: positions[pair[0]]):
        current = current_by_id.get(task_id)
        changed = current != normalized
        current_by_id[task_id] = dict(normalized)
        if task_id not in order:
            order.append(task_id)
        accepted.append({
            "id": task_id,
            "changed": bool(changed),
            "status": normalized["status"],
            "parent_id": normalized["parent_id"],
            "grounding_ids": list(normalized["grounding_ids"]),
        })

    return [dict(current_by_id[task_id]) for task_id in order], accepted, rejected


def validate_task_state(tasks: Any) -> List[Dict[str, Any]]:
    if not isinstance(tasks, list) or not all(isinstance(item, dict) for item in tasks):
        raise ValueError("TASK_STATE_INCOMPATIBLE")
    # Persisted Material references cannot be revalidated without Observation;
    # shape/graph checks use those ids as an allowed physical set.
    grounding = {
        ref: {"id": ref}
        for item in tasks if isinstance(item, dict)
        for ref in item.get("grounding_ids") or []
        if isinstance(ref, str) and ref.strip()
    }
    state, accepted, rejected = apply_task_updates(tasks, previous=[], grounding=grounding)
    if rejected or len(accepted) != len(tasks) or state != tasks:
        raise ValueError("TASK_STATE_INCOMPATIBLE")
    return [dict(item) for item in state]


def task_grounding_ids(tasks: Sequence[Dict[str, Any]] | None) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in tasks or []:
        if not isinstance(item, dict) or item.get("status") != "completed":
            continue
        for grounding_id in item.get("grounding_ids") or []:
            value = _text(grounding_id)
            if value and value not in seen:
                seen.add(value)
                result.append(value)
    return result


def task_state_view(tasks: Sequence[Dict[str, Any]] | None) -> Dict[str, Any]:
    canonical = [dict(item) for item in (tasks or []) if isinstance(item, dict)]
    by_parent: Dict[str | None, List[str]] = {}
    for item in canonical:
        parent = item.get("parent_id") if isinstance(item.get("parent_id"), str) else None
        by_parent.setdefault(parent, []).append(str(item.get("id") or ""))

    open_tasks = [item for item in canonical if item.get("status") == "open"]
    closed_tasks = [item for item in canonical if item.get("status") in {"completed", "dropped"}]
    return {
        "tasks": canonical,
        "open_count": len(open_tasks),
        "closed_count": len(closed_tasks),
        "root_ids": list(by_parent.get(None, [])),
        "ready_for_final": bool(canonical) and not open_tasks,
    }
