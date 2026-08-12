"""Canonical Runtime observation state for Eyle 2.7.5 Rev1.3.

Observation owns physical tool history, replay identity, materialized grounding,
Coverage/Frontier continuity and the pending model-facing delta.  The Main LLM
never manages opaque Runtime handles and there is no second grounding ledger.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .observation_contract import persisted_handles, persisted_snapshots, release_snapshot_handle
from .text_hash import hash_texto


def empty_ledger() -> Dict[str, Any]:
    return {
        "entries": {},
        "events": [],
        "replay_count": 0,
        "pending_results": [],
        "handles": {},          # Runtime-private lightweight continuation cursors.
        "snapshots": {},        # Runtime-private immutable continuation payloads stored once.
        "frontiers": {},        # Main-visible continuation refs -> private handles.
        "materials": {},        # Canonical objectively materialized grounding.
    }


def _ledger(session: Any) -> Dict[str, Any]:
    value = getattr(session, "observation_ledger", {}) or {}
    return value if isinstance(value, dict) else {}


def _entries(session: Any) -> Dict[str, Dict[str, Any]]:
    value = _ledger(session).setdefault("entries", {})
    return value if isinstance(value, dict) else {}


def _events(session: Any) -> List[Dict[str, Any]]:
    value = _ledger(session).setdefault("events", [])
    return value if isinstance(value, list) else []


def pending_results(session: Any) -> List[Dict[str, Any]]:
    value = _ledger(session).setdefault("pending_results", [])
    return value if isinstance(value, list) else []


def set_pending_results(session: Any, results: List[Dict[str, Any]]) -> None:
    session.observation_ledger["pending_results"] = copy.deepcopy(list(results or []))


def clear_pending_results(session: Any) -> None:
    session.observation_ledger["pending_results"] = []


# ---------------------------------------------------------------------------
# Materialized grounding. Material is domain-neutral: capabilities provide a
# locator plus content identity; Observation only stores and addresses it.
# ---------------------------------------------------------------------------

def material_items(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    value = ledger.setdefault("materials", {}) if isinstance(ledger, dict) else {}
    return value if isinstance(value, dict) else {}


def _locator(item: Dict[str, Any]) -> Dict[str, Any]:
    value = item.get("locator") if isinstance(item, dict) else None
    return dict(value) if isinstance(value, dict) else {}



def register_material_candidates(ledger: Dict[str, Any], candidates: Iterable[Dict[str, Any]]) -> List[str]:
    """Register capability-produced Material using only universal physical identity.

    Required universal shape: locator + content identity. ``source_type`` and
    ``source_capability`` describe provenance but Observation never interprets them.
    """
    store = material_items(ledger)
    ids: List[str] = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        item = copy.deepcopy(candidate)
        locator = _locator(item)
        if not locator:
            continue
        content = item.get("content")
        content_hash = str(item.get("content_hash") or "").strip()
        if not content_hash and content is not None:
            content_hash = hash_texto(str(content))
        if not content_hash:
            continue
        item["content_hash"] = content_hash
        item["locator"] = locator
        source_type = str(item.get("source_type") or item.get("source_capability") or "material").strip() or "material"
        source_capability = str(item.get("source_capability") or source_type).strip() or source_type
        item["source_type"] = source_type
        item["source_capability"] = source_capability
        source_version = str(item.get("source_version") or "").strip()
        existing = next((
            material_id for material_id, record in store.items()
            if isinstance(record, dict)
            and _locator(record) == locator
            and str(record.get("source_capability") or "") == source_capability
            and str(record.get("source_version") or "") == source_version
            and str(record.get("content_hash") or "") == content_hash
        ), None)
        material_id = existing or f"mat-{len(store)+1:04d}"
        item["id"] = material_id
        store[material_id] = item
        ids.append(material_id)
    return ids


def material_index_view(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compact generic Material directory shown to Main."""
    out: List[Dict[str, Any]] = []
    for material_id, item in material_items(ledger).items():
        if not isinstance(item, dict):
            continue
        entry = {
            "id": material_id,
            "source_type": item.get("source_type"),
            "source_capability": item.get("source_capability"),
            "locator": _locator(item),
            "content_hash": item.get("content_hash"),
        }
        if item.get("query") is not None:
            entry["query"] = item.get("query")
        out.append({k: v for k, v in entry.items() if v not in (None, "", {}, [])})
    return out

