#!/usr/bin/env python3
"""Canonical persisted continuation contract.

Continuations preserve physical execution state across human supervision. Main
owns when user input is required and what response choices mean. Runtime owns
persistence, identity, lifetime and exact resumption mechanics.
"""
from __future__ import annotations

import re
from typing import Any, Dict

PENDING_SCHEMA_VERSION = "4"

_BASE_FIELDS = {
    "pending_schema_version",
    "continuation_kind",
    "question",
    "session",
}
_PERSISTED_FIELDS = {"id", "created_at", "expires_at", "provider_context_hash"}
_KIND_FIELDS = {
    "capability_confirmation": {"capability", "provider", "confirmation_id"},
    "await_user": {"reason", "options"},
}


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_options(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError("PENDING_SCHEMA_INVALID")
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "label"}:
            raise ValueError("PENDING_SCHEMA_INVALID")
        option_id = item.get("id")
        label = item.get("label")
        if not _non_empty_text(option_id) or len(option_id.strip()) > 80:
            raise ValueError("PENDING_SCHEMA_INVALID")
        option_id = option_id.strip()
        if re.fullmatch(r"[A-Za-z0-9._-]+", option_id) is None or option_id in seen:
            raise ValueError("PENDING_SCHEMA_INVALID")
        if not _non_empty_text(label) or len(label.strip()) > 200:
            raise ValueError("PENDING_SCHEMA_INVALID")
        seen.add(option_id)
        normalized.append({"id": option_id, "label": label.strip()})
    return normalized


def validate_pending_continuation(value: Any, *, persisted: bool = False) -> Dict[str, Any]:
    """Validate the one canonical pending-continuation object.

    ``persisted=False`` validates the Core-produced envelope. ``persisted=True``
    additionally requires Runtime identity/lifetime metadata. No aliases or
    migration from earlier pending shapes are accepted.
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

    if kind == "capability_confirmation":
        if not _non_empty_text(value.get("capability")) or not _non_empty_text(value.get("provider")) or not _non_empty_text(value.get("confirmation_id")):
            raise ValueError("PENDING_SCHEMA_INVALID")
    else:
        reason = value.get("reason")
        if not _non_empty_text(reason) or len(reason.strip()) > 500:
            raise ValueError("PENDING_SCHEMA_INVALID")
        _validate_options(value.get("options"))

    if persisted:
        if not isinstance(value.get("id"), str) or not value["id"].strip():
            raise ValueError("PENDING_SCHEMA_INVALID")
        if not _non_empty_text(value.get("created_at")):
            raise ValueError("PENDING_SCHEMA_INVALID")
        expires_at = value.get("expires_at")
        if kind == "capability_confirmation":
            if not _non_empty_text(expires_at):
                raise ValueError("PENDING_SCHEMA_INVALID")
        elif expires_at is not None:
            raise ValueError("PENDING_SCHEMA_INVALID")
        context_hash = value.get("provider_context_hash")
        if context_hash is not None and not _non_empty_text(context_hash):
            raise ValueError("PENDING_SCHEMA_INVALID")

    return value
