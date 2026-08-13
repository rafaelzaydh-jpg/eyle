"""Independent persistent Memory capability provider.

Memory is cognitive persistence, not a workspace/source-code capability. The
host supplies an opaque scope root and storage directory; Main owns memory
meaning while this provider owns storage mechanics.
"""
from __future__ import annotations

import copy
import os
import uuid
from typing import Any, Dict

from eyle.capabilities.registry import Provider
from eyle.contracts.capability import failure, physical_effect, result
from eyle.providers.memory_impl.memory import (
    activate_memory, continue_memory_view, apply_memory_changeset, memory_record,
)


def _ctx(ctx: Dict[str, Any]) -> Dict[str, Any]:
    values = (ctx or {}).get("provider_context") or {}
    item = values.get("memory") if isinstance(values, dict) else None
    return item if isinstance(item, dict) else {}


def _roots(ctx: Dict[str, Any]) -> tuple[str | None, str | None]:
    item = _ctx(ctx)
    base = item.get("storage_dir")
    scope = item.get("scope_root")
    return (str(base) if base else None, str(scope) if scope else None)


def _success(detail: Any, *, changed: bool = False, effect: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return result("success", True, True, changed=changed, detail=detail, physical_effect_value=effect)


def _search(arguments: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    base, scope = _roots(ctx)
    if not base or not scope:
        return failure("MEMORY_CONTEXT_UNAVAILABLE", "memory storage/scope context is unavailable", failure_scope="resource")
    limit = int(arguments.get("limit") or 12)
    seed = arguments.get("seed") if isinstance(arguments.get("seed"), dict) else {}
    try:
        frontier = str(arguments.get("frontier") or "").strip()
        if frontier:
            view = continue_memory_view(base, scope, frontier, limit=limit)
        else:
            view = activate_memory(
                base, scope, ids=[],
                region=str(seed.get("region") or "").strip() or None,
                tags=seed.get("tags") or [],
                text=str(arguments.get("query") or ""),
                related_to=seed.get("related_to") or [],
                limit=limit, include_inactive=False,
            )
    except (OSError, ValueError) as exc:
        return failure("MEMORY_READ_FAILED", str(exc), executed=True)
    return _success({"view": view, "count": len(view.get("memories") or [])})


def _store(arguments: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    base, scope = _roots(ctx)
    if not base or not scope:
        return failure("MEMORY_CONTEXT_UNAVAILABLE", "memory storage/scope context is unavailable", failure_scope="resource")
    grounding = (ctx or {}).get("grounding") or {}
    meta = arguments.get("meta") if isinstance(arguments.get("meta"), dict) else {}
    grounding_ids = [str(item) for item in meta.get("grounding_ids") or []]
    missing = [item for item in grounding_ids if item not in grounding]
    if missing:
        return failure("MEMORY_UNKNOWN_GROUNDING", ", ".join(missing))
    provenance = {"kind": "observation", "refs": grounding_ids} if grounding_ids else {"kind": "main"}
    memory_id = f"mem-{uuid.uuid4().hex[:16]}"
    region = str(meta.get("region") or "").strip() or f"scope:{os.path.basename(os.path.realpath(scope)) or 'active'}"
    operations = [{
        "op": "create_memory", "id": memory_id, "region": region,
        "content": str(arguments.get("text") or ""), "tags": meta.get("tags") or [],
        "provenance": provenance,
    }]
    try:
        for old_id in meta.get("supersedes") or []:
            old = memory_record(base, scope, str(old_id))
            operations.append({"op": "supersede_memory", "id": old["id"], "expected_revision": old["revision"], "superseded_by": memory_id})
        for relation in meta.get("relations") or []:
            if isinstance(relation, dict):
                operations.append({"op": "create_relation", "source": memory_id, "label": relation.get("label"), "target": relation.get("target"), "provenance": provenance})
        change = apply_memory_changeset(base, scope, operations)
        entry = memory_record(base, scope, memory_id)
    except (OSError, ValueError) as exc:
        return failure("MEMORY_WRITE_FAILED", str(exc), executed=True)
    return _success(
        {"memory": entry, "changeset_id": change["changeset_id"], "affected": change["count"]},
        changed=True,
        effect=physical_effect("memory.kernel", "store", "persistent", changed=True),
    )


def _coverage_search(arguments, result_value):
    detail = result_value.get("detail") if isinstance(result_value.get("detail"), dict) else {}
    view = detail.get("view") if isinstance(detail.get("view"), dict) else {}
    coverage = view.get("memory_coverage") if isinstance(view.get("memory_coverage"), dict) else {}
    if result_value.get("executed") is not True:
        return {}
    frontier = view.get("memory_frontier") if isinstance(view.get("memory_frontier"), dict) else None
    return {
        "scope": {"kind": "memory_kernel_view", "frontier": str(arguments.get("frontier") or "") or None},
        "examined": {"memories_materialized": len(view.get("memories") or [])},
        "complete": bool(result_value.get("ok") is True and coverage.get("complete") is True),
        "boundaries": ([{"kind": "memory_frontier", "remaining": int(frontier.get("remaining_count") or 0)}] if frontier else []),
    }


def _coverage_store(arguments, result_value):
    detail = result_value.get("detail") if isinstance(result_value.get("detail"), dict) else {}
    memory = detail.get("memory") if isinstance(detail.get("memory"), dict) else {}
    if result_value.get("executed") is not True:
        return {}
    return {
        "scope": {"kind": "memory_kernel_changeset"},
        "examined": {"memories_written": 1 if memory else 0, "entities_affected": int(detail.get("affected") or 0)},
        "complete": bool(result_value.get("ok") is True), "boundaries": [],
    }


def _public_arguments(arguments):
    out = {}
    for key in ("query", "frontier"):
        if arguments.get(key) is not None:
            out[key] = str(arguments.get(key))[:160]
    for key in ("seed", "meta"):
        if isinstance(arguments.get(key), dict):
            out[key] = copy.deepcopy(arguments.get(key))
    if arguments.get("limit") is not None:
        out["limit"] = int(arguments.get("limit"))
    return out


def _schema(properties=None, required=None):
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}


CAPABILITIES = {
    "search": {
        "description": "Navigate bounded persistent Memory.", "availability": "memory",
        "produces_grounding": False, "effect": "observe",
        "returns": "Bounded Memory Nodes, MemoryCoverage and optional MemoryFrontier.",
        "caveats": ["Memory is prior cognitive context, not proof of current external state."],
        "input_schema": _schema({
            "query": {"type": "string", "maxLength": 1000, "description": "Optional lexical Memory seed."},
            "seed": {"type": "object", "additionalProperties": False, "properties": {
                "region": {"type": "string", "minLength": 1, "maxLength": 240},
                "tags": {"type": "array", "maxItems": 20, "items": {"type": "string", "minLength": 1, "maxLength": 96}},
                "related_to": {"type": "array", "maxItems": 20, "items": {"type": "string", "pattern": r"^mem-[A-Za-z0-9._-]+$"}},
            }},
            "frontier": {"type": "string", "pattern": r"^mf-[A-Za-z0-9._-]+$"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 30},
        }),
        "fn": _search, "observe": lambda _a, _r: [], "coverage": _coverage_search,
        "public_arguments": _public_arguments,
    },
    "store": {
        "description": "Persist one Memory Node with optional links.", "availability": "memory",
        "produces_grounding": False, "effect": "mutate",
        "returns": "Created Memory Node and ChangeSet metadata.",
        "caveats": ["Kernel validates structure; Main owns Memory meaning."],
        "input_schema": _schema({
            "text": {"type": "string", "minLength": 1, "maxLength": 8000},
            "meta": {"type": "object", "additionalProperties": False, "properties": {
                "region": {"type": "string", "minLength": 1, "maxLength": 240},
                "tags": {"type": "array", "maxItems": 30, "items": {"type": "string", "minLength": 1, "maxLength": 96}},
                "grounding_ids": {"type": "array", "maxItems": 30, "items": {"type": "string", "pattern": r"^mat-[0-9]+$"}},
                "supersedes": {"type": "array", "maxItems": 20, "items": {"type": "string", "pattern": r"^mem-[A-Za-z0-9._-]+$"}},
                "relations": {"type": "array", "maxItems": 30, "items": {"type": "object", "additionalProperties": False, "required": ["label", "target"], "properties": {
                    "label": {"type": "string", "minLength": 1, "maxLength": 120},
                    "target": {"type": "string", "pattern": r"^mem-[A-Za-z0-9._-]+$"},
                }}},
            }},
        }, ["text"]),
        "fn": _store, "observe": lambda _a, _r: [], "coverage": _coverage_store,
        "public_arguments": _public_arguments,
    },
}


def _available(_name, _spec, ctx):
    base, scope = _roots(ctx)
    return bool(base and scope)


def _describe(ctx):
    base, scope = _roots(ctx)
    return {"connected": bool(base and scope), "resources": {"persistent_memory": {"available": bool(base and scope), "kind": "cognitive_persistence"}}}


def _validate_config(value):
    if value is None:
        return
    if not isinstance(value, dict) or value:
        raise ValueError("MEMORY_PROVIDER_CONFIG_INVALID")


def get_provider():
    return Provider(provider_id="memory", capabilities=CAPABILITIES, available=_available, describe=_describe, validate_config=_validate_config)
