"""Intrinsic epistemic Memory Graph cognition for Eyle.

Memory is a transversal cognitive layer beside ECC. Main decides learned meaning
and retention independently from epistemic state. ``temporary``/``persistent`` answer whether a memory should be retained; epistemic metadata answers what kind of belief/observation/hypothesis it is, how confident Main is, how volatile it may be and in what temporal/contextual frame it applies. Runtime owns graph mechanics, paged materialization,
revisions, persistence, freshness and exact Coverage/Frontier continuation.
Temporary memory is not conversation history and is not cleared merely because a
chat/job boundary occurs.
"""
from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, Iterable, List

from eyle.core.evidence import retain_evidence
from eyle.contracts.observation import register_snapshot_handle, materialize_snapshot_handle, snapshot_store
from eyle.runtime.memory_graph import (
    apply_graph_operations, create_recall_snapshot, edge_history, edge_record, graph_overview, graph_records,
    node_record, node_history, recall_snapshot_page, release_recall_snapshot, world_scope,
)
from eyle.runtime.observation import (
    consume_frontier, expose_frontiers, frontier_view, material_items, resolve_frontier,
)




def release_memory_navigation(session: Any, provider_context: Dict[str, Any]) -> Dict[str, Any]:
    """Release DB-backed Memory recall snapshots owned by a terminal Session.

    A Memory Frontier is logical task-navigation state, not learned knowledge.  While
    a task is pending confirmation the Session is persisted and its exact cursor must
    survive.  Once the logical task reaches a terminal state, however, any unopened
    recall snapshots are unreachable and may be deleted mechanically.

    This performs no semantic pruning and never touches Memory nodes/relations.
    """
    storage, _world_scope_id = _context(provider_context)
    if not storage or session is None:
        return {"released": 0, "snapshot_ids": []}
    ledger = getattr(session, "observation_ledger", None)
    if not isinstance(ledger, dict):
        return {"released": 0, "snapshot_ids": []}
    db_ids: list[str] = []
    for item in list(snapshot_store(ledger).values()):
        if not isinstance(item, dict):
            continue
        payload = item.get("payload")
        cursor = payload.get("memory_cursor") if isinstance(payload, dict) else None
        snapshot_id = str((cursor or {}).get("snapshot_id") or "").strip() if isinstance(cursor, dict) else ""
        if snapshot_id and snapshot_id not in db_ids:
            db_ids.append(snapshot_id)
    released = 0
    for snapshot_id in db_ids:
        try:
            release_recall_snapshot(storage, snapshot_id)
            released += 1
        except (OSError, ValueError):
            # Terminal cleanup is best-effort physical housekeeping.  A cleanup
            # failure must never mutate or invalidate learned Memory.
            continue
    return {"released": released, "snapshot_ids": db_ids}

def empty_memory_view() -> Dict[str, Any]:
    return {"node_ids": [], "coverage": {}, "frontiers": [], "selector": {}, "overview": {}}

def _context(provider_context: Dict[str, Any]) -> tuple[str | None, str | None]:
    raw = (provider_context or {}).get("core_memory")
    if not isinstance(raw, dict):
        return None, None
    storage = str(raw.get("storage_dir") or "").strip() or None
    world_scope_id = str(raw.get("world_scope_id") or "").strip() or None
    return storage, world_scope_id


def memory_available(provider_context: Dict[str, Any]) -> bool:
    storage, scope = _context(provider_context)
    return bool(storage and scope)


def memory_environment(provider_context: Dict[str, Any]) -> Dict[str, Any]:
    """Small capability/identity envelope; graph counts are projected only once."""
    storage, scope = _context(provider_context)
    if not storage or not scope:
        return {"available": False}
    return {"available": True, "world_scope": world_scope(scope)}



def materialize_active_task(session: Any, provider_context: Dict[str, Any]) -> Dict[str, Any]:
    """Project exactly the Main-bound Task; never search, rank or expand Memory.

    ``active_task_id`` is semantic identity authored by Main and persisted by the
    Session. Runtime only performs an exact node lookup and exposes compact
    mechanical lifecycle/revision state. A terminal Task may remain projected so
    Main can observe that fact and decide how to conclude or rebind.
    """
    task_id = str(getattr(session, "active_task_id", None) or "").strip()
    if not task_id:
        return {}
    storage, _world_scope_id = _context(provider_context)
    if not storage:
        return {
            "id": task_id,
            "available": False,
            "error_code": "ACTIVE_TASK_CONTEXT_UNAVAILABLE",
        }
    try:
        record = node_record(storage, task_id)
    except (OSError, ValueError) as exc:
        return {
            "id": task_id,
            "available": False,
            "error_code": str(exc).split(":", 1)[0] or "ACTIVE_TASK_NOT_FOUND",
        }
    task = record.get("task") if isinstance(record.get("task"), dict) else None
    if str(record.get("kind") or "") != "task" or str(record.get("domain") or "") != "task" or task is None:
        return {
            "id": task_id,
            "available": False,
            "error_code": "ACTIVE_TASK_INVALID",
        }
    return {
        "id": task_id,
        "available": True,
        "revision": int(record.get("revision") or 0),
        "state": str(task.get("state") or ""),
        "state_revision": int(task.get("state_revision") or 0),
        "content": str(record.get("content") or ""),
    }


