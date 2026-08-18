"""Canonical structured cognition contract owned by Eyle.

Eyle defines the current ECC wire shape and Memory semantics. The Adapter receives
this schema, performs transport-only JSON recovery/validation and at most one
format repair. Core does not negotiate provider quirks or repair transport syntax.
"""
from __future__ import annotations

from eyle.contracts.memory import EPISTEMIC_SCHEMA, normalize_epistemic
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
_MATERIAL_REF = r"^mat-[0-9]+$"
_RELATION_REF = r"^rel-[A-Za-z0-9._-]+$"

# Rev2.8.4: the schema exposed to the Adapter describes the same Memory wire
# contract that Eyle validates locally.  Rev2.8.3 only declared
# ``arguments: object`` and then enforced a much narrower shape after the
# Adapter had already called the response valid.  That split contract caused
# EYLE_MEMORY_INVALID on otherwise recoverable support aliases.
_CANONICAL_SUPPORT_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["request"]}},
            "required": ["kind"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["memory"]},
                "memory_id": {"type": "string", "pattern": _MEMORY_REF},
                "revision": {"type": "integer", "minimum": 1},
            },
            "required": ["kind", "memory_id"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["material"]},
                "material_id": {"type": "string", "pattern": _MATERIAL_REF},
                # Selector is provider-owned opaque JSON. Runtime validates
                # JSON shape, not semantic depth/importance.
                "selector": {"type": "object"},
            },
            "required": ["kind", "material_id"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["relation"]},
                "relation_id": {"type": "string", "pattern": _RELATION_REF},
                "revision": {"type": "integer", "minimum": 1},
            },
            "required": ["kind", "relation_id"],
            "additionalProperties": False,
        },
    ]
}
_SAFE_SUPPORT_STRING = {
    "type": "string",
    "pattern": r"^(?:request|current_request|mat-[0-9]+|mem-[A-Za-z0-9._-]+|rel-[A-Za-z0-9._-]+|@[A-Za-z0-9_-]+|material:mat-[0-9]+|memory:(?:mem-[A-Za-z0-9._-]+|@[A-Za-z0-9_-]+)|relation:rel-[A-Za-z0-9._-]+)$",
}
# Provider-wire aliases are intentionally represented in the schema too. This
# lets the existing generic Adapter validate exactly what Eyle can normalize
# instead of rejecting a safe alias before local canonicalization runs.
_SUPPORT_WIRE_SCHEMA = {
    "oneOf": [
        *_CANONICAL_SUPPORT_SCHEMA["oneOf"],
        {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "pattern": _MEMORY_REF},
                "revision": {"type": "integer", "minimum": 1},
            },
            "required": ["memory_id"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "relation_id": {"type": "string", "pattern": _RELATION_REF},
                "revision": {"type": "integer", "minimum": 1},
            },
            "required": ["relation_id"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "material_id": {"type": "string", "pattern": _MATERIAL_REF},
                "selector": {"type": "object"},
            },
            "required": ["material_id"],
            "additionalProperties": False,
        },
        _SAFE_SUPPORT_STRING,
    ]
}
_SUPPORTS_SCHEMA = {
    "oneOf": [
        {"type": "array", "items": _SUPPORT_WIRE_SCHEMA},
        _SUPPORT_WIRE_SCHEMA,
    ]
}
_STRING_ARRAY_SCHEMA = {"type": "array", "items": {"type": "string", "minLength": 1}}

_EPISTEMIC_SCHEMA = EPISTEMIC_SCHEMA


