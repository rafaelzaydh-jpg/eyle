"""Universal observation/result contract for Runtime capabilities.

Rev5.7 introduces a small domain-neutral boundary between tools and AgentSession.
Tools may expose objective observations, coverage, unresolved frontiers and
opaque continuation handles without teaching the Runtime domain semantics.

Handles in Rev5.7 address *observation snapshots*. They are stable within the
workspace epoch that produced them. A later revision may add live resource
handles, but the core deliberately does not pretend snapshot data is live state.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

_EFFECT_CLASSES = {"observe", "execute", "mutate"}


def normalize_effect(value: Any) -> str:
    effect = str(value or "observe").strip().lower()
    return effect if effect in _EFFECT_CLASSES else "observe"


def _json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def handle_store(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    value = ledger.setdefault("handles", {}) if isinstance(ledger, dict) else {}
    return value if isinstance(value, dict) else {}


def register_snapshot_handle(
    store: Dict[str, Dict[str, Any]], *, kind: str, payload: Any,
    workspace_epoch: int, source_tool: str, description: str = "",
    page_size: int = 12, offset: int = 0,
) -> Dict[str, Any]:
    """Register one deterministic opaque handle for an observation snapshot."""
    kind = str(kind or "continuation").strip() or "continuation"
    page_size = max(1, min(100, int(page_size or 12)))
    offset = max(0, int(offset or 0))
    identity = {
        "kind": kind, "payload": payload, "workspace_epoch": int(workspace_epoch or 0),
        "source_tool": str(source_tool or ""), "offset": offset, "page_size": page_size,
    }
    handle_id = f"handle:{kind}:{_json_hash(identity)[:16]}"
    store[handle_id] = {
        "id": handle_id, "kind": kind, "payload": copy.deepcopy(payload),
        "workspace_epoch": int(workspace_epoch or 0), "source_tool": str(source_tool or ""),
        "description": str(description or "")[:240], "page_size": page_size, "offset": offset,
    }
    return {
        "id": handle_id, "kind": kind, "source_tool": str(source_tool or ""),
        **({"description": str(description)[:240]} if description else {}),
    }


def _paged_payload(entry: Dict[str, Any]) -> Tuple[Any, int, int, int]:
    payload = copy.deepcopy(entry.get("payload"))
    offset = max(0, int(entry.get("offset") or 0))
    page_size = max(1, int(entry.get("page_size") or 12))
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = list(payload.get("items") or [])
        start = min(offset, len(items)); end = min(len(items), start + page_size)
        materialized = dict(payload)
        materialized["items"] = copy.deepcopy(items[start:end])
        return materialized, start, end, len(items)
    if isinstance(payload, list):
        start = min(offset, len(payload)); end = min(len(payload), start + page_size)
        return copy.deepcopy(payload[start:end]), start, end, len(payload)
    return payload, 0, 1 if payload is not None else 0, 1 if payload is not None else 0


def materialize_snapshot_handle(
    store: Dict[str, Dict[str, Any]], handle_id: str, *, workspace_epoch: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Materialize one bounded page from a handle without domain interpretation."""
    entry = store.get(str(handle_id or "")) if isinstance(store, dict) else None
    if not isinstance(entry, dict):
        return None, "HANDLE_NOT_FOUND"
    if int(entry.get("workspace_epoch") or 0) != int(workspace_epoch or 0):
        return None, "HANDLE_STALE"
    payload, start, end, total = _paged_payload(entry)
    result: Dict[str, Any] = {
        "handle": str(handle_id), "kind": entry.get("kind"), "source_tool": entry.get("source_tool"),
        "payload": payload,
        "coverage": {"materialized_start": start, "materialized_end": end, "total_items": total},
        "frontiers": [], "handles": [],
    }
    if end < total:
        next_handle = register_snapshot_handle(
            store, kind=str(entry.get("kind") or "continuation"), payload=entry.get("payload"),
            workspace_epoch=int(workspace_epoch or 0), source_tool=str(entry.get("source_tool") or ""),
            description=str(entry.get("description") or ""), page_size=int(entry.get("page_size") or 12), offset=end,
        )
        result["handles"] = [next_handle]
        result["frontiers"] = [{
            "kind": "continuation_not_materialized", "at": str(handle_id),
            "reason": f"{total - end} item(s) remain behind a continuation handle",
            "handle": next_handle["id"],
        }]
    return result, None


def persisted_handles(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    store = handle_store(ledger)
    return {
        str(key): {
            field: copy.deepcopy(value.get(field))
            for field in ("id", "kind", "payload", "workspace_epoch", "source_tool", "description", "page_size", "offset")
            if value.get(field) is not None
        }
        for key, value in store.items() if isinstance(value, dict)
    }


def result_observation_fields(
    *, observations: Optional[Iterable[Dict[str, Any]]] = None,
    coverage: Optional[Dict[str, Any]] = None,
    frontiers: Optional[Iterable[Dict[str, Any]]] = None,
    handles: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return canonical optional observation fields for the tool result envelope."""
    return {
        "observations": [copy.deepcopy(item) for item in (observations or []) if isinstance(item, dict)],
        "coverage": copy.deepcopy(coverage) if isinstance(coverage, dict) else {},
        "frontiers": [copy.deepcopy(item) for item in (frontiers or []) if isinstance(item, dict)],
        "handles": [copy.deepcopy(item) for item in (handles or []) if isinstance(item, dict)],
    }