def apply_task_binding(
    session: Any,
    binding: Any,
    *,
    aliases: Dict[str, str] | None,
    provider_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply one explicit Main-authored active-Task binding mechanically.

    Omission/``None`` means no binding change. Runtime never discovers a Task.
    New bindings accept only current ``active``/``blocked`` Task nodes. Unbinding
    is explicit. Exact @aliases may refer to a Task created in the same atomic
    Memory sidecar.
    """
    if binding is None:
        return {"ok": True, "changed": False, "active_task_id": getattr(session, "active_task_id", None)}
    if not isinstance(binding, dict):
        return {"ok": False, "changed": False, "error_code": "TASK_BINDING_INVALID"}
    action = str(binding.get("action") or "").strip()
    if action == "unbind":
        if set(binding) != {"action"}:
            return {"ok": False, "changed": False, "error_code": "TASK_BINDING_INVALID"}
        changed = getattr(session, "active_task_id", None) is not None
        session.active_task_id = None
        return {"ok": True, "changed": changed, "active_task_id": None}
    if action != "bind" or set(binding) != {"action", "ref"}:
        return {"ok": False, "changed": False, "error_code": "TASK_BINDING_INVALID"}

    ref = str(binding.get("ref") or "").strip()
    if not ref:
        return {"ok": False, "changed": False, "error_code": "TASK_BINDING_INVALID"}
    try:
        task_id = _resolve_ref(ref, dict(aliases or {}))
    except ValueError as exc:
        return {"ok": False, "changed": False, "error_code": str(exc).split(":", 1)[0], "detail": str(exc)}
    storage, _world_scope_id = _context(provider_context)
    if not storage:
        return {"ok": False, "changed": False, "error_code": "MEMORY_CONTEXT_UNAVAILABLE"}
    try:
        record = node_record(storage, task_id)
    except (OSError, ValueError) as exc:
        return {"ok": False, "changed": False, "error_code": str(exc).split(":", 1)[0], "detail": str(exc)}
    task = record.get("task") if isinstance(record.get("task"), dict) else None
    if str(record.get("kind") or "") != "task" or str(record.get("domain") or "") != "task" or task is None:
        return {"ok": False, "changed": False, "error_code": "TASK_BINDING_TARGET_INVALID"}
    if str(task.get("state") or "") not in {"active", "blocked"}:
        return {"ok": False, "changed": False, "error_code": "TASK_BINDING_TARGET_TERMINAL"}
    previous = getattr(session, "active_task_id", None)
    session.active_task_id = task_id
    return {
        "ok": True,
        "changed": previous != task_id,
        "active_task_id": task_id,
        "previous_active_task_id": previous,
    }


def _resolve_ref(value: Any, aliases: Dict[str, str]) -> str:
    text = str(value or "").strip()
    if text.startswith("@"):
        key = text[1:]
        if key not in aliases:
            raise ValueError(f"MEMORY_ALIAS_UNKNOWN:{text}")
        return aliases[key]
    return text


def _support_anchors(
    supports: Iterable[Dict[str, Any]],
    *,
    session: Any,
    registry: Any,
    provider_context: Dict[str, Any],
    storage_dir: str,
    aliases: Dict[str, str],
) -> tuple[List[Dict[str, Any]], List[str]]:
    anchors: List[Dict[str, Any]] = []
    evidence_ids: List[str] = []
    materials = material_items(session.observation_ledger)
    for support in supports or []:
        if not isinstance(support, dict):
            raise ValueError("MEMORY_SUPPORT_INVALID")
        kind = str(support.get("kind") or "").strip()
        if kind == "request":
            anchors.append({
                "anchor_kind": "request",
                "locator": {"kind": "request", "execution_id": session.execution_id, "turn": int(session.turn)},
                "source_ref": str(session.execution_id or "current_request"),
            })
            continue
        if kind == "memory":
            raw_memory_id = str(support.get("memory_id") or "").strip()
            target = _resolve_ref(raw_memory_id, aliases)
            requested_revision = support.get("revision")
            try:
                record = node_record(storage_dir, target)  # deterministic existence/current-revision check
                source_revision = int(requested_revision) if requested_revision is not None else int(record.get("revision") or 0)
            except (OSError, ValueError):
                if not raw_memory_id.startswith("@"):
                    raise
                # A same-delta @alias denotes a node that will be atomically
                # created earlier in this exact changeset; its first revision is
                # mechanically 1. _insert_anchors validates existence at commit.
                source_revision = int(requested_revision) if requested_revision is not None else 1
            anchors.append({
                "anchor_kind": "memory", "source_entity_type": "node",
                "source_revision": source_revision,
                "locator": {"kind": "memory_node", "revision": source_revision}, "source_ref": target,
            })
            continue
        if kind == "relation":
            relation_id = str(support.get("relation_id") or "").strip()
            record = edge_record(storage_dir, relation_id)  # deterministic existence/current-revision check
            requested_revision = support.get("revision")
            source_revision = int(requested_revision) if requested_revision is not None else int(record.get("revision") or 0)
            anchors.append({
                "anchor_kind": "relation", "source_entity_type": "edge",
                "source_revision": source_revision,
                "locator": {"kind": "memory_relation", "revision": source_revision}, "source_ref": relation_id,
            })
            continue
        if kind != "material":
            raise ValueError("MEMORY_SUPPORT_KIND_INVALID")
        material_id = str(support.get("material_id") or "").strip()
        material = materials.get(material_id)
        if not isinstance(material, dict):
            raise ValueError(f"MEMORY_UNKNOWN_MATERIAL:{material_id}")
        if int(material.get("reality_epoch") or 0) != int(session.reality_epoch or 0):
            raise ValueError(f"MEMORY_STALE_MATERIAL:{material_id}")
        selector = copy.deepcopy(support.get("selector") or {})
        if not isinstance(selector, dict):
            raise ValueError("MEMORY_SUPPORT_SELECTOR_INVALID")
        selected = registry.select_evidence(material, selector)
        session.evidence, evidence_id, _created = retain_evidence(
            session.evidence,
            material_id=material_id,
            material=material,
            selector=selector,
            selected=selected,
            reality_epoch=session.reality_epoch,
        )
        evidence_ids.append(evidence_id)
        anchors.append({
            "anchor_kind": "material",
            "source_capability": str(material.get("source_capability") or ""),
            "locator": copy.deepcopy(selected.get("locator") or material.get("locator") or {}),
            "source_version": material.get("source_version"),
            "content_hash": selected.get("content_hash") or material.get("content_hash"),
            "freshness_token": material.get("freshness_token"),
            "freshness_arguments": copy.deepcopy(material.get("freshness_arguments") or {}),
            "source_ref": evidence_id,
        })
    return anchors, evidence_ids


def apply_memory_sidecar(
    session: Any,
    operations: Any,
    *,
    registry: Any,
    provider_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply Main-authored persistent learning atomically.

    ``memory_delta=[]`` means this experience added no useful memory; a non-empty list is the exact graph delta Main chose.
    """
    if not isinstance(operations, list):
        return {"ok": False, "error_code": "MEMORY_SIDECAR_INVALID"}
    ops = [copy.deepcopy(v) for v in operations if isinstance(v, dict)]
    if len(ops) != len(operations):
        return {"ok": False, "error_code": "MEMORY_SIDECAR_INVALID"}
    if not ops:
        return {"ok": True, "changed": False, "affected": [], "evidence_ids": []}

    storage, world_scope_id = _context(provider_context)
    if not storage or not world_scope_id:
        return {"ok": False, "error_code": "MEMORY_CONTEXT_UNAVAILABLE"}
    world_scope_value = world_scope(world_scope_id)

    aliases: Dict[str, str] = {}
    for raw in ops:
        if str(raw.get("op") or "") == "remember":
            key = str(raw.get("key") or "").strip()
            if key:
                if key in aliases:
                    return {"ok": False, "error_code": "MEMORY_ALIAS_DUPLICATE", "detail": key}
                aliases[key] = f"mem-{uuid.uuid4().hex[:16]}"

    prepared: List[Dict[str, Any]] = []
    evidence_ids: List[str] = []
    try:
        for raw in ops:
            op = str(raw.get("op") or "").strip()
            supports = raw.get("supports") or []
            anchors, retained = _support_anchors(
                supports, session=session, registry=registry, provider_context=provider_context,
                storage_dir=storage, aliases=aliases,
            )
            evidence_ids.extend(retained)
            if op == "remember":
                mapped_scope = "user" if str(raw.get("scope") or "world") == "user" else world_scope_value
                key = str(raw.get("key") or "").strip()
                prepared.append({
                    "op": "create_node", "id": aliases.get(key) if key else None,
                    "scope": mapped_scope, "kind": raw.get("kind"), "content": raw.get("content"),
                    "retention": raw.get("retention") or "temporary", "epistemic": raw.get("epistemic") or {"nature": "unclassified"},
                    "recall": raw.get("recall") or {}, "tags": raw.get("tags") or [], "anchors": anchors,
                })
            elif op == "revise":
                node_id = _resolve_ref(raw.get("id"), aliases); node_record(storage, node_id)
                prepared.append({
                    "op": "update_node", "id": node_id, "expected_revision": raw.get("expected_revision"),
                    "content": raw.get("content"), "kind": raw.get("kind"), "retention": raw.get("retention"),
                    "epistemic": raw.get("epistemic"), "recall": raw.get("recall"),
                    "add_recall": raw.get("add_recall") or {}, "remove_recall": raw.get("remove_recall") or {},
                    "add_tags": raw.get("add_tags") or [], "remove_tags": raw.get("remove_tags") or [],
                    "anchors": anchors,
                })
            elif op == "relate":
                source = _resolve_ref(raw.get("source"), aliases); target = _resolve_ref(raw.get("target"), aliases)
                if not str(raw.get("source") or "").startswith("@"): node_record(storage, source)
                if not str(raw.get("target") or "").startswith("@"): node_record(storage, target)
                prepared.append({"op": "create_edge", "source": source, "label": raw.get("relation"), "target": target, "epistemic": raw.get("epistemic") or {"nature": "relation"}, "anchors": anchors})
            elif op == "revise_relation":
                relation_id = str(raw.get("id") or "").strip(); edge_record(storage, relation_id)
                prepared.append({
                    "op": "update_edge", "id": relation_id, "expected_revision": raw.get("expected_revision"),
                    "label": raw.get("relation"), "epistemic": raw.get("epistemic"), "anchors": anchors,
                })
            elif op == "archive":
                node_id = _resolve_ref(raw.get("id"), aliases); node_record(storage, node_id)
                prepared.append({"op": "archive_node", "id": node_id, "expected_revision": raw.get("expected_revision")})
            elif op == "supersede":
                node_id = _resolve_ref(raw.get("id"), aliases); replacement = _resolve_ref(raw.get("replacement"), aliases)
                node_record(storage, node_id)
                if not str(raw.get("replacement") or "").startswith("@"): node_record(storage, replacement)
                prepared.append({"op": "supersede_node", "id": node_id, "expected_revision": raw.get("expected_revision"), "replacement": replacement})
            elif op == "retire_relation":
                relation_id = str(raw.get("id") or "").strip(); edge_record(storage, relation_id)
                prepared.append({"op": "retire_edge", "id": relation_id, "expected_revision": raw.get("expected_revision")})
            elif op == "task_status":
                task_id = _resolve_ref(raw.get("id"), aliases)
                if not str(raw.get("id") or "").startswith("@"):
                    task_node = node_record(storage, task_id)
                    if str(task_node.get("kind") or "") != "task" or not isinstance(task_node.get("task"), dict):
                        raise ValueError("MEMORY_TASK_NOT_FOUND")
                prepared.append({
                    "op": "set_task_status", "id": task_id,
                    "expected_state_revision": raw.get("expected_state_revision"),
                    "state": raw.get("state"),
                })
            else:
                raise ValueError(f"MEMORY_SIDECAR_OPERATION_INVALID:{op or '<empty>'}")
        change = apply_graph_operations(storage, prepared, execution_id=session.execution_id, turn=session.turn)
        # Eyle does not auto-archive temporary memory by a tiny fixed
        # capacity. Projection remains bounded, while retention stays an
        # explicit cognition/maintenance decision.
    except (OSError, ValueError) as exc:
        return {"ok": False, "error_code": str(exc).split(":", 1)[0], "detail": str(exc)}
    affected = change.get("affected") or []
    task_state_changed = any(
        isinstance(item, dict) and item.get("type") == "task" and item.get("action") == "set_task_status"
        for item in affected
    )
    return {
        "ok": True, "changed": bool(change.get("count")), "task_state_changed": task_state_changed,
        "changeset_id": change.get("changeset_id"), "affected": affected,
        "evidence_ids": sorted(set(evidence_ids)), "aliases": aliases,
    }


def _compact_locator(locator: Dict[str, Any], *, maximum: int = 12) -> Dict[str, Any]:
    """Bound an opaque provider locator without interpreting its domain."""
    out: Dict[str, Any] = {}
    for key in sorted(locator):
        if len(out) >= maximum:
            break
        value = locator.get(key)
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)) and len(str(value)) <= 240:
            out[str(key)[:120]] = value
        elif isinstance(value, list) and len(value) <= 8 and all(isinstance(v, (str, int, float, bool)) and len(str(v)) <= 120 for v in value):
            out[str(key)[:120]] = copy.deepcopy(value)
    return out