_ASSOCIATIVE_RECALL_SCHEMA = {
    "type": "object",
    "properties": {
        # These are Main-authored retrieval cues, not evidence or Runtime-ranked
        # semantics. Runtime only stores/indexes the exact strings Main chose.
        "aliases": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "concepts": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "cues": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "additionalProperties": False,
}

def _memory_action_schema(op: str, properties: Dict[str, Any], required: list[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": [op]},
            "arguments": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
        "required": ["op", "arguments"],
        "additionalProperties": False,
    }

_MEMORY_ACTION_SCHEMA = {
    "oneOf": [
        _memory_action_schema(
            "remember",
            {
                "key": {"type": "string", "pattern": r"^[A-Za-z0-9_-]+$"},
                "scope": {"type": "string", "enum": ["world", "user"]},
                "retention": {"type": "string", "enum": ["temporary", "persistent"]},
                "kind": {"type": "string", "minLength": 1},
                "content": {"type": "string", "minLength": 1},
                "epistemic": _EPISTEMIC_SCHEMA,
                "recall": _ASSOCIATIVE_RECALL_SCHEMA,
                "tags": _STRING_ARRAY_SCHEMA,
                "supports": _SUPPORTS_SCHEMA,
            },
            ["scope", "retention", "kind", "content"],
        ),
        _memory_action_schema(
            "revise",
            {
                "id": {"type": "string", "pattern": _MEMORY_REF},
                "expected_revision": {"type": "integer", "minimum": 1},
                "retention": {"type": "string", "enum": ["temporary", "persistent"]},
                "kind": {"type": "string", "minLength": 1},
                "content": {"type": "string", "minLength": 1},
                "epistemic": _EPISTEMIC_SCHEMA,
                "recall": _ASSOCIATIVE_RECALL_SCHEMA,
                "add_recall": _ASSOCIATIVE_RECALL_SCHEMA,
                "remove_recall": _ASSOCIATIVE_RECALL_SCHEMA,
                "add_tags": _STRING_ARRAY_SCHEMA,
                "remove_tags": _STRING_ARRAY_SCHEMA,
                "supports": _SUPPORTS_SCHEMA,
            },
            ["id", "expected_revision"],
        ),
        _memory_action_schema(
            "relate",
            {
                "source": {"type": "string", "pattern": _MEMORY_REF},
                "relation": {"type": "string", "minLength": 1},
                "target": {"type": "string", "pattern": _MEMORY_REF},
                "epistemic": _EPISTEMIC_SCHEMA,
                "supports": _SUPPORTS_SCHEMA,
            },
            ["source", "relation", "target"],
        ),
        _memory_action_schema(
            "revise_relation",
            {
                "id": {"type": "string", "pattern": r"^rel-[A-Za-z0-9._-]+$"},
                "expected_revision": {"type": "integer", "minimum": 1},
                "relation": {"type": "string", "minLength": 1},
                "epistemic": _EPISTEMIC_SCHEMA,
                "supports": _SUPPORTS_SCHEMA,
            },
            ["id", "expected_revision"],
        ),
        _memory_action_schema(
            "task_status",
            {
                "id": {"type": "string", "pattern": _MEMORY_REF},
                "expected_state_revision": {"type": "integer", "minimum": 1},
                "state": {"type": "string", "enum": ["active", "blocked", "resolved", "cancelled"]},
            },
            ["id", "expected_state_revision", "state"],
        ),
        _memory_action_schema(
            "archive",
            {
                "id": {"type": "string", "pattern": _MEMORY_REF},
                "expected_revision": {"type": "integer", "minimum": 1},
            },
            ["id", "expected_revision"],
        ),
        _memory_action_schema(
            "supersede",
            {
                "id": {"type": "string", "pattern": _MEMORY_REF},
                "expected_revision": {"type": "integer", "minimum": 1},
                "replacement": {"type": "string", "pattern": _MEMORY_REF},
            },
            ["id", "expected_revision", "replacement"],
        ),
        _memory_action_schema(
            "retire_relation",
            {
                "id": {"type": "string", "pattern": r"^rel-[A-Za-z0-9._-]+$"},
                "expected_revision": {"type": "integer", "minimum": 1},
            },
            ["id", "expected_revision"],
        ),
    ]
}
_MEMORY_DELTA_SCHEMA = {"type": "array", "items": _MEMORY_ACTION_SCHEMA}
_OPERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {"type": "string", "minLength": 1, "pattern": r"^[A-Za-z0-9_-]+$"},
        "arguments": {"type": "object"},
    },
    "required": ["operation", "arguments"],
    "additionalProperties": False,
}
_DECISION_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["explorar"]},
                "operations": {"type": "array", "minItems": 1, "items": _OPERATION_SCHEMA},
            },
            "required": ["type", "operations"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["construir"]},
                "operation": _OPERATION_SCHEMA["properties"]["operation"],
                "arguments": {"type": "object"},
            },
            "required": ["type", "operation", "arguments"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["concluir"]},
                "response": {"type": "string", "minLength": 1},
                "choices": {"type": "array", "minItems": 2, "items": {"type": "string", "minLength": 1}},
                "allow_free_text": {"type": "boolean"},
            },
            "required": ["type", "response"],
            "additionalProperties": False,
        },
    ]
}
_EYLE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": _DECISION_SCHEMA,
        "memory_delta": _MEMORY_DELTA_SCHEMA,
    },
    "required": ["decision", "memory_delta"],
    "additionalProperties": False,
}

