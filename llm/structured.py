"""Canonical structured output contract for the Eyle ECC general-agent core.

The cognitive vocabulary is still only Explorar, Construir, Concluir. Persistent
Memory and transient Objective State are transversal semantic sidecars of every
move, never additional actions/tools or Runtime-owned planners.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Dict


class StructuredResponseError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


_MEMORY_REF = r"^(?:mem-[A-Za-z0-9._-]+|@[A-Za-z0-9_-]+)$"
_MEMORY_SUPPORT_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["material"]},
                "material_id": {"type": "string", "pattern": r"^mat-[0-9]+$"},
                "selector": {"type": "object"},
            },
            "required": ["kind", "material_id"], "additionalProperties": False,
        },
        {
            "type": "object", "properties": {"kind": {"type": "string", "enum": ["request"]}},
            "required": ["kind"], "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["memory"]},
                "memory_id": {"type": "string", "pattern": _MEMORY_REF},
            },
            "required": ["kind", "memory_id"], "additionalProperties": False,
        },
    ]
}
_MEMORY_OP_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["remember"]},
                "key": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"^[A-Za-z0-9_-]+$"},
                "scope": {"type": "string", "enum": ["world", "user"]},
                "kind": {"type": "string", "minLength": 1, "maxLength": 96},
                "content": {"type": "string", "minLength": 1, "maxLength": 12000},
                "tags": {"type": "array", "maxItems": 20, "items": {"type": "string", "minLength": 1, "maxLength": 96}},
                "supports": {"type": "array", "maxItems": 12, "items": _MEMORY_SUPPORT_SCHEMA},
            },
            "required": ["op", "scope", "kind", "content"], "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["revise"]},
                "id": {"type": "string", "pattern": _MEMORY_REF},
                "expected_revision": {"type": "integer", "minimum": 1},
                "kind": {"type": "string", "minLength": 1, "maxLength": 96},
                "content": {"type": "string", "minLength": 1, "maxLength": 12000},
                "add_tags": {"type": "array", "maxItems": 20, "items": {"type": "string", "minLength": 1, "maxLength": 96}},
                "remove_tags": {"type": "array", "maxItems": 20, "items": {"type": "string", "minLength": 1, "maxLength": 96}},
                "supports": {"type": "array", "maxItems": 12, "items": _MEMORY_SUPPORT_SCHEMA},
            },
            "required": ["op", "id", "expected_revision"], "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["relate"]},
                "source": {"type": "string", "pattern": _MEMORY_REF},
                "relation": {"type": "string", "minLength": 1, "maxLength": 120},
                "target": {"type": "string", "pattern": _MEMORY_REF},
                "supports": {"type": "array", "maxItems": 12, "items": _MEMORY_SUPPORT_SCHEMA},
            },
            "required": ["op", "source", "relation", "target"], "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["archive"]},
                "id": {"type": "string", "pattern": _MEMORY_REF},
                "expected_revision": {"type": "integer", "minimum": 1},
            },
            "required": ["op", "id", "expected_revision"], "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["supersede"]},
                "id": {"type": "string", "pattern": _MEMORY_REF},
                "expected_revision": {"type": "integer", "minimum": 1},
                "replacement": {"type": "string", "pattern": _MEMORY_REF},
            },
            "required": ["op", "id", "expected_revision", "replacement"], "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["retire_relation"]},
                "id": {"type": "string", "pattern": r"^rel-[A-Za-z0-9._-]+$"},
                "expected_revision": {"type": "integer", "minimum": 1},
            },
            "required": ["op", "id", "expected_revision"], "additionalProperties": False,
        },
    ]
}
_MEMORY_SCHEMA = {
    "type": "object",
    "properties": {
        "focus": {"type": "array", "maxItems": 12, "items": {"type": "string", "minLength": 1, "maxLength": 160}},
        "disposition": {"type": "string", "enum": ["unchanged", "updated"]},
        "operations": {"type": "array", "maxItems": 16, "items": _MEMORY_OP_SCHEMA},
    },
    "required": ["focus", "disposition", "operations"],
    "additionalProperties": False,
    "allOf": [
        {
            "if": {"properties": {"disposition": {"const": "updated"}}, "required": ["disposition"]},
            "then": {"properties": {"operations": {"minItems": 1}}},
        },
        {
            "if": {"properties": {"disposition": {"const": "unchanged"}}, "required": ["disposition"]},
            "then": {"properties": {"operations": {"maxItems": 0}}},
        },
    ],
}

_OBJECTIVE_CHILD_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"^[A-Za-z0-9_-]+$"},
        "description": {"type": "string", "minLength": 1, "maxLength": 2000},
        "status": {"type": "string", "minLength": 1, "maxLength": 96},
        "outcome": {"type": "string", "minLength": 1, "maxLength": 4000},
    },
    "required": ["key", "description", "status"],
    "additionalProperties": False,
}
_OBJECTIVE_STATE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
        "status": {"type": "string", "minLength": 1, "maxLength": 96},
        "children": {"type": "array", "maxItems": 16, "items": _OBJECTIVE_CHILD_SCHEMA},
        "constraints": {"type": "array", "maxItems": 16, "items": {"type": "string", "minLength": 1, "maxLength": 1000}},
    },
    "required": ["summary", "status", "children", "constraints"],
    "additionalProperties": False,
}
_OBJECTIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "disposition": {"type": "string", "enum": ["unchanged", "updated", "cleared"]},
        "state": {"oneOf": [{"type": "null"}, _OBJECTIVE_STATE_SCHEMA]},
    },
    "required": ["disposition", "state"],
    "additionalProperties": False,
    "allOf": [
        {
            "if": {"properties": {"disposition": {"const": "updated"}}, "required": ["disposition"]},
            "then": {"properties": {"state": _OBJECTIVE_STATE_SCHEMA}},
        },
        {
            "if": {"properties": {"disposition": {"enum": ["unchanged", "cleared"]}}, "required": ["disposition"]},
            "then": {"properties": {"state": {"type": "null"}}},
        },
    ],
}

_ECC_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["explorar"]},
                "operation": {"type": "string", "minLength": 1, "pattern": r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?$"},
                "arguments": {"type": "object"},
                "objective": _OBJECTIVE_SCHEMA,
                "memory": _MEMORY_SCHEMA,
            },
            "required": ["type", "operation", "arguments", "objective", "memory"], "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["construir"]},
                "operation": {"type": "string", "minLength": 1, "pattern": r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?$"},
                "arguments": {"type": "object"},
                "objective": _OBJECTIVE_SCHEMA,
                "memory": _MEMORY_SCHEMA,
            },
            "required": ["type", "operation", "arguments", "objective", "memory"], "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["concluir"]},
                "response": {"type": "string", "minLength": 1},
                "objective": _OBJECTIVE_SCHEMA,
                "memory": _MEMORY_SCHEMA,
            },
            "required": ["type", "response", "objective", "memory"], "additionalProperties": False,
        },
    ]
}


def schema_for_profile(profile: str) -> Dict[str, Any]:
    if profile != "ecc":
        raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
    return deepcopy(_ECC_SCHEMA)


def json_schema_response_format(profile: str) -> Dict[str, Any]:
    schema = schema_for_profile(profile)
    return {"type": "json_schema", "json_schema": {"name": "eyle_ecc_decision", "strict": True, "schema": schema}}


def mandatory_top_level_keys(profile: str) -> tuple[str, ...]:
    schema_for_profile(profile)
    return ("type", "objective", "memory")


def contract_instruction(profile: str) -> str:
    schema_for_profile(profile)
    return (
        'Reply with one ECC JSON object only. type must be explorar, construir, or concluir. '
        'Always include objective and memory. If Objective stays the same, use '
        'objective={"disposition":"unchanged","state":null}. If Memory stays the same, use '
        'memory={"focus":[],"disposition":"unchanged","operations":[]}. For explorar or construir, '
        'use a short operation name listed for that move.'
    )


def _object(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise StructuredResponseError("STRUCTURED_EMPTY", "structured response is empty")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StructuredResponseError("STRUCTURED_JSON_INVALID", f"response must be exactly one JSON object: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise StructuredResponseError("STRUCTURED_OBJECT_REQUIRED", "top-level JSON must be an object")
    return value


def observed_top_level(raw: Any) -> Dict[str, Any] | None:
    try:
        return _object(raw)
    except StructuredResponseError:
        return None


def _clean_json_value(value: Any, label: str, *, depth: int = 0) -> Any:
    if depth > 4:
        raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} selector too deep")
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > 1000:
            raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} selector string too large")
        return value
    if isinstance(value, list):
        if len(value) > 32:
            raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} selector array too large")
        return [_clean_json_value(v, label, depth=depth + 1) for v in value]
    if isinstance(value, dict):
        if len(value) > 32:
            raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} selector object too large")
        out = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 120:
                raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} selector key invalid")
            out[key] = _clean_json_value(item, label, depth=depth + 1)
        return out
    raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} selector value invalid")


def _clean_support(raw: Any, label: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} support must be object")
    kind = raw.get("kind")
    if kind == "request":
        if set(raw) != {"kind"}:
            raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} request support shape invalid")
        return {"kind": "request"}
    if kind == "memory":
        if set(raw) != {"kind", "memory_id"} or not isinstance(raw.get("memory_id"), str) or re.fullmatch(_MEMORY_REF, raw["memory_id"]) is None:
            raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} memory support invalid")
        return {"kind": "memory", "memory_id": raw["memory_id"]}
    if kind != "material":
        raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} support kind invalid")
    if set(raw) - {"kind", "material_id", "selector"}:
        raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} material support shape invalid")
    material_id = raw.get("material_id")
    if not isinstance(material_id, str) or re.fullmatch(r"mat-[0-9]+", material_id) is None:
        raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} material_id invalid")
    out = {"kind": "material", "material_id": material_id}
    if "selector" in raw:
        if not isinstance(raw.get("selector"), dict):
            raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} selector must be object")
        selector = _clean_json_value(raw.get("selector"), f"{label}.selector")
        if len(json.dumps(selector, ensure_ascii=False, separators=(",", ":"), default=str)) > 4000:
            raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} selector too large")
        out["selector"] = selector
    return out


def _ref(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(_MEMORY_REF, value) is None:
        raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} memory reference invalid")
    return value


def _string_list(value: Any, label: str, maximum: int, item_max: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} invalid")
    out = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > item_max:
            raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} item invalid")
        out.append(raw.strip())
    return out


def _clean_memory(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"focus", "disposition", "operations"}:
        raise StructuredResponseError("ECC_MEMORY_REQUIRED", "memory must contain exactly focus, disposition and operations")
    focus = _string_list(raw.get("focus"), "memory.focus", 12, 160)
    disposition = raw.get("disposition")
    if disposition not in {"unchanged", "updated"}:
        raise StructuredResponseError("ECC_MEMORY_INVALID", "memory.disposition must be unchanged or updated")
    operations = raw.get("operations")
    if not isinstance(operations, list) or len(operations) > 16:
        raise StructuredResponseError("ECC_MEMORY_INVALID", "memory.operations must be an array with at most 16 items")
    clean_ops = []
    for index, item in enumerate(operations, 1):
        label = f"memory.operations[{index}]"
        if not isinstance(item, dict):
            raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} must be object")
        op = item.get("op")
        supports = [_clean_support(v, label) for v in item.get("supports") or []]
        if len(supports) > 12:
            raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label}.supports too large")
        if op == "remember":
            allowed = {"op", "key", "scope", "kind", "content", "tags", "supports"}
            if set(item) - allowed or item.get("scope") not in {"world", "user"}:
                raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} remember shape invalid")
            kind, content = item.get("kind"), item.get("content")
            if not isinstance(kind, str) or not kind.strip() or len(kind.strip()) > 96 or not isinstance(content, str) or not content.strip() or len(content.strip()) > 12000:
                raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} remember content invalid")
            out = {"op": "remember", "scope": item["scope"], "kind": kind.strip(), "content": content.strip()}
            key = item.get("key")
            if key is not None:
                if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_-]+", key) is None or len(key) > 64:
                    raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label}.key invalid")
                out["key"] = key
            tags = _string_list(item.get("tags"), f"{label}.tags", 20, 96)
            if tags: out["tags"] = tags
            if supports: out["supports"] = supports
            clean_ops.append(out); continue
        if op == "revise":
            allowed = {"op", "id", "expected_revision", "kind", "content", "add_tags", "remove_tags", "supports"}
            if set(item) - allowed:
                raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} revise shape invalid")
            revision = item.get("expected_revision")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label}.expected_revision invalid")
            out = {"op": "revise", "id": _ref(item.get("id"), label), "expected_revision": revision}
            if item.get("kind") is not None:
                if not isinstance(item["kind"], str) or not item["kind"].strip() or len(item["kind"].strip()) > 96: raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label}.kind invalid")
                out["kind"] = item["kind"].strip()
            if item.get("content") is not None:
                if not isinstance(item["content"], str) or not item["content"].strip() or len(item["content"].strip()) > 12000: raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label}.content invalid")
                out["content"] = item["content"].strip()
            add_tags = _string_list(item.get("add_tags"), f"{label}.add_tags", 20, 96); remove_tags = _string_list(item.get("remove_tags"), f"{label}.remove_tags", 20, 96)
            if add_tags: out["add_tags"] = add_tags
            if remove_tags: out["remove_tags"] = remove_tags
            if supports: out["supports"] = supports
            clean_ops.append(out); continue
        if op == "relate":
            allowed = {"op", "source", "relation", "target", "supports"}
            relation = item.get("relation")
            if set(item) - allowed or not isinstance(relation, str) or not relation.strip() or len(relation.strip()) > 120:
                raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} relate shape invalid")
            out = {"op": "relate", "source": _ref(item.get("source"), label), "relation": relation.strip(), "target": _ref(item.get("target"), label)}
            if supports: out["supports"] = supports
            clean_ops.append(out); continue
        if op in {"archive", "retire_relation"}:
            allowed = {"op", "id", "expected_revision"}
            revision = item.get("expected_revision")
            if set(item) != allowed or not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} {op} shape invalid")
            identity = item.get("id")
            pattern = r"rel-[A-Za-z0-9._-]+" if op == "retire_relation" else _MEMORY_REF
            if not isinstance(identity, str) or re.fullmatch(pattern, identity) is None:
                raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label}.id invalid")
            clean_ops.append({"op": op, "id": identity, "expected_revision": revision}); continue
        if op == "supersede":
            if set(item) != {"op", "id", "expected_revision", "replacement"}:
                raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label} supersede shape invalid")
            revision = item.get("expected_revision")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label}.expected_revision invalid")
            clean_ops.append({"op": "supersede", "id": _ref(item.get("id"), label), "expected_revision": revision, "replacement": _ref(item.get("replacement"), label)}); continue
        raise StructuredResponseError("ECC_MEMORY_INVALID", f"{label}.op invalid")
    if disposition == "updated" and not clean_ops:
        raise StructuredResponseError("ECC_MEMORY_INVALID", "memory.disposition=updated requires at least one operation")
    if disposition == "unchanged" and clean_ops:
        raise StructuredResponseError("ECC_MEMORY_INVALID", "memory.disposition=unchanged cannot contain operations")
    return {"focus": focus, "disposition": disposition, "operations": clean_ops}


def _clean_objective_state(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"summary", "status", "children", "constraints"}:
        raise StructuredResponseError("ECC_OBJECTIVE_INVALID", "objective.state must contain exactly summary, status, children and constraints")
    summary = raw.get("summary")
    status = raw.get("status")
    if not isinstance(summary, str) or not summary.strip() or len(summary.strip()) > 2000:
        raise StructuredResponseError("ECC_OBJECTIVE_INVALID", "objective.state.summary invalid")
    if not isinstance(status, str) or not status.strip() or len(status.strip()) > 96:
        raise StructuredResponseError("ECC_OBJECTIVE_INVALID", "objective.state.status invalid")
    children = raw.get("children")
    if not isinstance(children, list) or len(children) > 16:
        raise StructuredResponseError("ECC_OBJECTIVE_INVALID", "objective.state.children invalid")
    clean_children = []
    seen = set()
    for index, item in enumerate(children, 1):
        label = f"objective.state.children[{index}]"
        if not isinstance(item, dict) or set(item) - {"key", "description", "status", "outcome"} or not {"key", "description", "status"}.issubset(item):
            raise StructuredResponseError("ECC_OBJECTIVE_INVALID", f"{label} shape invalid")
        key, description, child_status = item.get("key"), item.get("description"), item.get("status")
        if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_-]+", key) is None or len(key) > 64 or key in seen:
            raise StructuredResponseError("ECC_OBJECTIVE_INVALID", f"{label}.key invalid")
        if not isinstance(description, str) or not description.strip() or len(description.strip()) > 2000:
            raise StructuredResponseError("ECC_OBJECTIVE_INVALID", f"{label}.description invalid")
        if not isinstance(child_status, str) or not child_status.strip() or len(child_status.strip()) > 96:
            raise StructuredResponseError("ECC_OBJECTIVE_INVALID", f"{label}.status invalid")
        out = {"key": key, "description": description.strip(), "status": child_status.strip()}
        if "outcome" in item:
            outcome = item.get("outcome")
            if not isinstance(outcome, str) or not outcome.strip() or len(outcome.strip()) > 4000:
                raise StructuredResponseError("ECC_OBJECTIVE_INVALID", f"{label}.outcome invalid")
            out["outcome"] = outcome.strip()
        seen.add(key)
        clean_children.append(out)
    constraints = _string_list(raw.get("constraints"), "objective.state.constraints", 16, 1000)
    return {"summary": summary.strip(), "status": status.strip(), "children": clean_children, "constraints": constraints}


def _clean_objective(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"disposition", "state"}:
        raise StructuredResponseError("ECC_OBJECTIVE_REQUIRED", "objective must contain exactly disposition and state")
    disposition = raw.get("disposition")
    if disposition not in {"unchanged", "updated", "cleared"}:
        raise StructuredResponseError("ECC_OBJECTIVE_INVALID", "objective.disposition must be unchanged, updated or cleared")
    state = raw.get("state")
    if disposition == "updated":
        return {"disposition": disposition, "state": _clean_objective_state(state)}
    if state is not None:
        raise StructuredResponseError("ECC_OBJECTIVE_INVALID", f"objective.state must be null when disposition is {disposition}")
    return {"disposition": disposition, "state": None}


def parse_ecc_response(raw: Any) -> Dict[str, Any]:
    value = _object(raw)
    allowed = {"type", "operation", "arguments", "response", "objective", "memory"}
    extra = sorted(set(value) - allowed)
    if extra:
        raise StructuredResponseError("ECC_UNKNOWN_KEYS", "unknown ECC field(s): " + ", ".join(extra))
    kind = value.get("type")
    if kind not in {"explorar", "construir", "concluir"}:
        raise StructuredResponseError("ECC_TYPE_INVALID", "type must be explorar, construir or concluir")
    if "objective" not in value:
        raise StructuredResponseError("ECC_OBJECTIVE_REQUIRED", "every ECC move requires the internal objective sidecar")
    if "memory" not in value:
        raise StructuredResponseError("ECC_MEMORY_REQUIRED", "every ECC move requires the internal memory sidecar")
    normalized: Dict[str, Any] = {"type": kind, "objective": _clean_objective(value.get("objective")), "memory": _clean_memory(value.get("memory"))}
    if kind in {"explorar", "construir"}:
        if "response" in value:
            raise StructuredResponseError("ECC_SHAPE_INVALID", f"{kind} cannot contain response")
        operation, arguments = value.get("operation"), value.get("arguments")
        if not isinstance(operation, str) or not operation.strip() or not isinstance(arguments, dict):
            raise StructuredResponseError("ECC_OPERATION_INVALID", f"{kind} requires operation and object arguments")
        operation = operation.strip()
        for redundant_prefix in ("explorar.", "construir."):
            if operation.startswith(redundant_prefix):
                operation = operation[len(redundant_prefix):]; break
        if re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?", operation) is None:
            raise StructuredResponseError("ECC_OPERATION_INVALID", "operation must be a short ECC operation name")
        normalized["operation"] = operation; normalized["arguments"] = dict(arguments)
    else:
        if "operation" in value or "arguments" in value:
            raise StructuredResponseError("ECC_SHAPE_INVALID", "concluir cannot contain operation or arguments")
        response = value.get("response")
        if not isinstance(response, str) or not response.strip():
            raise StructuredResponseError("ECC_RESPONSE_INVALID", "concluir requires a non-empty response")
        normalized["response"] = response
    return normalized


def parse_profile_response(raw: Any, profile: str) -> Dict[str, Any]:
    if profile != "ecc":
        raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
    return parse_ecc_response(raw)
