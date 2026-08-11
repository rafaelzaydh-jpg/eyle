#!/usr/bin/env python3
"""Canonical pending-continuation contract.

Pending continuations are persisted Runtime/Core state, so they use one exact
English schema. The Runtime may add security/lifetime metadata, but it never
renames, aliases, defaults, or migrates Core continuation fields.
"""
from __future__ import annotations

from typing import Any, Dict

PENDING_SCHEMA_VERSION = "1"

_BASE_FIELDS = {
    "pending_schema_version",
    "continuation_kind",
    "question",
    "session",
}
_PERSISTED_FIELDS = {"id", "created_at", "expires_at", "project_hash"}
_KIND_FIELDS = {
    "write_confirmation": {"transaction_id"},
    "user_input": {"clarification"},
}


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_pending_continuation(value: Any, *, persisted: bool = False) -> Dict[str, Any]:
    """Validate and return the one canonical pending-continuation object.

    ``persisted=False`` validates the Core-produced envelope. ``persisted=True``
    additionally requires the Runtime-owned identity/lifetime metadata. Both
    modes reject missing fields, unknown fields, aliases and old shapes.
    """
    if not isinstance(value, dict):
        raise ValueError("PENDING_SCHEMA_INVALID")
    if value.get("pending_schema_version") != PENDING_SCHEMA_VERSION:
        raise ValueError("PENDING_SCHEMA_INCOMPATIBLE")

    kind = value.get("continuation_kind")
    if kind not in _KIND_FIELDS:
        raise ValueError("PENDING_SCHEMA_INVALID")
    expected = _BASE_FIELDS | _KIND_FIELDS[kind] | (_PERSISTED_FIELDS if persisted else set())
    if set(value) != expected:
        raise ValueError("PENDING_SCHEMA_INVALID")

    if not _non_empty_text(value.get("question")):
        raise ValueError("PENDING_SCHEMA_INVALID")
    if not isinstance(value.get("session"), dict):
        raise ValueError("PENDING_SCHEMA_INVALID")

    if kind == "write_confirmation":
        if not _non_empty_text(value.get("transaction_id")):
            raise ValueError("PENDING_SCHEMA_INVALID")
    else:
        clarification = value.get("clarification")
        if not isinstance(clarification, dict) or set(clarification) != {"question", "missing_information"}:
            raise ValueError("PENDING_SCHEMA_INVALID")
        if not _non_empty_text(clarification.get("question")) or not _non_empty_text(clarification.get("missing_information")):
            raise ValueError("PENDING_SCHEMA_INVALID")

    if persisted:
        if not isinstance(value.get("id"), str) or not value["id"].strip():
            raise ValueError("PENDING_SCHEMA_INVALID")
        for field in ("created_at", "expires_at"):
            if not _non_empty_text(value.get(field)):
                raise ValueError("PENDING_SCHEMA_INVALID")
        project_hash = value.get("project_hash")
        if project_hash is not None and not _non_empty_text(project_hash):
            raise ValueError("PENDING_SCHEMA_INVALID")

    return value