def _anchor_status(
    anchor: Dict[str, Any],
    *,
    registry: Any,
    runtime_ctx: Dict[str, Any],
    storage_dir: str,
    node_cache: Dict[str, Dict[str, Any]],
    stack: set[str],
) -> Dict[str, Any]:
    kind = str(anchor.get("anchor_kind") or "")
    if kind == "request":
        return {"status": "semantic", "kind": "request", "origin_state": "pinned_request"}
    if kind == "memory":
        ref = str(anchor.get("source_ref") or "")
        pinned_revision = anchor.get("source_revision")
        if not ref:
            return {"status": "degraded", "kind": "memory", "reason": "missing_memory_ref"}
        if ref in stack:
            return {"status": "degraded", "kind": "memory", "memory_id": ref, "reason": "dependency_cycle"}
        try:
            raw = node_record(storage_dir, ref)
        except (OSError, ValueError):
            return {"status": "stale", "kind": "memory", "memory_id": ref, "reason": "dependency_missing", "source_revision": pinned_revision}
        current_revision = int(raw.get("revision") or 0)
        compact = {
            "kind": "memory", "memory_id": ref,
            "source_revision": int(pinned_revision) if pinned_revision is not None else None,
            "current_revision": current_revision,
        }
        if pinned_revision is None:
            compact["origin_state"] = "unpinned"
            evaluated = _evaluate_node(raw, registry=registry, runtime_ctx=runtime_ctx, storage_dir=storage_dir, node_cache=node_cache, stack=stack | {ref})
            compact["status"] = str(evaluated.get("freshness") or "semantic")
            return compact
        if current_revision != int(pinned_revision):
            # This is a provenance fact only. Runtime does not infer that the
            # derived memory became false/stale merely because its source was revised.
            compact.update({"status": "semantic", "origin_state": "source_revised"})
            return compact
        compact["origin_state"] = "pinned_current"
        evaluated = _evaluate_node(raw, registry=registry, runtime_ctx=runtime_ctx, storage_dir=storage_dir, node_cache=node_cache, stack=stack | {ref})
        compact["status"] = str(evaluated.get("freshness") or "semantic")
        return compact
    if kind == "relation":
        ref = str(anchor.get("source_ref") or "")
        pinned_revision = anchor.get("source_revision")
        if not ref:
            return {"status": "degraded", "kind": "relation", "reason": "missing_relation_ref"}
        try:
            raw = edge_record(storage_dir, ref)
        except (OSError, ValueError):
            return {"status": "stale", "kind": "relation", "relation_id": ref, "reason": "dependency_missing", "source_revision": pinned_revision}
        current_revision = int(raw.get("revision") or 0)
        origin_state = "unpinned" if pinned_revision is None else ("pinned_current" if current_revision == int(pinned_revision) else "source_revised")
        return {
            "status": "semantic", "kind": "relation", "relation_id": ref,
            "source_revision": int(pinned_revision) if pinned_revision is not None else None,
            "current_revision": current_revision, "origin_state": origin_state,
        }
    if kind != "material":
        return {"status": "degraded", "kind": kind or "unknown", "reason": "unknown_anchor"}
    material = {
        "source_capability": anchor.get("source_capability"),
        "locator": copy.deepcopy(anchor.get("locator") or {}),
        "source_version": anchor.get("source_version"),
        "content_hash": anchor.get("content_hash"),
        "freshness_token": anchor.get("freshness_token"),
        "freshness_arguments": copy.deepcopy(anchor.get("freshness_arguments") or {}),
    }
    fresh, reason = registry.material_freshness(material, runtime_ctx)
    locator = material.get("locator") if isinstance(material.get("locator"), dict) else {}
    compact = {
        "kind": "material", "status": "fresh" if fresh is True else "stale",
        "origin_state": "pinned_material",
        "source_capability": material.get("source_capability"),
        "locator": _compact_locator(locator),
    }
    source_ref = str(anchor.get("source_ref") or "")
    if source_ref.startswith("ev-"):
        compact["evidence_id"] = source_ref
    if fresh is not True:
        compact["reason"] = str(reason or "stale")
    return compact


