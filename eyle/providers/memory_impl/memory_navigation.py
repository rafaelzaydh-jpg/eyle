"""Bounded navigation over persistent Memory Kernel state.

MemoryCoverage and MemoryFrontier are deliberately distinct contracts from
Observation Coverage/Frontier. Similar mechanics are not yet a shared
abstraction.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

from eyle.providers.memory_impl.memory_store import (
    advance_continuation,
    candidate_ids,
    load_continuation,
    memory_records,
    save_continuation,
)


MAX_MEMORY_VIEW = 30


def _view_id() -> str:
    return f"mv-{uuid.uuid4().hex[:16]}"


def _limit(value: int | None) -> int:
    return max(1, min(int(value or 12), MAX_MEMORY_VIEW))


def activate_memory(
    base_dir: str,
    project_root: str,
    *,
    ids: Iterable[str] = (),
    region: str | None = None,
    tags: Iterable[str] = (),
    text: str = "",
    related_to: Iterable[str] = (),
    limit: int = 12,
    include_inactive: bool = False,
) -> dict[str, Any]:
    page_size = _limit(limit)
    ordered, examined = candidate_ids(
        base_dir, project_root,
        ids=ids, region=region, tags=tags, text=text,
        related_to=related_to, include_inactive=include_inactive,
    )
    selected = ordered[:page_size]
    remaining = max(0, len(ordered) - len(selected))
    criteria = {
        "ids": [str(item) for item in ids or []],
        "region": str(region) if region else None,
        "tags": [str(item) for item in tags or []],
        "text": str(text or ""),
        "related_to": [str(item) for item in related_to or []],
        "include_inactive": bool(include_inactive),
    }
    frontier = None
    if remaining:
        frontier_id = save_continuation(base_dir, project_root, ordered, len(selected), criteria)
        frontier = {"id": frontier_id, "remaining": True, "remaining_count": remaining}
    coverage = {
        "kind": "memory_navigation",
        "regions": [str(region)] if region else [],
        "seed_tags": [str(item) for item in tags or []],
        "related_to": [str(item) for item in related_to or []],
        "text": str(text or ""),
        "examined": {**examined, "ordered_candidates": len(ordered), "materialized_count": len(selected)},
        "boundaries": ([{"kind": "materialization_limit", "limit": page_size, "remaining": remaining}] if remaining else []),
        "complete": remaining == 0,
    }
    return {
        "view_id": _view_id(),
        "memories": memory_records(base_dir, project_root, selected),
        "memory_coverage": coverage,
        "memory_frontier": frontier,
    }


def continue_memory_view(
    base_dir: str,
    project_root: str,
    frontier_id: str,
    *,
    limit: int = 12,
) -> dict[str, Any]:
    snapshot = load_continuation(base_dir, project_root, str(frontier_id))
    ordered = [str(item) for item in snapshot["ordered_ids"]]
    start = int(snapshot["next_offset"])
    page_size = _limit(limit)
    selected = ordered[start:start + page_size]
    next_offset = start + len(selected)
    remaining = max(0, len(ordered) - next_offset)
    advance_continuation(base_dir, project_root, str(frontier_id), next_offset, done=remaining == 0)
    criteria = dict(snapshot.get("criteria") or {})
    coverage = {
        "kind": "memory_continuation",
        "regions": [criteria["region"]] if criteria.get("region") else [],
        "seed_tags": list(criteria.get("tags") or []),
        "related_to": list(criteria.get("related_to") or []),
        "text": str(criteria.get("text") or ""),
        "examined": {"page_start": start, "materialized_count": len(selected), "ordered_candidates": len(ordered)},
        "boundaries": ([{"kind": "materialization_limit", "limit": page_size, "remaining": remaining}] if remaining else []),
        "complete": remaining == 0,
    }
    frontier = {"id": str(frontier_id), "remaining": True, "remaining_count": remaining} if remaining else None
    return {
        "view_id": _view_id(),
        "memories": memory_records(base_dir, project_root, selected),
        "memory_coverage": coverage,
        "memory_frontier": frontier,
    }
