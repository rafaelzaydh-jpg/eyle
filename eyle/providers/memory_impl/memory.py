"""Memory Kernel public surface for Eyle.

The model owns meaning. The kernel owns persistent memory state.

This module intentionally stays small: semantic content, tags, region names and
relation labels come from Main. Physical persistence lives in memory_store;
bounded materialization lives in memory_navigation. Observation, Tasks and
Investigation remain separate semantic systems.
"""
from __future__ import annotations

from typing import Any, Iterable

from eyle.providers.memory_impl.memory_navigation import activate_memory, continue_memory_view
from eyle.providers.memory_impl.memory_store import (
    MEMORY_SCHEMA_VERSION,
    apply_operations,
    history_records,
    memory_record,
)


def apply_memory_changeset(
    base_dir: str,
    project_root: str,
    operations: Iterable[dict[str, Any]],
    *,
    changeset_id: str | None = None,
) -> dict[str, Any]:
    """Apply one atomic semantic ChangeSet proposed by Main."""
    return apply_operations(base_dir, project_root, operations, changeset_id=changeset_id)


def memory_history(
    base_dir: str,
    project_root: str,
    entity_id: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return append-only physical history for one memory/relation identity."""
    return history_records(base_dir, project_root, entity_id, limit=limit)


__all__ = [
    "MEMORY_SCHEMA_VERSION",
    "activate_memory",
    "continue_memory_view",
    "apply_memory_changeset",
    "memory_history",
    "memory_record",
]