# Provider-facing ECC wire: strict about the decision family, deliberately
# shallow about Memory. This lets the Adapter reject malformed ECC such as
# explorar.operations=[] while preserving the invariant that an invalid
# Memory sidecar never vetoes an otherwise valid ECC decision.
_WIRE_MEMORY_DELTA_SCHEMA = {"type": "array"}
_EYLE_WIRE_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["explorar"]},
                "operations": {"type": "array", "minItems": 1, "items": _OPERATION_SCHEMA},
                "memory_delta": _WIRE_MEMORY_DELTA_SCHEMA,
            },
            "required": ["type", "operations", "memory_delta"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["construir"]},
                "operation": _OPERATION_SCHEMA["properties"]["operation"],
                "arguments": {"type": "object"},
                "memory_delta": _WIRE_MEMORY_DELTA_SCHEMA,
            },
            "required": ["type", "operation", "arguments", "memory_delta"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["concluir"]},
                "response": {"type": "string", "minLength": 1},
                "choices": {"type": "array", "minItems": 2, "items": {"type": "string", "minLength": 1}},
                "allow_free_text": {"type": "boolean"},
                "memory_delta": _WIRE_MEMORY_DELTA_SCHEMA,
            },
            "required": ["type", "response", "memory_delta"],
            "additionalProperties": False,
        },
    ]
}


def schema_for_profile(profile: str) -> Dict[str, Any]:
    """Return Eyle's strict internal canonical schema."""
    if profile != "ecc":
        raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
    return deepcopy(_EYLE_RESPONSE_SCHEMA)


def wire_schema_for_profile(profile: str) -> Dict[str, Any]:
    """Return the current provider-facing ECC wire schema."""
    if profile != "ecc":
        raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
    return deepcopy(_EYLE_WIRE_SCHEMA)


def json_schema_response_format(profile: str) -> Dict[str, Any]:
    schema = wire_schema_for_profile(profile)
    return {"type": "json_schema", "json_schema": {"name": "eyle_cognition_wire", "strict": True, "schema": schema}}


def mandatory_top_level_keys(profile: str) -> tuple[str, ...]:
    wire_schema_for_profile(profile)
    return ("type", "memory_delta")




