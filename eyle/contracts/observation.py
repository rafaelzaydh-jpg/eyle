"""Universal physical observation/result contract for Eyle 2.7.5 Rev1.4.3.

Capabilities own how reality is executed, identified and projected into Material,
Coverage and Frontier. Observation stores those physical facts generically.
Runtime-private snapshots retain large continuation payloads once; lightweight
handles only point at a snapshot plus a cursor.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Iterable, Optional, Tuple

_EFFECT_CLASSES = {"observe", "execute", "mutate"}


class CoverageContractError(ValueError):
    """Capability returned non-canonical physical Coverage."""


def normalize_coverage(value: Any, *, allow_empty: bool = True) -> Dict[str, Any]:
    """Validate and canonicalize the universal physical Coverage contract.

    Coverage describes only mechanically observed scope. Non-empty Coverage must
    contain ``scope``, ``examined``, ``complete`` and ``boundaries`` with stable
    physical types. Unknown top-level fields are rejected so capabilities cannot
    silently invent parallel Coverage dialects. Domain-specific facts belong in
    ``facts``.
    """
    if value in (None, {}):
        if allow_empty:
            return {}
        raise CoverageContractError("coverage is required")
    if not isinstance(value, dict):
        raise CoverageContractError("coverage must be an object")
    required = {"scope", "examined", "complete", "boundaries"}
    missing = sorted(required - set(value))
    if missing:
        raise CoverageContractError("coverage missing field(s): " + ", ".join(missing))
    allowed = required | {"facts"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise CoverageContractError("coverage has unknown field(s): " + ", ".join(unknown))
    if not isinstance(value.get("scope"), dict):
        raise CoverageContractError("coverage.scope must be an object")
    if not isinstance(value.get("examined"), dict):
        raise CoverageContractError("coverage.examined must be an object")
    if not isinstance(value.get("complete"), bool):
        raise CoverageContractError("coverage.complete must be boolean")
    boundaries = value.get("boundaries")
    if not isinstance(boundaries, list) or any(not isinstance(item, dict) for item in boundaries):
        raise CoverageContractError("coverage.boundaries must be an array of objects")
    facts = value.get("facts")
    if facts is not None and not isinstance(facts, dict):
        raise CoverageContractError("coverage.facts must be an object when present")
    result = {
        "scope": copy.deepcopy(value["scope"]),
        "examined": copy.deepcopy(value["examined"]),
        "complete": value["complete"],
        "boundaries": [copy.deepcopy(item) for item in boundaries],
    }
    if facts:
        result["facts"] = copy.deepcopy(facts)
    return result


def normalize_effect(value: Any) -> str:
    effect = str(value or "observe").strip().lower()
    return effect if effect in _EFFECT_CLASSES else "observe"


def _json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def handle_store(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    value = ledger.setdefault("handles", {}) if isinstance(ledger, dict) else {}
    return value if isinstance(value, dict) else {}


def snapshot_store(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    value = ledger.setdefault("snapshots", {}) if isinstance(ledger, dict) else {}
    return value if isinstance(value, dict) else {}


def _register_handle_for_snapshot(
    ledger: Dict[str, Any], *, snapshot_id: str, kind: str, reality_epoch: int,
    source_capability: str, description: str, page_size: int, offset: int,
) -> Dict[str, Any]:
    handles = handle_store(ledger)
    identity = {
        "snapshot_id": str(snapshot_id), "reality_epoch": int(reality_epoch or 0),
        "offset": int(offset), "page_size": int(page_size),
    }
    handle_id = f"handle:{kind}:{_json_hash(identity)[:16]}"
    handles[handle_id] = {
        "id": handle_id, "kind": str(kind), "snapshot_id": str(snapshot_id),
        "reality_epoch": int(reality_epoch or 0), "source_capability": str(source_capability or ""),
        "description": str(description or "")[:240], "page_size": int(page_size), "offset": int(offset),
    }
    return {
        "id": handle_id, "kind": str(kind), "source_capability": str(source_capability or ""),
        **({"description": str(description)[:240]} if description else {}),
    }


def register_snapshot_handle(
    ledger: Dict[str, Any], *, kind: str, payload: Any,
    reality_epoch: int, source_capability: str, description: str = "",
    page_size: int = 12, offset: int = 0,
) -> Dict[str, Any]:
    """Store one immutable snapshot payload and return a lightweight cursor handle."""
    kind = str(kind or "continuation").strip() or "continuation"
    page_size = max(1, min(100, int(page_size or 12)))
    offset = max(0, int(offset or 0))
    snapshot_identity = {
        "kind": kind, "payload": payload, "reality_epoch": int(reality_epoch or 0),
        "source_capability": str(source_capability or ""),
    }
    snapshot_id = f"snap:{kind}:{_json_hash(snapshot_identity)[:16]}"
    snapshots = snapshot_store(ledger)
    if snapshot_id not in snapshots:
        snapshots[snapshot_id] = {
            "id": snapshot_id, "kind": kind, "payload": copy.deepcopy(payload),
            "reality_epoch": int(reality_epoch or 0), "source_capability": str(source_capability or ""),
            "description": str(description or "")[:240],
        }
    return _register_handle_for_snapshot(
        ledger, snapshot_id=snapshot_id, kind=kind, reality_epoch=reality_epoch,
        source_capability=source_capability, description=description, page_size=page_size, offset=offset,
    )


def _paged_payload(payload: Any, *, offset: int, page_size: int) -> Tuple[Any, int, int, int]:
    """Copy only the requested page, never the retained snapshot payload."""
    offset = max(0, int(offset or 0))
    page_size = max(1, int(page_size or 12))
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = payload.get("items") or []
        start = min(offset, len(items)); end = min(len(items), start + page_size)
        # Metadata is immutable snapshot state and can be shared shallowly. Only
        # the materialized item slice is detached for downstream capability use.
        materialized = {key: value for key, value in payload.items() if key != "items"}
        materialized["items"] = copy.deepcopy(items[start:end])
        return materialized, start, end, len(items)
    if isinstance(payload, list):
        start = min(offset, len(payload)); end = min(len(payload), start + page_size)
        return copy.deepcopy(payload[start:end]), start, end, len(payload)
    return copy.deepcopy(payload), 0, 1 if payload is not None else 0, 1 if payload is not None else 0


def materialize_snapshot_handle(
    ledger: Dict[str, Any], handle_id: str, *, reality_epoch: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Materialize one bounded page without copying the retained snapshot into the next handle."""
    handle = handle_store(ledger).get(str(handle_id or ""))
    if not isinstance(handle, dict):
        return None, "HANDLE_NOT_FOUND"
    if int(handle.get("reality_epoch") or 0) != int(reality_epoch or 0):
        return None, "HANDLE_STALE"
    snapshot = snapshot_store(ledger).get(str(handle.get("snapshot_id") or ""))
    if not isinstance(snapshot, dict):
        return None, "SNAPSHOT_NOT_FOUND"
    if int(snapshot.get("reality_epoch") or 0) != int(reality_epoch or 0):
        return None, "HANDLE_STALE"

    page_size = max(1, int(handle.get("page_size") or 12))
    offset = max(0, int(handle.get("offset") or 0))
    payload, start, end, total = _paged_payload(snapshot.get("payload"), offset=offset, page_size=page_size)
    complete = end >= total
    coverage = {
        "scope": {"kind": "snapshot_continuation", "source_capability": handle.get("source_capability")},
        "examined": {"item_start": start, "item_end": end, "items": max(0, end - start)},
        "complete": bool(complete),
        "boundaries": [],
        "facts": {
            "snapshot_exhausted": bool(complete),
            "total_items": total,
            "remaining_items": max(0, total - end),
        },
    }
    result: Dict[str, Any] = {
        "handle": str(handle_id), "kind": handle.get("kind"), "source_capability": handle.get("source_capability"),
        "payload": payload, "coverage": coverage, "frontiers": [],
    }
    if not complete:
        next_handle = _register_handle_for_snapshot(
            ledger, snapshot_id=str(handle.get("snapshot_id") or ""),
            kind=str(handle.get("kind") or "continuation"), reality_epoch=int(reality_epoch or 0),
            source_capability=str(handle.get("source_capability") or ""), description=str(handle.get("description") or ""),
            page_size=page_size, offset=end,
        )
        result["frontiers"] = [{
            "kind": "continuation_not_materialized", "at": str(handle.get("source_capability") or "continuation"),
            "count": max(0, total - end),
            "reason": f"{max(0, total - end)} item(s) remain behind a continuation handle",
            "handle": next_handle["id"],
        }]
    return result, None