def _combine_anchor_statuses(statuses: List[Dict[str, Any]]) -> str:
    if not statuses:
        return "unbound"
    values = [str(item.get("status") or "degraded") for item in statuses]
    if all(value == "semantic" for value in values):
        return "semantic"
    physicalish = [v for v in values if v != "semantic"]
    if physicalish and all(v == "fresh" for v in physicalish):
        return "fresh"
    if physicalish and all(v == "stale" for v in physicalish) and "semantic" not in values:
        return "stale"
    if "stale" in values or "degraded" in values:
        return "degraded"
    return "fresh"


def _evaluate_node(
    raw: Dict[str, Any], *, registry: Any, runtime_ctx: Dict[str, Any], storage_dir: str,
    node_cache: Dict[str, Dict[str, Any]], stack: set[str],
) -> Dict[str, Any]:
    node_id = str(raw.get("id") or "")
    if node_id in node_cache:
        return copy.deepcopy(node_cache[node_id])
    statuses = [
        _anchor_status(anchor, registry=registry, runtime_ctx=runtime_ctx, storage_dir=storage_dir, node_cache=node_cache, stack=stack)
        for anchor in raw.get("anchors") or [] if isinstance(anchor, dict)
    ]
    freshness = _combine_anchor_statuses(statuses)
    out = {
        "id": node_id, "scope": raw.get("scope"),
        "domain": raw.get("domain") or "knowledge", "context_key": raw.get("context_key"),
        "kind": raw.get("kind"),
        "retention": raw.get("retention") or "persistent", "status": raw.get("status") or "current",
        "content": str(raw.get("content") or ""),
        "epistemic": copy.deepcopy(raw.get("epistemic") or {"nature": "unclassified", "volatility": "unknown", "temporal": {}, "context": {}}),
        "revision": raw.get("revision"),
        "created_at": raw.get("created_at"), "updated_at": raw.get("updated_at"),
    }
    task = raw.get("task") if isinstance(raw.get("task"), dict) else None
    if task is not None:
        out["task"] = copy.deepcopy(task)
    recall = copy.deepcopy(raw.get("recall") or {})
    if recall:
        out["recall"] = recall
    tags = list(raw.get("tags") or [])
    if tags:
        out["tags"] = tags
    if freshness not in {"", "unbound"}:
        out["freshness"] = freshness
    if statuses:
        out["sources"] = statuses
        changed_sources = [item for item in statuses if str(item.get("origin_state") or "") == "source_revised"]
        if changed_sources:
            out["provenance"] = {"source_revised": len(changed_sources)}
    node_cache[node_id] = copy.deepcopy(out)
    return out


