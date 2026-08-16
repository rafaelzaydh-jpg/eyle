#!/usr/bin/env python3
"""Canonical persisted continuation contract.

Continuations preserve physical execution state across Runtime-owned human
confirmation gates. ECC has no model-authored await-user action. Runtime owns
persistence, identity, lifetime and exact resumption mechanics.
"""
from __future__ import annotations

from typing import Any, Dict
import re

from eyle.runtime.execution_context import validate_execution_continuity_state

PENDING_SCHEMA_VERSION = "11-ecc"

_BASE_FIELDS = {
    "pending_schema_version",
    "continuation_kind",
    "question",
    "session",
    "execution_state",
}
_PERSISTED_FIELDS = {"id", "created_at", "expires_at", "provider_context_hash"}
_KIND_FIELDS = {
    "capability_confirmation": {"capability", "provider", "confirmation_id"},
    }


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


_CONFIRM_CONTROL = re.compile(
    r"^\s*(?:sim|confirmar|confirme|confirmo|aplicar|aplique)"
    r"(?:\s+[0-9A-Fa-f]{4})?\s*[.!]?\s*$", re.IGNORECASE,
)
_CANCEL_CONTROL = re.compile(
    r"^\s*(?:não|nao|cancelar|cancele|cancela)"
    r"(?:\s+[0-9A-Fa-f]{4})?\s*[.!]?\s*$", re.IGNORECASE,
)
_EXPLICIT_CONTROL = re.compile(
    r"^\s*(?:(?:sim|não|nao|confirmar|confirme|confirmo|aplicar|aplique|cancelar|cancele|cancela)"
    r"(?:\s+[0-9A-Fa-f]{4})?)\s*[.!]?\s*$", re.IGNORECASE,
)


def confirmation_control(value: Any) -> str | None:
    """Classify only explicit runtime confirmation controls; never infer intent."""
    text = str(value or "")
    if _CANCEL_CONTROL.fullmatch(text):
        return "cancelar"
    if _CONFIRM_CONTROL.fullmatch(text):
        return "aplicar"
    return None


def is_explicit_confirmation_control(value: Any) -> bool:
    return _EXPLICIT_CONTROL.fullmatch(str(value or "")) is not None



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
    try:
        validate_execution_continuity_state(value.get("execution_state"))
    except ValueError as exc:
        raise ValueError("PENDING_EXECUTION_STATE_INVALID") from exc

    if kind == "capability_confirmation":
        if not _non_empty_text(value.get("capability")) or not _non_empty_text(value.get("provider")) or not _non_empty_text(value.get("confirmation_id")):
            raise ValueError("PENDING_SCHEMA_INVALID")

    if persisted:
        if not isinstance(value.get("id"), str) or not value["id"].strip():
            raise ValueError("PENDING_SCHEMA_INVALID")
        if not _non_empty_text(value.get("created_at")):
            raise ValueError("PENDING_SCHEMA_INVALID")
        expires_at = value.get("expires_at")
        if kind == "capability_confirmation":
            if not _non_empty_text(expires_at):
                raise ValueError("PENDING_SCHEMA_INVALID")
        context_hash = value.get("provider_context_hash")
        if context_hash is not None and not _non_empty_text(context_hash):
            raise ValueError("PENDING_SCHEMA_INVALID")

    return value
