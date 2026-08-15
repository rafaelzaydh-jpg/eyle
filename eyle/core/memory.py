"""ECC-internal persistent Memory Graph sidecar.

Memory is not a fourth cognitive action and is not a capability.  Every ECC
move carries a small memory sidecar: Main may focus existing graph context and
may emit semantic graph deltas.  Runtime owns persistence, identities,
revision conflicts, source hashes/freshness and graph topology.
"""
from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, Iterable, List, Tuple

from eyle.core.evidence import retain_evidence
from eyle.runtime.memory_graph import (
    apply_graph_operations,
    edge_record,
    graph_counts,
    node_record,
    world_scope,
    retrieve_graph,
)
from eyle.runtime.observation import material_items


def empty_memory_focus() -> List[str]:
    return []


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
    storage, scope = _context(provider_context)
    if not storage or not scope:
        return {"available": False}
    try:
        counts = graph_counts(storage)
    except (OSError, ValueError):
        return {"available": False}
    return {"available": True, "world_scope": world_scope(scope), **counts}


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
            target = _resolve_ref(support.get("memory_id"), aliases)
            node_record(storage_dir, target)  # deterministic existence check
            anchors.append({"anchor_kind": "memory", "locator": {"kind": "memory_node"}, "source_ref": target})
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
    sidecar: Any,
    *,
    registry: Any,
    provider_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply one memory sidecar atomically. No semantic choice is made here."""
    if not isinstance(sidecar, dict):
        return {"ok": False, "error_code": "MEMORY_SIDECAR_INVALID"}
    focus = [str(v).strip() for v in sidecar.get("focus") or [] if str(v).strip()][:12]
    session.memory_focus = focus
    disposition = str(sidecar.get("disposition") or "").strip()
    operations = [copy.deepcopy(v) for v in sidecar.get("operations") or [] if isinstance(v, dict)]
    if disposition not in {"unchanged", "updated"}:
        return {"ok": False, "error_code": "MEMORY_DISPOSITION_INVALID", "focus": focus}
    if disposition == "unchanged" and operations:
        return {"ok": False, "error_code": "MEMORY_DISPOSITION_CONFLICT", "focus": focus}
    if disposition == "updated" and not operations:
        return {"ok": False, "error_code": "MEMORY_DISPOSITION_CONFLICT", "focus": focus}
    if not operations:
        return {"ok": True, "changed": False, "focus": focus, "disposition": disposition, "affected": [], "evidence_ids": []}

    storage, world_scope_id = _context(provider_context)
    if not storage or not world_scope_id:
        return {"ok": False, "error_code": "MEMORY_CONTEXT_UNAVAILABLE"}
    world_scope_value = world_scope(world_scope_id)

    # Runtime owns physical IDs. Main may name new nodes only through local @keys.
    aliases: Dict[str, str] = {}
    for raw in operations:
        if str(raw.get("op") or "") == "remember":
            key = str(raw.get("key") or "").strip()
            if key:
                if key in aliases:
                    return {"ok": False, "error_code": "MEMORY_ALIAS_DUPLICATE", "detail": key}
                aliases[key] = f"mem-{uuid.uuid4().hex[:16]}"

    prepared: List[Dict[str, Any]] = []
    evidence_ids: List[str] = []
    try:
        for raw in operations:
            op = str(raw.get("op") or "").strip()
            supports = raw.get("supports") or []
            anchors, retained = _support_anchors(
                supports,
                session=session,
                registry=registry,
                provider_context=provider_context,
                storage_dir=storage,
                aliases=aliases,
            )
            evidence_ids.extend(retained)
            if op == "remember":
                scope = str(raw.get("scope") or "world")
                mapped_scope = "user" if scope == "user" else world_scope_value
                key = str(raw.get("key") or "").strip()
                prepared.append({
                    "op": "create_node", "id": aliases.get(key) if key else None,
                    "scope": mapped_scope, "kind": raw.get("kind"), "content": raw.get("content"),
                    "tags": raw.get("tags") or [], "anchors": anchors,
                })
            elif op == "revise":
                node_id = _resolve_ref(raw.get("id"), aliases)
                node_record(storage, node_id)
                prepared.append({
                    "op": "update_node", "id": node_id, "expected_revision": raw.get("expected_revision"),
                    "content": raw.get("content"), "kind": raw.get("kind"),
                    "add_tags": raw.get("add_tags") or [], "remove_tags": raw.get("remove_tags") or [],
                    "anchors": anchors,
                })
            elif op == "relate":
                source = _resolve_ref(raw.get("source"), aliases); target = _resolve_ref(raw.get("target"), aliases)
                # Existing IDs are checked; @aliases become nodes in this same atomic changeset.
                if not str(raw.get("source") or "").startswith("@"):
                    node_record(storage, source)
                if not str(raw.get("target") or "").startswith("@"):
                    node_record(storage, target)
                prepared.append({"op": "create_edge", "source": source, "label": raw.get("relation"), "target": target, "anchors": anchors})
            elif op == "archive":
                node_id = _resolve_ref(raw.get("id"), aliases); node_record(storage, node_id)
                prepared.append({"op": "archive_node", "id": node_id, "expected_revision": raw.get("expected_revision")})
            elif op == "supersede":
                node_id = _resolve_ref(raw.get("id"), aliases); replacement = _resolve_ref(raw.get("replacement"), aliases)
                node_record(storage, node_id)
                if not str(raw.get("replacement") or "").startswith("@"):
                    node_record(storage, replacement)
                prepared.append({"op": "supersede_node", "id": node_id, "expected_revision": raw.get("expected_revision"), "replacement": replacement})
            elif op == "retire_relation":
                relation_id = str(raw.get("id") or "").strip(); edge_record(storage, relation_id)
                prepared.append({"op": "retire_edge", "id": relation_id, "expected_revision": raw.get("expected_revision")})
            else:
                raise ValueError(f"MEMORY_SIDECAR_OPERATION_INVALID:{op or '<empty>'}")
        change = apply_graph_operations(
            storage, prepared, execution_id=session.execution_id, turn=session.turn,
        )
    except (OSError, ValueError) as exc:
        return {"ok": False, "error_code": str(exc).split(":", 1)[0], "detail": str(exc), "focus": focus}
    affected = change.get("affected") or []
    # Memory is transversal to the current thought: when Main writes graph state
    # without choosing an explicit focus, keep the nodes it just touched hot for
    # the next ECC turn. This is a mechanical continuation aid, not a semantic
    # choice about importance. Explicit focus always wins.
    affected_nodes: List[str] = []
    for item in affected:
        if not isinstance(item, dict):
            continue
        for key_name in ("id", "source", "target", "replacement"):
            value = str(item.get(key_name) or "").strip()
            if value.startswith("mem-") and value not in affected_nodes:
                affected_nodes.append(value)
    if focus:
        session.memory_focus = focus
    elif affected_nodes:
        session.memory_focus = affected_nodes[-8:]
    return {
        "ok": True, "changed": bool(change.get("count")), "focus": list(session.memory_focus), "disposition": disposition,
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
        return {"status": "semantic", "kind": "request"}
    if kind == "memory":
        ref = str(anchor.get("source_ref") or "")
        if not ref:
            return {"status": "degraded", "kind": "memory", "reason": "missing_memory_ref"}
        if ref in stack:
            return {"status": "degraded", "kind": "memory", "memory_id": ref, "reason": "dependency_cycle"}
        try:
            raw = node_record(storage_dir, ref)
        except (OSError, ValueError):
            return {"status": "stale", "kind": "memory", "memory_id": ref, "reason": "dependency_missing"}
        evaluated = _evaluate_node(raw, registry=registry, runtime_ctx=runtime_ctx, storage_dir=storage_dir, node_cache=node_cache, stack=stack | {ref})
        status = str(evaluated.get("freshness") or "degraded")
        return {"status": status, "kind": "memory", "memory_id": ref}
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
        "id": node_id, "scope": raw.get("scope"), "kind": raw.get("kind"),
        "content": str(raw.get("content") or "")[:1400], "tags": list(raw.get("tags") or [])[:16],
        "revision": raw.get("revision"), "freshness": freshness,
        "sources": statuses[:8], "topology": copy.deepcopy(raw.get("topology") or {}),
        "retrieval": copy.deepcopy(raw.get("retrieval") or {}),
    }
    node_cache[node_id] = copy.deepcopy(out)
    return out


def memory_graph_view(
    session: Any,
    *,
    query: str,
    registry: Any,
    config: Dict[str, Any],
    provider_context: Dict[str, Any],
    limit: int = 14,
) -> Dict[str, Any]:
    storage, world_scope_id = _context(provider_context)
    if not storage or not world_scope_id:
        return {"available": False, "nodes": [], "edges": []}
    try:
        raw = retrieve_graph(
            storage,
            world_scope_value=world_scope(world_scope_id),
            query=str(query or ""),
            focus=session.memory_focus,
            limit=limit,
            execution_id=session.execution_id,
        )
    except (OSError, ValueError) as exc:
        return {"available": False, "nodes": [], "edges": [], "error": str(exc).split(":", 1)[0]}
    runtime_ctx = {
        "config": config or {}, "provider_context": provider_context or {}, "session": session,
        "grounding": material_items(session.observation_ledger), "observation_ledger": session.observation_ledger,
        "reality_epoch": int(session.reality_epoch),
    }
    node_cache: Dict[str, Dict[str, Any]] = {}
    nodes = [
        _evaluate_node(raw_node, registry=registry, runtime_ctx=runtime_ctx, storage_dir=storage, node_cache=node_cache, stack={str(raw_node.get('id') or '')})
        for raw_node in raw.get("nodes") or [] if isinstance(raw_node, dict)
    ]
    by_id = {str(item.get("id")): item for item in nodes}
    edges = []
    for raw_edge in raw.get("edges") or []:
        if not isinstance(raw_edge, dict):
            continue
        statuses = [
            _anchor_status(anchor, registry=registry, runtime_ctx=runtime_ctx, storage_dir=storage, node_cache=node_cache, stack=set())
            for anchor in raw_edge.get("anchors") or [] if isinstance(anchor, dict)
        ]
        if statuses:
            freshness = _combine_anchor_statuses(statuses)
        else:
            endpoint = [str((by_id.get(str(raw_edge.get(side))) or {}).get("freshness") or "unbound") for side in ("source", "target")]
            freshness = "stale" if endpoint and all(v == "stale" for v in endpoint) else ("degraded" if any(v in {"stale", "degraded"} for v in endpoint) else "fresh")
        edges.append({
            "id": raw_edge.get("id"), "source": raw_edge.get("source"), "relation": raw_edge.get("label"),
            "target": raw_edge.get("target"), "revision": raw_edge.get("revision"), "freshness": freshness,
            **({"sources": statuses[:4]} if statuses else {}),
        })
    counts = graph_counts(storage)
    return {
        "available": True, "nodes": nodes, "edges": edges,
        "retrieval": copy.deepcopy(raw.get("retrieval") or {}),
        "graph": counts,
        "trust_note": "freshness is mechanically derived from source anchors/hashes; semantic/unbound memory is not file-invalidated",
    }