def _runtime_context(session: Any, config: Dict[str, Any], provider_context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "config": config or {}, "provider_context": provider_context or {}, "session": session,
        "grounding": material_items(session.observation_ledger), "observation_ledger": session.observation_ledger,
        "reality_epoch": int(session.reality_epoch),
    }


def _project_records(
    raw: Dict[str, Any], *, session: Any, registry: Any, config: Dict[str, Any],
    provider_context: Dict[str, Any], storage: str,
) -> Dict[str, Any]:
    runtime_ctx = _runtime_context(session, config, provider_context)
    node_cache: Dict[str, Dict[str, Any]] = {}
    nodes = [
        _evaluate_node(item, registry=registry, runtime_ctx=runtime_ctx, storage_dir=storage,
                       node_cache=node_cache, stack={str(item.get("id") or "")})
        for item in raw.get("nodes") or [] if isinstance(item, dict)
    ]
    by_id = {str(item.get("id")): item for item in nodes}
    edges = []
    for raw_edge in raw.get("edges") or []:
        if not isinstance(raw_edge, dict):
            continue
        statuses = [
            _anchor_status(anchor, registry=registry, runtime_ctx=runtime_ctx, storage_dir=storage,
                           node_cache=node_cache, stack=set())
            for anchor in raw_edge.get("anchors") or [] if isinstance(anchor, dict)
        ]
        if statuses:
            freshness = _combine_anchor_statuses(statuses)
        else:
            endpoint = [str((by_id.get(str(raw_edge.get(side))) or {}).get("freshness") or "unbound") for side in ("source", "target")]
            freshness = "stale" if endpoint and all(v == "stale" for v in endpoint) else ("degraded" if any(v in {"stale", "degraded"} for v in endpoint) else "fresh")
        edges.append({
            "id": raw_edge.get("id"), "source": raw_edge.get("source"), "relation": raw_edge.get("label"),
            "target": raw_edge.get("target"), "epistemic": copy.deepcopy(raw_edge.get("epistemic") or {"nature": "relation", "volatility": "unknown", "temporal": {}, "context": {}}),
            "revision": raw_edge.get("revision"), "freshness": freshness,
            **({"sources": statuses} if statuses else {}),
            **({"provenance": {"source_revised": sum(1 for item in statuses if str(item.get("origin_state") or "") == "source_revised")}} if any(str(item.get("origin_state") or "") == "source_revised" for item in statuses) else {}),
        })
    tasks = [{
        "id": item.get("id"), "state": (item.get("task") or {}).get("state"),
        "state_revision": (item.get("task") or {}).get("state_revision"),
        "retention": item.get("retention"), "revision": item.get("revision"),
    } for item in nodes if isinstance(item.get("task"), dict)]
    return {"nodes": nodes, "edges": edges, "tasks": tasks}