def freshest_material_for_locator(
    ledger: Dict[str, Any], locator: Dict[str, Any], *, match_fields: Optional[Iterable[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Return the newest Material whose locator matches a caller-declared physical locator.

    Observation does not know what locator kinds mean. The caller/capability
    chooses which locator fields define the physical resource identity.
    """
    target = dict(locator or {})
    fields = [str(value) for value in (match_fields or target.keys()) if str(value)]
    for item in reversed(list(material_items(ledger).values())):
        if not isinstance(item, dict):
            continue
        candidate = _locator(item)
        if fields and all(candidate.get(field) == target.get(field) for field in fields):
            return item
    return None


def register_runtime_material(ledger: Dict[str, Any], item: Dict[str, Any]) -> str:
    ids = register_material_candidates(ledger, [item])
    return ids[0] if ids else ""


def seed_runtime_failure(ledger: Dict[str, Any], conversation_context: Any) -> List[Dict[str, Any]]:
    messages = list((conversation_context or {}).get("recent_messages") or []) if isinstance(conversation_context, dict) else []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        failure = message.get("write_failure")
        if not isinstance(failure, dict) or not failure:
            continue
        detail = str(failure.get("detail") or "").strip()
        if not detail:
            continue
        content_hash = hash_texto(detail)
        material_id = register_runtime_material(ledger, {
            "locator": {"kind": "runtime", "name": "write_failure"},
            "content_hash": content_hash, "content": detail,
            "source_type": "runtime_validation", "stage": failure.get("stage"),
            "error_code": failure.get("error_code"), "paths": list(failure.get("paths") or []),
            "rollback_confirmed": failure.get("rollback_confirmed"),
        })
        return [{
            "tool": "runtime_validation", "status": "success", "ok": True,
            "executed": False, "changed": False, "error_code": None,
            "detail": {"grounding_id": material_id, "source_type": "runtime_validation", "stage": failure.get("stage"), "error_code": failure.get("error_code"), "content": detail[:700]},
            "grounding_ids": [material_id] if material_id else [],
        }]
    return []


# ---------------------------------------------------------------------------
# Frontier continuity. Main sees fr-* refs; only Runtime sees handle:* values.
# ---------------------------------------------------------------------------

def frontier_store(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    value = ledger.setdefault("frontiers", {}) if isinstance(ledger, dict) else {}
    return value if isinstance(value, dict) else {}


def _frontier_id_for_handle(store: Dict[str, Dict[str, Any]], handle_id: str) -> Optional[str]:
    for frontier_id, item in store.items():
        if isinstance(item, dict) and item.get("handle") == handle_id and item.get("status") == "open":
            return frontier_id
    return None


def expose_frontiers(session: Any, tool: str, model_result: Dict[str, Any]) -> List[str]:
    """Replace opaque continuation handles with stable Main-facing Frontier refs."""
    if not isinstance(model_result, dict):
        return []
    ledger = _ledger(session)
    store = frontier_store(ledger)
    raw_frontiers = model_result.get("frontiers")
    if not isinstance(raw_frontiers, list):
        detail = model_result.get("detail")
        raw_frontiers = detail.get("frontiers") if isinstance(detail, dict) else []
    published: List[Dict[str, Any]] = []
    ids: List[str] = []
    for raw in raw_frontiers or []:
        if not isinstance(raw, dict):
            continue
        item = copy.deepcopy(raw)
        handle_id = str(item.pop("handle", "") or "").strip()
        frontier_id = None
        if handle_id:
            frontier_id = _frontier_id_for_handle(store, handle_id)
            if frontier_id is None:
                frontier_id = f"fr-{len(store)+1:04d}"
                store[frontier_id] = {
                    "id": frontier_id,
                    "handle": handle_id,
                    "workspace_epoch": int(getattr(session, "workspace_epoch", 0) or 0),
                    "source_tool": str(tool or ""),
                    "kind": item.get("kind"),
                    "at": item.get("at"),
                    "reason": item.get("reason"),
                    "count": item.get("count"),
                    "status": "open",
                }
            item["id"] = frontier_id
            ids.append(frontier_id)
        published.append(item)
    if published:
        model_result["frontiers"] = published
    else:
        model_result.pop("frontiers", None)
    model_result.pop("handles", None)
    detail = model_result.get("detail")
    if isinstance(detail, dict):
        detail.pop("handles", None)
        if published:
            detail["frontiers"] = copy.deepcopy(published)
        elif "frontiers" in detail:
            detail["frontiers"] = []
    return ids


def resolve_frontier(ledger: Dict[str, Any], frontier_id: str, *, workspace_epoch: int) -> Tuple[Optional[str], Optional[str]]:
    item = frontier_store(ledger).get(str(frontier_id or ""))
    if not isinstance(item, dict):
        return None, "FRONTIER_NOT_FOUND"
    if item.get("status") != "open":
        return None, "FRONTIER_CONSUMED"
    if int(item.get("workspace_epoch") or 0) != int(workspace_epoch or 0):
        return None, "FRONTIER_STALE"
    handle_id = str(item.get("handle") or "")
    if not handle_id:
        return None, "FRONTIER_UNAVAILABLE"
    return handle_id, None


def consume_frontier(ledger: Dict[str, Any], frontier_id: str) -> None:
    item = frontier_store(ledger).get(str(frontier_id or ""))
    if isinstance(item, dict):
        item["status"] = "consumed"
        release_snapshot_handle(ledger, str(item.get("handle") or ""))


def frontier_view(ledger: Dict[str, Any], ids: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    wanted = set(str(item) for item in (ids or []) if str(item)) if ids is not None else None
    out: List[Dict[str, Any]] = []
    for frontier_id, item in frontier_store(ledger).items():
        if not isinstance(item, dict) or (wanted is not None and frontier_id not in wanted):
            continue
        entry = {
            "id": frontier_id,
            "kind": item.get("kind"),
            "source_tool": item.get("source_tool"),
            "reason": item.get("reason"),
            "count": item.get("count"),
            "status": item.get("status"),
        }
        out.append({k: v for k, v in entry.items() if v is not None})
    return out


# ---------------------------------------------------------------------------
# Observation/replay identity and history.
# ---------------------------------------------------------------------------

def ledger_key(signature: str, workspace_epoch: int) -> str:
    return f"w{int(workspace_epoch)}:{signature}"


def lookup(session: Any, signature: Optional[str]) -> Optional[Dict[str, Any]]:
    if not signature:
        return None
    item = _entries(session).get(ledger_key(signature, getattr(session, "workspace_epoch", 0)))
    return copy.deepcopy(item) if isinstance(item, dict) else None


def result_fingerprint(result: Dict[str, Any]) -> str:
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strip_private_handles(value: Any) -> Any:
    clone = copy.deepcopy(value)
    if not isinstance(clone, dict):
        return clone
    clone.pop("handles", None)
    frontiers = clone.get("frontiers")
    if isinstance(frontiers, list):
        clone["frontiers"] = [
            {k: v for k, v in item.items() if k != "handle"}
            for item in frontiers if isinstance(item, dict)
        ]
    detail = clone.get("detail")
    if isinstance(detail, dict):
        detail.pop("handles", None)
        if isinstance(detail.get("frontiers"), list):
            detail["frontiers"] = [
                {k: v for k, v in item.items() if k != "handle"}
                for item in detail.get("frontiers") if isinstance(item, dict)
            ]
    return clone


def _append_event(session: Any, *, tool: str, arguments: Dict[str, Any], result: Dict[str, Any],
                  model_result: Dict[str, Any], observation_signature: Optional[str], status: str,
                  replay_reason: Optional[str] = None, public_arguments: Optional[Dict[str, Any]] = None,
                  public_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    events = _events(session)
    event = {
        "event_id": f"obs-{len(events)+1:04d}", "turn": int(getattr(session, "turn", 0)),
        "workspace_epoch": int(getattr(session, "workspace_epoch", 0)), "tool": str(tool),
        "arguments": copy.deepcopy(public_arguments if public_arguments is not None else arguments), "status": str(status),
        "executed": result.get("executed") is True, "ok": result.get("ok") is True,
        "error_code": result.get("error_code"), "retryable": result.get("retryable"),
        "failure_scope": result.get("failure_scope"), "failure_resource": result.get("failure_resource"),
        "observation_signature": observation_signature,
        "grounding_ids": list(model_result.get("grounding_ids") or []),
        "frontier_ids": [str(item.get("id")) for item in model_result.get("frontiers") or [] if isinstance(item, dict) and item.get("id")],
        "result": _strip_private_handles(public_result if public_result is not None else result),
    }
    if replay_reason:
        event["replay_reason"] = str(replay_reason)
    events.append(event)
    return event


def record(session: Any, signature: Optional[str], tool: str, arguments: Dict[str, Any], result: Dict[str, Any],
           model_result: Dict[str, Any], *, public_arguments: Optional[Dict[str, Any]] = None,
           public_result: Optional[Dict[str, Any]] = None) -> None:
    """Record one physical outcome without interpreting capability semantics."""
    frontier_ids = expose_frontiers(session, tool, model_result)
    _append_event(
        session, tool=tool, arguments=arguments, result=result, model_result=model_result,
        observation_signature=signature, status=str(result.get("status") or ("success" if result.get("ok") else "failed")),
        public_arguments=public_arguments, public_result=public_result,
    )
    if not signature or result.get("executed") is not True:
        return
    reusable = result.get("ok") is True or bool(result.get("observations")) or (
        result.get("retryable") is False and result.get("failure_scope") in {"request", "resource"}
    )
    if not reusable:
        return
    key = ledger_key(signature, getattr(session, "workspace_epoch", 0))
    _entries(session)[key] = {
        "observation_signature": signature,
        "workspace_epoch": int(getattr(session, "workspace_epoch", 0)),
        "tool": tool,
        "arguments": copy.deepcopy(arguments),
        "public_arguments": copy.deepcopy(public_arguments if public_arguments is not None else arguments),
        "result_fingerprint": result_fingerprint(result),
        "grounding_ids": list(model_result.get("grounding_ids") or []),
        "frontier_ids": list(frontier_ids),
        "coverage": copy.deepcopy(result.get("coverage") or {}),
        "failure_scope": result.get("failure_scope"),
        "failure_resource": result.get("failure_resource"),
        "failure_error_code": result.get("error_code") if result.get("failure_scope") else None,
        "failure_detail": str(result.get("detail") or "")[:500] if result.get("failure_scope") else None,
        "replay_result": copy.deepcopy(model_result),
        "turn": int(getattr(session, "turn", 0)),
    }


def record_replay(session: Any, entry: Dict[str, Any], model_result: Dict[str, Any], *, reason: str,
                  public_result: Optional[Dict[str, Any]] = None) -> None:
    """Count cache hits without creating a second Observation event.

    The original physical Observation remains canonical. Decision telemetry may
    still record that the Main requested cached reality again.
    """
    ledger = _ledger(session)
    ledger["replay_count"] = max(0, int(ledger.get("replay_count") or 0)) + 1


def event_history(session: Any, *, limit: int = 50) -> List[Dict[str, Any]]:
    events = _events(session); selected = events[-max(1, int(limit)):] if limit else events
    out = []
    for event in selected:
        if not isinstance(event, dict):
            continue
        out.append({
            "turn": event.get("turn"), "tool": event.get("tool"), "status": event.get("status"), "error_code": event.get("error_code"), "retryable": event.get("retryable"),
            "failure_scope": event.get("failure_scope"), "failure_resource": event.get("failure_resource"),
            "observation_signature": event.get("observation_signature"), "arguments": copy.deepcopy(event.get("arguments") or {}),
            "result": copy.deepcopy(event.get("result") or {}),
            "grounding_ids": list(event.get("grounding_ids") or []),
            "frontier_ids": list(event.get("frontier_ids") or []), "replay_reason": event.get("replay_reason"),
        })
    return out


def navigation_view(session: Any) -> List[Dict[str, Any]]:
    ordered = sorted(
        (item for item in _entries(session).values() if isinstance(item, dict)),
        key=lambda item: (int(item.get("turn") or 0), str(item.get("observation_signature") or "")),
    )
    out = []
    for item in ordered:
        frontier_ids = list(item.get("frontier_ids") or [])
        entry = {
            "turn": item.get("turn"), "tool": item.get("tool"),
            "grounding_ids": list(item.get("grounding_ids") or []),
            "frontiers": frontier_view(_ledger(session), frontier_ids),
        }
        if item.get("observation_signature"):
            entry["observation_signature"] = item.get("observation_signature")
        coverage = item.get("coverage") if isinstance(item.get("coverage"), dict) else None
        if coverage:
            entry["coverage"] = copy.deepcopy(coverage)
        out.append(entry)
    return out


def physical_tool_calls(session: Any) -> int:
    return sum(1 for event in _events(session) if isinstance(event, dict) and event.get("executed") is True)


def replay_count(session: Any) -> int:
    return max(0, int(_ledger(session).get("replay_count") or 0))


def persisted_view(ledger: Dict[str, Any]) -> Dict[str, Any]:
    """Persist canonical physical state, never hot source bodies or pending deltas."""
    entries = ledger.get("entries") if isinstance(ledger, dict) and isinstance(ledger.get("entries"), dict) else {}
    events = ledger.get("events") if isinstance(ledger, dict) and isinstance(ledger.get("events"), list) else []
    safe_entries: Dict[str, Dict[str, Any]] = {}
    for key, value in entries.items():
        if not isinstance(value, dict):
            continue
        safe_entries[str(key)] = {
            field: copy.deepcopy(value.get(field))
            for field in (
                "observation_signature", "workspace_epoch", "tool", "arguments", "public_arguments",
                "result_fingerprint", "grounding_ids", "frontier_ids", "coverage",
                "failure_scope", "failure_resource", "failure_error_code", "failure_detail", "turn",
            )
            if value.get(field) is not None
        }
    safe_events = []
    for item in events:
        if not isinstance(item, dict):
            continue
        safe_events.append({
            field: copy.deepcopy(item.get(field))
            for field in (
                "event_id", "turn", "workspace_epoch", "tool", "arguments", "status", "executed",
                "ok", "error_code", "failure_scope", "failure_resource", "observation_signature",
                "grounding_ids", "frontier_ids", "result", "replay_reason",
            )
            if item.get(field) is not None
        })
    safe_materials = {
        key: {
            field: copy.deepcopy(value)
            for field, value in item.items()
            if field not in {"content", "numbered_content"}
        }
        for key, item in material_items(ledger).items() if isinstance(item, dict)
    }
    safe_frontiers = {
        str(key): {
            field: copy.deepcopy(item.get(field))
            for field in ("id", "handle", "workspace_epoch", "source_tool", "kind", "at", "reason", "count", "status")
            if item.get(field) is not None
        }
        for key, item in frontier_store(ledger).items() if isinstance(item, dict)
    }
    return {
        "entries": safe_entries,
        "events": safe_events,
        "replay_count": max(0, int((ledger or {}).get("replay_count") or 0)),
        "pending_results": [],
        "handles": persisted_handles(ledger),
        "snapshots": persisted_snapshots(ledger),
        "frontiers": safe_frontiers,
        "materials": safe_materials,
    }
