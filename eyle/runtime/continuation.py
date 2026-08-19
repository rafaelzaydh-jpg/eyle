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

PENDING_SCHEMA_VERSION = "16-ecc"

_BASE_FIELDS = {
    "pending_schema_version",
    "continuation_kind",
    "question",
    "session",
    "execution_state",
}
_PERSISTED_FIELDS = {"id", "created_at", "expires_at", "provider_identity_hash"}
_KIND_FIELDS = {
    "capability_confirmation": {"capability", "provider", "confirmation_id"},
    "semantic_choice": {"interaction_id", "options", "allow_free_text"},
    "recoverable_execution": {"checkpoint_reason", "resume_hint"},
}


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


_CONFIRM_CONTROL = re.compile(
    r"^\s*(?:sim|aceitar|aceito|aprovar|aprovo|confirmar|confirme|confirmo|aplicar|aplique)"
    r"(?:\s+[0-9A-Fa-f]{4})?\s*[.!]?\s*$", re.IGNORECASE,
)
_CANCEL_CONTROL = re.compile(
    r"^\s*(?:não|nao|recusar|recuso|rejeitar|rejeito|cancelar|cancele|cancela)"
    r"(?:\s+[0-9A-Fa-f]{4})?\s*[.!]?\s*$", re.IGNORECASE,
)
_EXPLICIT_CONTROL = re.compile(
    r"^\s*(?:(?:sim|não|nao|aceitar|aceito|aprovar|aprovo|recusar|recuso|rejeitar|rejeito|confirmar|confirme|confirmo|aplicar|aplique|cancelar|cancele|cancela)"
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



def resolve_semantic_choice(value: Any, pending: Dict[str, Any]) -> str | None:
    """Resolve an explicit option/index, or free text when Main allowed it."""
    if not isinstance(pending, dict) or pending.get("continuation_kind") != "semantic_choice":
        return None
    text = str(value or "").strip()
    if not text:
        return None
    options = [str(item).strip() for item in pending.get("options") or []]
    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(options):
            return options[index]
    folded = text.casefold()
    for option in options:
        if option.casefold() == folded:
            return option
    if bool(pending.get("allow_free_text")):
        return text
    return None


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
    if persisted and kind == "recoverable_execution":
        expected = expected | {"checkpoint_generation"}
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
    elif kind == "semantic_choice":
        if not _non_empty_text(value.get("interaction_id")) or not isinstance(value.get("allow_free_text"), bool):
            raise ValueError("PENDING_SCHEMA_INVALID")
        options = value.get("options")
        if not isinstance(options, list) or len(options) < 2 or not all(_non_empty_text(item) for item in options):
            raise ValueError("PENDING_SCHEMA_INVALID")
        if len({str(item).strip().casefold() for item in options}) != len(options):
            raise ValueError("PENDING_SCHEMA_INVALID")
    elif kind == "recoverable_execution":
        if value.get("checkpoint_reason") not in {"budget_salvage", "stalled_recoverable"}:
            raise ValueError("PENDING_SCHEMA_INVALID")
        if not _non_empty_text(value.get("resume_hint")):
            raise ValueError("PENDING_SCHEMA_INVALID")

    if persisted:
        if not isinstance(value.get("id"), str) or not value["id"].strip():
            raise ValueError("PENDING_SCHEMA_INVALID")
        if not _non_empty_text(value.get("created_at")):
            raise ValueError("PENDING_SCHEMA_INVALID")
        expires_at = value.get("expires_at")
        if kind in {"capability_confirmation", "semantic_choice"}:
            if not _non_empty_text(expires_at):
                raise ValueError("PENDING_SCHEMA_INVALID")
        identity_hash = value.get("provider_identity_hash")
        if identity_hash is not None and not _non_empty_text(identity_hash):
            raise ValueError("PENDING_SCHEMA_INVALID")
        if kind == "recoverable_execution":
            if not _non_empty_text(identity_hash):
                raise ValueError("PENDING_SCHEMA_INVALID")
            generation = value.get("checkpoint_generation")
            if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
                raise ValueError("PENDING_SCHEMA_INVALID")

    return value
