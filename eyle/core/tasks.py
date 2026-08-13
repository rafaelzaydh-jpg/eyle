"""Main-owned recursive task state for Eyle 2.7.5 Rev1.3.4.

Tasks are commitments, not observations and not investigations. Main owns the
semantic decision to create, revise, complete or drop them. Runtime only
validates structural integrity and persists the resulting state.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

TASK_STATUSES = {"open", "completed", "dropped"}
_TASK_FIELDS = {"id", "parent_id", "description", "status", "result"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _validate_field_shape(item: Any, *, position: int) -> tuple[Dict[str, Any] | None, str | None]:
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

    return {
        "id": task_id,
        "parent_id": parent_id,
        "description": description,
        "status": status,
        "result": result,
    }, None


def _cycle_ids(tasks: Dict[str, Dict[str, Any]]) -> set[str]:
    """Return task IDs participating in a parent cycle."""
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


def apply_task_updates(
    raw: Any,
    *,
    previous: Sequence[Dict[str, Any]] | None = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Apply Main task deltas while preserving Runtime-owned structure.

    Omitted tasks stay unchanged. Updates may create parents and children in the
    same batch. Invalid siblings do not roll back independent valid updates.
    Runtime never infers completion from children, tools or observations.
    """
    canonical = [dict(item) for item in (previous or []) if isinstance(item, dict)]
    if not isinstance(raw, list):
        return canonical, [], [{"id": None, "reason": "TASK_UPDATES_LIST_REQUIRED"}]

    current_by_id: Dict[str, Dict[str, Any]] = {
        _text(item.get("id")): dict(item)
        for item in canonical
        if _text(item.get("id"))
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
        normalized, error = _validate_field_shape(item, position=position)
        if error is not None:
            reject(task_id or None, error, position)
            continue
        assert normalized is not None
        candidates[normalized["id"]] = normalized
        positions[normalized["id"]] = position

    # Remove candidates whose parent cannot exist after this batch. Repeat to
    # cascade failures from invalid newly-created parents into their children.
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

    # Validate the resulting graph. Existing canonical state is already valid;
    # cycles here necessarily involve one or more incoming updates. Reject every
    # updated member of each cycle and then re-check child parent availability.
    while candidates:
        tentative = {key: dict(value) for key, value in current_by_id.items()}
        tentative.update({key: dict(value) for key, value in candidates.items()})
        cycles = _cycle_ids(tentative)
        cycle_updates = sorted(cycles & set(candidates))
        if not cycle_updates:
            break
        for task_id in cycle_updates:
            reject(task_id, f"TASK_PARENT_CYCLE:{task_id}", positions.get(task_id))
            candidates.pop(task_id, None)
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
        })

    return [dict(current_by_id[task_id]) for task_id in order], accepted, rejected


def validate_task_state(tasks: Any) -> List[Dict[str, Any]]:
    """Validate one persisted canonical task list without migration or repair."""
    if not isinstance(tasks, list) or not all(isinstance(item, dict) for item in tasks):
        raise ValueError("TASK_STATE_INCOMPATIBLE")
    state, accepted, rejected = apply_task_updates(tasks, previous=[])
    if rejected or len(accepted) != len(tasks) or state != tasks:
        raise ValueError("TASK_STATE_INCOMPATIBLE")
    return [dict(item) for item in state]


def task_state_view(tasks: Sequence[Dict[str, Any]] | None) -> Dict[str, Any]:
    """Compact current task state; no semantic prioritization is performed."""
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
        "all_known_tasks_closed": bool(canonical) and not open_tasks,
    }