def release_snapshot_handle(ledger: Dict[str, Any], handle_id: str) -> None:
    """Release a consumed cursor and garbage-collect an unreferenced snapshot."""
    handles = handle_store(ledger)
    handle = handles.pop(str(handle_id or ""), None)
    if not isinstance(handle, dict):
        return
    snapshot_id = str(handle.get("snapshot_id") or "")
    if snapshot_id and not any(
        isinstance(item, dict) and str(item.get("snapshot_id") or "") == snapshot_id
        for item in handles.values()
    ):
        snapshot_store(ledger).pop(snapshot_id, None)


def persisted_handles(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(key): {
            field: copy.deepcopy(value.get(field))
            for field in ("id", "kind", "snapshot_id", "reality_epoch", "source_capability", "description", "page_size", "offset")
            if value.get(field) is not None
        }
        for key, value in handle_store(ledger).items() if isinstance(value, dict)
    }


def persisted_snapshots(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(key): {
            field: copy.deepcopy(value.get(field))
            for field in ("id", "kind", "payload", "reality_epoch", "source_capability", "description")
            if value.get(field) is not None
        }
        for key, value in snapshot_store(ledger).items() if isinstance(value, dict)
    }


def result_observation_fields(
    *, observations: Optional[Iterable[Dict[str, Any]]] = None,
    coverage: Optional[Dict[str, Any]] = None,
    frontiers: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Canonical physical observation fields shared by every capability result."""
    return {
        "observations": [copy.deepcopy(item) for item in (observations or []) if isinstance(item, dict)],
        "coverage": copy.deepcopy(coverage) if isinstance(coverage, dict) else {},
        "frontiers": [copy.deepcopy(item) for item in (frontiers or []) if isinstance(item, dict)],
    }