def parse_json_representation(raw: Any) -> Any:
    """Decode the current JSON representation only.

    Mechanical fence/prose/fragment recovery belongs to the Adapter.
    """
    if isinstance(raw, (dict, list)):
        return deepcopy(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise StructuredResponseError("STRUCTURED_EMPTY", "structured response is empty")
    try:
        return json.loads(raw.strip())
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise StructuredResponseError(
            "STRUCTURED_JSON_INVALID",
            f"response is not valid current JSON: {type(exc).__name__}",
        ) from exc


def _canonicalize_wire(raw: Any) -> tuple[Dict[str, Any], list[str]]:
    """Split the current flat ECC wire into Eyle's internal envelope.

    No historical wrappers, field aliases or movement aliases are accepted here.
    Transport syntax repair belongs to the Adapter; Core only maps the current
    Eyle-owned wire into its internal decision + Memory sidecar representation.
    """
    value = parse_json_representation(raw)
    if not isinstance(value, dict):
        raise StructuredResponseError("STRUCTURED_OBJECT_REQUIRED", "top-level wire value must be an object")
    if "type" not in value:
        raise StructuredResponseError("EYLE_ENVELOPE_INVALID", "current ECC wire requires top-level type")
    memory_source = deepcopy(value.get("memory_delta", []))
    decision = {key: deepcopy(item) for key, item in value.items() if key != "memory_delta"}
    return {"decision": decision, "memory_delta": memory_source}, []


def canonicalize_wire_response(raw: Any) -> Dict[str, Any]:
    """Deterministically map the current flat wire into the canonical envelope."""
    envelope, _ = _canonicalize_wire(raw)
    return envelope


def wire_canonicalization_steps(raw: Any) -> list[str]:
    """Diagnostic-only list of deterministic boundary normalizations."""
    _, steps = _canonicalize_wire(raw)
    return steps


def observed_top_level(raw: Any) -> Dict[str, Any] | None:
    try:
        value = parse_json_representation(raw)
        return dict(value) if isinstance(value, dict) else None
    except StructuredResponseError:
        return None


def _clean_json_value(value: Any, label: str) -> Any:
    """Validate opaque selector data as JSON without semantic size policy.

    Transport/body/process boundaries already protect physical resources.  A
    selector belongs to the provider that produced Material, so Core must not
    invent depth/item/string ceilings that the provider-facing schema cannot
    express or that Main did not choose.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_clean_json_value(v, label) for v in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} selector key invalid")
            out[key] = _clean_json_value(item, label)
        return out
    raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} selector value invalid")


def _support_items(raw: Any, label: str) -> list[Any]:
    """Normalize safe wire aliases to a list before canonical support parsing."""
    if raw is None:
        return []
    if isinstance(raw, (str, dict)):
        return [raw]
    if isinstance(raw, list):
        return raw
    raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} supports must be array, object or safe reference")


def _clean_support(raw: Any, label: str) -> Dict[str, Any]:
    """Normalize provider-wire support aliases to Eyle's canonical support.

    Canonical output is always one of:
      {kind:request}
      {kind:memory,memory_id:mem-*|@alias,revision?:N}
      {kind:relation,relation_id:rel-*,revision?:N}
      {kind:material,material_id:mat-*,selector?:{...}}

    Strings/singleton objects are accepted only as unambiguous boundary aliases;
    the Memory Graph never sees those aliases.
    """
    if isinstance(raw, str):
        value = raw.strip()
        if value in {"request", "current_request"}:
            return {"kind": "request"}
        if re.fullmatch(_MATERIAL_REF, value):
            return {"kind": "material", "material_id": value}
        if re.fullmatch(_MEMORY_REF, value):
            return {"kind": "memory", "memory_id": value}
        if re.fullmatch(_RELATION_REF, value):
            return {"kind": "relation", "relation_id": value}
        if value.startswith("material:") and re.fullmatch(_MATERIAL_REF, value.split(":", 1)[1]):
            return {"kind": "material", "material_id": value.split(":", 1)[1]}
        if value.startswith("memory:") and re.fullmatch(_MEMORY_REF, value.split(":", 1)[1]):
            return {"kind": "memory", "memory_id": value.split(":", 1)[1]}
        if value.startswith("relation:") and re.fullmatch(_RELATION_REF, value.split(":", 1)[1]):
            return {"kind": "relation", "relation_id": value.split(":", 1)[1]}
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} support reference invalid")
    if not isinstance(raw, dict):
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} support must be object or safe reference")

    data = dict(raw)
    kind = str(data.get("kind") or "").strip().lower()
    # Safe inference for common wire shorthands.  Ambiguous shapes still fail.
    if not kind:
        if "material_id" in data and set(data) <= {"material_id", "selector"}:
            kind = "material"
        elif "memory_id" in data and set(data) <= {"memory_id", "revision"}:
            kind = "memory"
        elif "relation_id" in data and set(data) <= {"relation_id", "revision"}:
            kind = "relation"
    if kind == "current_request":
        kind = "request"
    if kind == "material" and "material_id" not in data and "id" in data:
        data["material_id"] = data.pop("id")
    if kind == "memory" and "memory_id" not in data and "id" in data:
        data["memory_id"] = data.pop("id")
    if kind == "relation" and "relation_id" not in data and "id" in data:
        data["relation_id"] = data.pop("id")

    if kind == "request":
        if set(data) - {"kind"}:
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} request support shape invalid")
        return {"kind": "request"}
    if kind == "memory":
        memory_id = data.get("memory_id")
        revision = data.get("revision")
        if set(data) - {"kind", "memory_id", "revision"} or not isinstance(memory_id, str) or re.fullmatch(_MEMORY_REF, memory_id) is None:
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} memory support invalid")
        out = {"kind": "memory", "memory_id": memory_id}
        if revision is not None:
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} memory support revision invalid")
            out["revision"] = revision
        return out
    if kind == "relation":
        relation_id = data.get("relation_id")
        revision = data.get("revision")
        if set(data) - {"kind", "relation_id", "revision"} or not isinstance(relation_id, str) or re.fullmatch(_RELATION_REF, relation_id) is None:
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} relation support invalid")
        out = {"kind": "relation", "relation_id": relation_id}
        if revision is not None:
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} relation support revision invalid")
            out["revision"] = revision
        return out
    if kind != "material":
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} support kind invalid")
    if set(data) - {"kind", "material_id", "selector"}:
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} material support shape invalid")
    material_id = data.get("material_id")
    if not isinstance(material_id, str) or re.fullmatch(_MATERIAL_REF, material_id) is None:
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} material_id invalid")
    out = {"kind": "material", "material_id": material_id}
    if "selector" in data:
        if not isinstance(data.get("selector"), dict):
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} selector must be object")
        out["selector"] = _clean_json_value(data.get("selector"), f"{label}.selector")
    return out


def _ref(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(_MEMORY_REF, value) is None:
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} memory reference invalid")
    return value


def _strings(value: Any, label: str, item_max: int | None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} invalid")
    out = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip() or (item_max is not None and len(raw.strip()) > item_max):
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} item invalid")
        out.append(raw.strip())
    return out


def _revision(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.expected_revision invalid")
    return value



def _clean_epistemic(raw: Any, label: str, *, default_unclassified: bool = False) -> Dict[str, Any] | None:
    def _error(message: str):
        return StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.epistemic invalid: {message}")
    try:
        out = normalize_epistemic(
            raw,
            default_unclassified=default_unclassified,
            error_factory=_error,
        )
    except StructuredResponseError:
        raise
    if out is None:
        return None
    # Wire boundary also proves the nested values are canonical JSON-safe.
    out["temporal"] = _clean_json_value(out.get("temporal") or {}, f"{label}.epistemic.temporal")
    out["context"] = _clean_json_value(out.get("context") or {}, f"{label}.epistemic.context")
    return out

def _clean_associative_recall(raw: Any, label: str) -> Dict[str, list[str]] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"aliases", "concepts", "cues"}:
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.recall invalid")
    out: Dict[str, list[str]] = {}
    for key in ("aliases", "concepts", "cues"):
        values = raw.get(key)
        if values is None:
            continue
        if not isinstance(values, list):
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.recall.{key} invalid")
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.recall.{key} invalid")
            text = value.strip()
            folded = text.casefold()
            if folded in seen:
                continue
            seen.add(folded); cleaned.append(text)
        if cleaned:
            out[key] = cleaned
    return out


def _clean_memory(raw: Any) -> list[Dict[str, Any]]:
    if not isinstance(raw, list):
        raise StructuredResponseError("EYLE_MEMORY_REQUIRED", "memory_delta must be an array")
    clean: list[Dict[str, Any]] = []
    for index, action in enumerate(raw, 1):
        label = f"memory_delta[{index}]"
        if not isinstance(action, dict) or not isinstance(action.get("op"), str):
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} must contain an op")
        if "arguments" in action:
            if set(action) != {"op", "arguments"} or not isinstance(action.get("arguments"), dict):
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} must contain exactly op and arguments")
            op = action.get("op"); args = dict(action["arguments"])
        else:
            # Safe Rev2.8.4 wire alias: models in prompt/json-object mode may
            # flatten the arguments shown in prose. Normalize before validation.
            op = action.get("op")
            args = {key: value for key, value in action.items() if key != "op"}
        supports = [_clean_support(v, label) for v in _support_items(args.get("supports"), label)]
        if op == "remember":
            allowed = {"key", "scope", "retention", "kind", "content", "epistemic", "recall", "tags", "supports"}
            if set(args) - allowed or args.get("scope") not in {"world", "user"} or args.get("retention") not in {"temporary", "persistent"}:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} remember arguments invalid")
            kind, content = args.get("kind"), args.get("content")
            if not isinstance(kind, str) or not kind.strip() or len(kind.strip()) > 96 or not isinstance(content, str) or not content.strip():
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} remember content invalid")
            out = {"op": "remember", "scope": args["scope"], "retention": args["retention"], "kind": kind.strip(), "content": content.strip()}
            epistemic = _clean_epistemic(args.get("epistemic"), label)
            if epistemic is not None:
                out["epistemic"] = epistemic
            recall = _clean_associative_recall(args.get("recall"), label)
            if recall:
                out["recall"] = recall
            key = args.get("key")
            if key is not None:
                if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_-]+", key) is None or len(key) > 64:
                    raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.key invalid")
                out["key"] = key
            tags = _strings(args.get("tags"), f"{label}.tags", None)
            if tags: out["tags"] = tags
            if supports: out["supports"] = supports
            clean.append(out); continue
        if op == "revise":
            allowed = {"id", "expected_revision", "retention", "kind", "content", "epistemic", "recall", "add_recall", "remove_recall", "add_tags", "remove_tags", "supports"}
            if set(args) - allowed:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} revise arguments invalid")
            out = {"op": "revise", "id": _ref(args.get("id"), label), "expected_revision": _revision(args.get("expected_revision"), label)}
            if args.get("retention") is not None:
                if args["retention"] not in {"temporary", "persistent"}:
                    raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.retention invalid")
                out["retention"] = args["retention"]
            if args.get("kind") is not None:
                if not isinstance(args["kind"], str) or not args["kind"].strip() or len(args["kind"].strip()) > 96:
                    raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.kind invalid")
                out["kind"] = args["kind"].strip()
            if args.get("content") is not None:
                if not isinstance(args["content"], str) or not args["content"].strip():
                    raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.content invalid")
                out["content"] = args["content"].strip()
            epistemic = _clean_epistemic(args.get("epistemic"), label)
            if epistemic is not None:
                out["epistemic"] = epistemic
            recall = _clean_associative_recall(args.get("recall"), label)
            add_recall = _clean_associative_recall(args.get("add_recall"), label)
            remove_recall = _clean_associative_recall(args.get("remove_recall"), label)
            if recall is not None:
                out["recall"] = recall
            if add_recall:
                out["add_recall"] = add_recall
            if remove_recall:
                out["remove_recall"] = remove_recall
            add_tags = _strings(args.get("add_tags"), f"{label}.add_tags", None)
            remove_tags = _strings(args.get("remove_tags"), f"{label}.remove_tags", None)
            if add_tags: out["add_tags"] = add_tags
            if remove_tags: out["remove_tags"] = remove_tags
            if supports: out["supports"] = supports
            clean.append(out); continue
        if op == "relate":
            allowed = {"source", "relation", "target", "epistemic", "supports"}
            relation = args.get("relation")
            if set(args) - allowed or not isinstance(relation, str) or not relation.strip() or len(relation.strip()) > 120:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} relate arguments invalid")
            out = {"op": "relate", "source": _ref(args.get("source"), label), "relation": relation.strip(), "target": _ref(args.get("target"), label)}
            epistemic = _clean_epistemic(args.get("epistemic"), label)
            if epistemic is not None:
                out["epistemic"] = epistemic
            if supports: out["supports"] = supports
            clean.append(out); continue
        if op == "revise_relation":
            allowed = {"id", "expected_revision", "relation", "epistemic", "supports"}
            if set(args) - allowed:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} revise_relation arguments invalid")
            identity = args.get("id")
            if not isinstance(identity, str) or re.fullmatch(r"rel-[A-Za-z0-9._-]+", identity) is None:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.id invalid")
            out = {"op": "revise_relation", "id": identity, "expected_revision": _revision(args.get("expected_revision"), label)}
            relation = args.get("relation")
            if relation is not None:
                if not isinstance(relation, str) or not relation.strip() or len(relation.strip()) > 120:
                    raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.relation invalid")
                out["relation"] = relation.strip()
            epistemic = _clean_epistemic(args.get("epistemic"), label)
            if epistemic is not None:
                out["epistemic"] = epistemic
            if supports: out["supports"] = supports
            clean.append(out); continue
        if op == "task_status":
            allowed = {"id", "expected_state_revision", "state"}
            if set(args) - allowed:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} task_status arguments invalid")
            state = str(args.get("state") or "").strip().lower()
            if state not in {"active", "blocked", "resolved", "cancelled"}:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.state invalid")
            expected = args.get("expected_state_revision")
            if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.expected_state_revision invalid")
            clean.append({"op": "task_status", "id": _ref(args.get("id"), label), "expected_state_revision": expected, "state": state})
            continue
        if op == "archive":
            if set(args) != {"id", "expected_revision"}:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} archive arguments invalid")
            clean.append({"op": "archive", "id": _ref(args.get("id"), label), "expected_revision": _revision(args.get("expected_revision"), label)}); continue
        if op == "supersede":
            if set(args) != {"id", "expected_revision", "replacement"}:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} supersede arguments invalid")
            clean.append({"op": "supersede", "id": _ref(args.get("id"), label), "expected_revision": _revision(args.get("expected_revision"), label), "replacement": _ref(args.get("replacement"), label)}); continue
        if op == "retire_relation":
            if set(args) != {"id", "expected_revision"}:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} retire_relation arguments invalid")
            identity = args.get("id")
            if not isinstance(identity, str) or re.fullmatch(r"rel-[A-Za-z0-9._-]+", identity) is None:
                raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.id invalid")
            clean.append({"op": "retire_relation", "id": identity, "expected_revision": _revision(args.get("expected_revision"), label)}); continue
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.op invalid")
    return clean


def _clean_operation(raw: Any, label: str) -> Dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"operation", "arguments"}:
        raise StructuredResponseError("ECC_OPERATION_INVALID", f"{label} must contain exactly operation and arguments")
    operation, arguments = raw.get("operation"), raw.get("arguments")
    if not isinstance(operation, str) or not operation.strip() or not isinstance(arguments, dict):
        raise StructuredResponseError("ECC_OPERATION_INVALID", f"{label} requires operation and object arguments")
    operation = operation.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", operation) is None:
        raise StructuredResponseError("ECC_OPERATION_INVALID", f"{label}.operation invalid")
    return {"operation": operation, "arguments": dict(arguments)}


def _parse_ecc_decision(decision: Any) -> Dict[str, Any]:
    """Validate only the semantic ECC decision.

    Rev3.7 invariant: Memory is a sidecar. A malformed memory delta must never
    prevent a valid ECC decision from reaching Runtime.
    """
    if not isinstance(decision, dict):
        raise StructuredResponseError("ECC_DECISION_INVALID", "decision must be an object")
    kind = decision.get("type")
    normalized: Dict[str, Any] = {"type": kind}
    if kind == "explorar":
        if set(decision) != {"type", "operations"}:
            raise StructuredResponseError("ECC_SHAPE_INVALID", "explorar requires exactly type and operations")
        operations = decision.get("operations")
        if not isinstance(operations, list) or len(operations) < 1:
            raise StructuredResponseError("ECC_OPERATION_INVALID", "explorar.operations must contain at least one operation")
        normalized["operations"] = [_clean_operation(item, f"decision.operations[{i}]") for i, item in enumerate(operations)]
        return normalized
    if kind == "construir":
        if set(decision) != {"type", "operation", "arguments"}:
            raise StructuredResponseError("ECC_SHAPE_INVALID", "construir requires exactly type, operation and arguments")
        op = _clean_operation({"operation": decision.get("operation"), "arguments": decision.get("arguments")}, "decision")
        normalized.update(op)
        return normalized
    if kind == "concluir":
        allowed = {"type", "response", "choices", "allow_free_text"}
        if set(decision) - allowed:
            raise StructuredResponseError("ECC_SHAPE_INVALID", "concluir contains unsupported fields")
        response = decision.get("response")
        if not isinstance(response, str) or not response.strip():
            raise StructuredResponseError("ECC_RESPONSE_INVALID", "concluir requires a non-empty response")
        normalized["response"] = response.strip()
        if "choices" in decision:
            raw_choices = decision.get("choices")
            if not isinstance(raw_choices, list):
                raise StructuredResponseError("ECC_CHOICE_INVALID", "concluir.choices must be an array")
            choices = []
            seen = set()
            for value in raw_choices:
                if not isinstance(value, str) or not value.strip():
                    raise StructuredResponseError("ECC_CHOICE_INVALID", "concluir.choices must contain non-empty strings")
                label = value.strip()
                folded = label.casefold()
                if folded not in seen:
                    seen.add(folded); choices.append(label)
            if len(choices) < 2:
                raise StructuredResponseError("ECC_CHOICE_INVALID", "concluir.choices requires at least two distinct options")
            normalized["choices"] = choices
            normalized["allow_free_text"] = bool(decision.get("allow_free_text", True))
        elif "allow_free_text" in decision:
            raise StructuredResponseError("ECC_CHOICE_INVALID", "allow_free_text requires choices")
        return normalized
    raise StructuredResponseError("ECC_TYPE_INVALID", "decision.type must be explorar, construir or concluir")


def parse_ecc_response(raw: Any) -> Dict[str, Any]:
    # Current flat wire -> canonical envelope. ECC is validated first;
    # Memory is then parsed independently as a non-vetoing Eyle sidecar.
    envelope = canonicalize_wire_response(raw)
    if set(envelope) != {"decision", "memory_delta"}:
        raise StructuredResponseError("EYLE_ENVELOPE_INVALID", "canonical top-level must contain exactly decision and memory_delta")

    normalized = _parse_ecc_decision(envelope.get("decision"))
    try:
        normalized["memory_delta"] = _clean_memory(envelope.get("memory_delta"))
    except StructuredResponseError as error:
        if not str(error.code).startswith("EYLE_MEMORY_"):
            raise
        normalized["memory_delta"] = []
        normalized["memory_error"] = {"code": error.code, "detail": error.detail}
    return normalized


def parse_profile_response(raw: Any, profile: str) -> Dict[str, Any]:
    if profile != "ecc":
        raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
    return parse_ecc_response(raw)