def sync_memory_lifecycle(provider_context: Dict[str, Any], conversation_context: Any, *, execution_id: str | None = None) -> Dict[str, Any]:
    """Conversation boundaries do not erase Memory.

    Temporary retention means weak/possibly-useful memory, not chat-local state.
    Initial projection is mechanically paged; storage retention is not silently trimmed when deltas are
    applied. The hook remains explicit so hosts can report the boundary without
    turning it into a second memory system.
    """
    return {"changed": False, "reason": "memory_survives_conversation_boundary"}

def materialize_explicit_memory_view(
    session: Any, *, registry: Any, config: Dict[str, Any], provider_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Materialize only Memory explicitly selected by Main.

    Runtime never injects temporary/global Memory merely because it exists.
    Recall and activation own selection; this function only serializes the exact
    node IDs and Frontier state already present in the Session.
    """
    storage, world_scope_id = _context(provider_context)
    if not storage or not world_scope_id:
        return {"available": False, "nodes": [], "edges": []}
    state = session.memory_view if isinstance(getattr(session, "memory_view", None), dict) else empty_memory_view()
    explicit_ids = [str(v) for v in state.get("node_ids") or [] if str(v).strip()]
    frontier_ids = [str(v) for v in state.get("frontiers") or [] if str(v).strip()]
    try:
        projected = _project_records(
            graph_records(storage, explicit_ids, include_inactive=True),
            session=session, registry=registry, config=config,
            provider_context=provider_context, storage=storage,
        )
    except (OSError, ValueError) as exc:
        return {"available": False, "nodes": [], "edges": [], "error": str(exc).split(":", 1)[0]}
    view = {"available": True, **projected}
    frontiers = frontier_view(session.observation_ledger, frontier_ids)
    if frontiers:
        view["frontiers"] = frontiers
    for key in ("coverage", "selector", "overview"):
        value = copy.deepcopy(state.get(key) or {})
        if value:
            view[key] = value
    return view


def memory_overview_result(session: Any, *, arguments: Dict[str, Any], provider_context: Dict[str, Any]) -> Dict[str, Any]:
    storage, world_scope_id = _context(provider_context)
    if not storage or not world_scope_id:
        return {"operation": "memory_overview", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "MEMORY_CONTEXT_UNAVAILABLE"}
    scope = str(arguments.get("scope") or "all")
    try:
        overview = graph_overview(storage, world_scope_value=world_scope(world_scope_id), scope=scope)
    except (OSError, ValueError) as exc:
        return {"operation": "memory_overview", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": str(exc).split(":", 1)[0]}
    session.memory_view["overview"] = copy.deepcopy(overview)
    coverage = {
        "scope": {"kind": "memory_graph", "scope": scope},
        "examined": {"nodes": int(overview.get("nodes") or 0), "edges": int(overview.get("edges") or 0)},
        "complete": True, "boundaries": [],
        "facts": {"materialized_node_bodies": 0, "directory_only": True},
    }
    return {"operation": "memory_overview", "status": "success", "ok": True, "executed": True, "changed": False, "detail": overview, "coverage": coverage}


def _memory_frontier(
    session: Any, *, snapshot_id: str, after_ordinal: int, page_size: int,
    selector: Dict[str, Any], selection: Dict[str, Any], remaining: int,
) -> List[str]:
    """Expose a tiny DB cursor as an exact Memory Frontier.

    Session no longer carries the full matching ID universe. The immutable
    selection lives in SQLite; the handle stores only snapshot identity + cursor.
    """
    if not snapshot_id or int(remaining or 0) <= 0:
        return []
    handle = register_snapshot_handle(
        session.observation_ledger,
        kind="memory_cursor",
        payload={
            "memory_cursor": {
                "snapshot_id": str(snapshot_id), "after_ordinal": max(0, int(after_ordinal or 0)),
                "page_size": max(1, int(page_size or 30)), "selector": copy.deepcopy(selector),
                "selection": copy.deepcopy(selection),
            }
        },
        reality_epoch=int(session.reality_epoch), source_capability="core.memory",
        description="Continue the exact DB-backed Memory selection", page_size=1, offset=0,
    )
    model = {"frontiers": [{
        "kind": "memory_not_materialized", "at": "memory_graph", "count": int(remaining),
        "reason": f"{int(remaining)} matching memory node(s) remain", "handle": handle["id"],
    }]}
    return expose_frontiers(session, "core.memory", model)


def memory_history_result(session: Any, *, arguments: Dict[str, Any], provider_context: Dict[str, Any]) -> Dict[str, Any]:
    storage, _world_scope_id = _context(provider_context)
    if not storage:
        return {"operation": "memory_history", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "MEMORY_CONTEXT_UNAVAILABLE"}
    node_id = str(arguments.get("id") or "").strip()
    if not node_id:
        return {"operation": "memory_history", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "MEMORY_NODE_ID_REQUIRED"}
    try:
        history = node_history(storage, node_id)
    except (OSError, ValueError) as exc:
        return {"operation": "memory_history", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": str(exc).split(":", 1)[0], "detail": str(exc)}
    coverage = {
        "scope": {"kind": "memory_node_history", "id": node_id},
        "examined": {"events": len(history.get("events") or []), "relations": len(history.get("relations") or [])},
        "complete": True, "boundaries": [],
        "facts": {"all_persisted_node_events_materialized": True},
    }
    return {"operation": "memory_history", "status": "success", "ok": True, "executed": True, "changed": False, "detail": history, "coverage": coverage}


def memory_relation_history_result(session: Any, *, arguments: Dict[str, Any], provider_context: Dict[str, Any]) -> Dict[str, Any]:
    storage, _world_scope_id = _context(provider_context)
    if not storage:
        return {"operation": "memory_relation_history", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "MEMORY_CONTEXT_UNAVAILABLE"}
    relation_id = str(arguments.get("id") or "").strip()
    if not relation_id:
        return {"operation": "memory_relation_history", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "MEMORY_EDGE_ID_REQUIRED"}
    try:
        history = edge_history(storage, relation_id)
    except (OSError, ValueError) as exc:
        return {"operation": "memory_relation_history", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": str(exc).split(":", 1)[0], "detail": str(exc)}
    coverage = {
        "scope": {"kind": "memory_relation_history", "id": relation_id},
        "examined": {"events": len(history.get("events") or [])},
        "complete": True, "boundaries": [], "facts": {"all_persisted_relation_events_materialized": True},
    }
    return {"operation": "memory_relation_history", "status": "success", "ok": True, "executed": True, "changed": False, "detail": history, "coverage": coverage}

def memory_activate_result(
    session: Any, *, arguments: Dict[str, Any], registry: Any, config: Dict[str, Any], provider_context: Dict[str, Any],
) -> Dict[str, Any]:
    storage, world_scope_id = _context(provider_context)
    if not storage or not world_scope_id:
        return {"operation": "memory_activate", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "MEMORY_CONTEXT_UNAVAILABLE"}
    selector = {
        "query": str(arguments.get("query") or "").strip(),
        "queries": [str(v) for v in arguments.get("queries") or [] if str(v).strip()],
        "ids": [str(v) for v in arguments.get("ids") or [] if str(v).strip()],
        "tags": [str(v) for v in arguments.get("tags") or [] if str(v).strip()],
        "scope": str(arguments.get("scope") or "all"),
        "retention": str(arguments.get("retention") or "all"),
        "domain": str(arguments.get("domain") or "all").strip().lower(),
        "context_key": str(arguments.get("context_key") or "").strip() or None,
        "natures": [str(v) for v in arguments.get("natures") or [] if str(v).strip()],
        "volatilities": [str(v) for v in arguments.get("volatilities") or [] if str(v).strip()],
        "relation_labels": [str(v) for v in arguments.get("relation_labels") or [] if str(v).strip()],
        "include_neighbors": bool(arguments.get("include_neighbors") is True),
    }
    if (
        not selector["query"] and not selector["queries"] and not selector["ids"] and not selector["tags"]
        and not selector["natures"] and not selector["volatilities"] and not selector["relation_labels"]
        and selector["domain"] == "all" and not selector["context_key"]
    ):
        return {"operation": "memory_activate", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "MEMORY_SELECTOR_REQUIRED", "detail": "Use query/queries, ids, tags, domain/context_key, natures, volatilities or relation_labels; use memory_overview to browse the directory."}
    page_size = max(1, int(arguments.get("limit") or 30))
    snapshot_id = ""
    try:
        created = create_recall_snapshot(
            storage, world_scope_value=world_scope(world_scope_id),
            owner_execution_id=str(getattr(session, "execution_id", None) or "").strip() or None, **selector,
        )
        snapshot_id = str(created.get("snapshot_id") or "")
        selection = copy.deepcopy(created.get("selection") or {})
        page = recall_snapshot_page(storage, snapshot_id, after_ordinal=0, limit=page_size)
        page_ids = list(page.get("node_ids") or [])
        page_items = [copy.deepcopy(v) for v in page.get("items") or [] if isinstance(v, dict)]
    except (OSError, ValueError) as exc:
        if snapshot_id:
            try:
                release_recall_snapshot(storage, snapshot_id)
            except (OSError, ValueError):
                pass
        return {"operation": "memory_activate", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": str(exc).split(":", 1)[0], "detail": str(exc)}

    remaining = int(page.get("remaining") or 0)
    frontier_ids: List[str] = []
    if remaining > 0:
        frontier_ids = _memory_frontier(
            session, snapshot_id=snapshot_id, after_ordinal=int(page.get("last_ordinal") or 0),
            page_size=page_size, selector=selector, selection=selection, remaining=remaining,
        )
    else:
        release_recall_snapshot(storage, snapshot_id)
    total = int(selection.get("selected_nodes") or 0)
    coverage = {
        "scope": {"kind": "memory_graph", "scope": selector["scope"], "retention": selector.get("retention", "all"), "domain": selector.get("domain", "all"), "context_key": selector.get("context_key"), "query": selector["query"], "tags": selector["tags"], "ids": selector["ids"], "natures": selector.get("natures", []), "volatilities": selector.get("volatilities", [])},
        "examined": {"nodes": int(selection.get("scoped_nodes") or 0), "matches": total},
        "complete": not bool(frontier_ids), "boundaries": [],
        "facts": {
            "materialized_nodes": len(page_ids), "remaining_nodes": remaining,
            "include_neighbors": selector["include_neighbors"], "search_backend": selection.get("backend"),
            "db_cursor": True, "navigation_provenance": True,
        },
    }
    session.memory_view = {"node_ids": page_ids, "coverage": coverage, "frontiers": frontier_ids, "selector": selector, "overview": copy.deepcopy(session.memory_view.get("overview") or {})}
    return {
        "operation": "memory_activate", "status": "success", "ok": True, "executed": True, "changed": False,
        "detail": {
            "activation": "materialized_in_memory_view",
            "node_ids": page_ids,
            "matched_nodes": int(selection.get("matched_nodes") or 0),
            "selected_nodes": total,
            "search_backend": selection.get("backend"),
            "recall_provenance": page_items,
        },
        "coverage": coverage,
        **({"frontiers": frontier_view(session.observation_ledger, frontier_ids)} if frontier_ids else {}),
    }


def memory_continue_result(
    session: Any, *, frontier_id: str, registry: Any, config: Dict[str, Any], provider_context: Dict[str, Any],
) -> Dict[str, Any]:
    storage, _world_scope_id = _context(provider_context)
    if not storage:
        return {"operation": "continue", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "MEMORY_CONTEXT_UNAVAILABLE"}
    handle_id, error = resolve_frontier(session.observation_ledger, frontier_id, reality_epoch=int(session.reality_epoch))
    if error:
        return {"operation": "continue", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": error}
    materialized, error = materialize_snapshot_handle(session.observation_ledger, str(handle_id), reality_epoch=int(session.reality_epoch))
    if error or not isinstance(materialized, dict):
        return {"operation": "continue", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": error or "MEMORY_CONTINUATION_INVALID"}
    payload = materialized.get("payload") if isinstance(materialized.get("payload"), dict) else {}

    cursor = payload.get("memory_cursor") if isinstance(payload.get("memory_cursor"), dict) else None
    if cursor is None:
        consume_frontier(session.observation_ledger, frontier_id)
        return {
            "operation": "continue", "status": "failed", "ok": False, "executed": False,
            "changed": False, "error_code": "MEMORY_CONTINUATION_INVALID",
        }
    snapshot_id = str(cursor.get("snapshot_id") or "")
    selector = copy.deepcopy(cursor.get("selector") or session.memory_view.get("selector") or {})
    selection = copy.deepcopy(cursor.get("selection") or {})
    page_size = max(1, int(cursor.get("page_size") or 30))
    try:
        page = recall_snapshot_page(
            storage, snapshot_id, after_ordinal=int(cursor.get("after_ordinal") or 0), limit=page_size,
        )
    except (OSError, ValueError) as exc:
        consume_frontier(session.observation_ledger, frontier_id)
        return {"operation": "continue", "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": str(exc).split(":", 1)[0], "detail": str(exc)}
    page_ids = list(page.get("node_ids") or [])
    page_items = [copy.deepcopy(v) for v in page.get("items") or [] if isinstance(v, dict)]
    consume_frontier(session.observation_ledger, frontier_id)
    remaining = int(page.get("remaining") or 0)
    next_ids: List[str] = []
    if remaining > 0:
        next_ids = _memory_frontier(
            session, snapshot_id=snapshot_id, after_ordinal=int(page.get("last_ordinal") or 0),
            page_size=page_size, selector=selector, selection=selection, remaining=remaining,
        )
    else:
        release_recall_snapshot(storage, snapshot_id)
    materialized_count = int(page.get("last_ordinal") or 0)
    total = int(selection.get("selected_nodes") or materialized_count)

    previous = [str(v) for v in session.memory_view.get("node_ids") or [] if str(v).strip()]
    merged: List[str] = []
    for node_id in [*previous, *page_ids]:
        if node_id not in merged:
            merged.append(node_id)
    coverage = {
        "scope": {"kind": "memory_graph", "scope": selector.get("scope", "all"), "retention": selector.get("retention", "all"), "domain": selector.get("domain", "all"), "context_key": selector.get("context_key"), "query": selector.get("query", ""), "tags": selector.get("tags", []), "ids": selector.get("ids", []), "natures": selector.get("natures", []), "volatilities": selector.get("volatilities", [])},
        "examined": {"nodes": int(selection.get("scoped_nodes") or 0), "matches": total},
        "complete": not bool(next_ids), "boundaries": [],
        "facts": {
            "materialized_nodes": materialized_count, "materialized_nodes_total": len(merged),
            "remaining_nodes": remaining, "search_backend": selection.get("backend"),
            "db_cursor": bool(selection.get("db_cursor")),
        },
    }
    session.memory_view.update({"node_ids": merged, "coverage": coverage, "frontiers": next_ids, "selector": selector})
    return {
        "operation": "continue", "status": "success", "ok": True, "executed": True, "changed": False,
        "detail": {
            "activation": "materialized_in_memory_view",
            "node_ids": page_ids,
            "new_nodes": len(page_ids),
            "recall_provenance": page_items,
        }, "coverage": coverage,
        **({"frontiers": frontier_view(session.observation_ledger, next_ids)} if next_ids else {}),
    }

