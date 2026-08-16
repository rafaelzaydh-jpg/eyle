"""Wire + canonical structured cognition boundary for Eyle.

The provider-facing wire is intentionally tolerant: Main does the semantic work
and may emit a simple flat JSON object. Eyle deterministically canonicalizes safe
aliases into its strict internal {decision,memory_delta} contract and validates
semantics locally. The Adapter owns only provider transport and JSON recovery; it
never owns ECC or Memory meaning.
"""
from __future__ import annotations

import ast
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
    ]
}
_SAFE_SUPPORT_STRING = {
    "type": "string",
    "pattern": r"^(?:request|current_request|mat-[0-9]+|mem-[A-Za-z0-9._-]+|@[A-Za-z0-9_-]+|material:mat-[0-9]+|memory:(?:mem-[A-Za-z0-9._-]+|@[A-Za-z0-9_-]+))$",
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
            },
            "required": ["memory_id"],
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

_EPISTEMIC_SCHEMA = {
    "type": "object",
    "properties": {
        # Main authors these semantic labels. Runtime intentionally does not
        # freeze a closed ontology: a growing mind may need categories that
        # this release did not anticipate.
        "nature": {"type": "string", "minLength": 1},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "volatility": {"type": "string", "minLength": 1},
        "temporal": {"type": "object"},
        "context": {"type": "object"},
    },
    "required": ["nature"],
    "additionalProperties": False,
}

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
        "operation": {"type": "string", "minLength": 1, "pattern": r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?$"},
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

# Eyle separates the provider-facing wire shape from the canonical ECC
# contract.  The wire schema deliberately asks the model/provider for only the
# basic physical property the Adapter can help with: return a JSON object.  Eyle
# itself owns aliases, canonicalization and semantic validation.
_EYLE_WIRE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "object"},
        "type": {"type": "string"},
        "response": {},
        "answer": {},
        "operation": {},
        "operations": {},
        "arguments": {},
        "memory_delta": {},
        "memory": {},
        "memories": {},
    },
    "additionalProperties": True,
}


def schema_for_profile(profile: str) -> Dict[str, Any]:
    """Return Eyle's strict *internal* canonical schema."""
    if profile != "ecc":
        raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
    return deepcopy(_EYLE_RESPONSE_SCHEMA)


def wire_schema_for_profile(profile: str) -> Dict[str, Any]:
    """Return the intentionally tolerant provider-facing wire schema."""
    if profile != "ecc":
        raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
    return deepcopy(_EYLE_WIRE_SCHEMA)


def json_schema_response_format(profile: str) -> Dict[str, Any]:
    # Provider native-schema modes are an aid, never the semantic authority.
    # strict=False is intentional: safe aliases are canonicalized inside Eyle.
    schema = wire_schema_for_profile(profile)
    return {"type": "json_schema", "json_schema": {"name": "eyle_cognition_wire", "strict": False, "schema": schema}}


def mandatory_top_level_keys(profile: str) -> tuple[str, ...]:
    schema_for_profile(profile)
    return ("decision", "memory_delta")


def contract_instruction(profile: str) -> str:
    wire_schema_for_profile(profile)
    return (
        'Return one JSON object only. Do the basic semantic work; Eyle will canonicalize safe wire aliases. '
        'Preferred wire form is flat: {"type":"explorar|construir|concluir",...,"memory_delta":[]}. '
        'explorar uses operations:[{operation,arguments}]; construir uses operation+arguments; concluir uses response. '
        'A nested {"decision":{...},"memory_delta":[]} envelope is also accepted. '
        'memory_delta is transversal to ECC and has no semantic item-count ceiling. Memory items may be flat '
        '{op:"remember",scope:"user|world",retention:"temporary|persistent",kind,content,...}; Eyle wraps arguments. '
        'Safe supports may be "request", "mat-0001", "mem-...", or their canonical objects. '
        'Do not spend cognition trying to serialize Eyle internals perfectly; preserve meaning and emit simple JSON. '
        'Memory may include epistemic:{nature,confidence?,volatility?,temporal?,context?}; nature and volatility are open Main-authored labels, never a closed Runtime ontology. '
        'Keep epistemic layers distinct: an observed event, an inference about it, a preference, a hypothesis, a belief, and a decision may coexist instead of collapsing into one timeless fact. Confidence describes your current support for an interpretation/applicability, not metaphysical truth or a mandatory arithmetic score. '
        'When related active memory exists, consolidate incrementally when useful: revise the same continuing proposition/relation when its support changes; create and relate a new temporal/contextual state when reality or a volatile person/preferences changed; derive reusable patterns/hypotheses from supporting memories; preserve prior history rather than rewriting it. '
        'Do not treat persistence as certainty. Prefer atomic reusable knowledge over transcript/document blobs, and let contradictory/context-specific memories coexist when that better represents what was learned.'
    )


_WHOLE_FENCE_RE = re.compile(r"^\s*```(?:json|javascript|python)?\s*(.*?)\s*```\s*$", re.I | re.S)


def _balanced_json_fragment(text: str) -> str | None:
    """Extract the first balanced object/array without interpreting semantics."""
    start = None
    opener = ""
    for index, char in enumerate(text):
        if char in "{[":
            start = index
            opener = char
            break
    if start is None:
        return None
    stack = [opener]
    quote = None
    escaped = False
    pairs = {"}": "{", "]": "["}
    for index in range(start + 1, len(text)):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char in "{[":
            stack.append(char)
            continue
        if char in "}]":
            if not stack or stack[-1] != pairs[char]:
                continue
            stack.pop()
            if not stack:
                return text[start:index + 1]
    return None


def _decode_jsonish(candidate: str) -> Any:
    last_error: Exception | None = None
    for decoder in (json.loads, ast.literal_eval):
        try:
            value = decoder(candidate)
            # Models/providers occasionally JSON-encode the JSON string itself.
            if isinstance(value, str):
                nested = value.strip()
                if nested.startswith(("{", "[")):
                    try:
                        return json.loads(nested)
                    except Exception:
                        try:
                            return ast.literal_eval(nested)
                        except Exception:
                            pass
            return value
        except Exception as exc:  # deterministic fall-through only
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("empty JSON decoder set")


def parse_json_representation(raw: Any) -> Any:
    """Recover a JSON/Python-literal representation without inventing meaning."""
    if isinstance(raw, (dict, list)):
        return deepcopy(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise StructuredResponseError("STRUCTURED_EMPTY", "structured response is empty")
    text = raw.strip()
    candidates: list[str] = []
    fence = _WHOLE_FENCE_RE.match(text)
    if fence:
        candidates.append(fence.group(1).strip())
    candidates.append(text)
    fragment = _balanced_json_fragment(text)
    if fragment and fragment not in candidates:
        candidates.append(fragment)
    last: Exception | None = None
    for candidate in candidates:
        try:
            return _decode_jsonish(candidate)
        except Exception as exc:
            last = exc
    detail = type(last).__name__ if last is not None else "unknown"
    raise StructuredResponseError("STRUCTURED_JSON_INVALID", f"response does not contain a recoverable JSON object: {detail}") from last


def _movement(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "explore": "explorar", "exploration": "explorar", "observe": "explorar", "explorar": "explorar",
        "build": "construir", "construct": "construir", "write": "construir", "construir": "construir",
        "final": "concluir", "finish": "concluir", "conclude": "concluir", "answer": "concluir", "concluir": "concluir",
    }.get(token, str(value or "").strip())


def _memory_op(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "remember": "remember", "store": "remember", "memorize": "remember",
        "revise": "revise", "update": "revise",
        "relate": "relate", "link": "relate",
        "revise_relation": "revise_relation", "update_relation": "revise_relation", "revise_edge": "revise_relation", "update_edge": "revise_relation",
        "archive": "archive",
        "supersede": "supersede", "replace": "supersede",
        "retire_relation": "retire_relation", "retire_edge": "retire_relation",
    }.get(token, str(value or "").strip())


def _wire_operation(raw: Any) -> Any:
    if isinstance(raw, str) and raw.strip():
        return {"operation": raw.strip(), "arguments": {}}
    if not isinstance(raw, dict):
        return raw
    operation = raw.get("operation")
    if not isinstance(operation, str) or not operation.strip():
        for alias in ("name", "tool"):
            if isinstance(raw.get(alias), str) and raw.get(alias).strip():
                operation = raw.get(alias).strip()
                break
    arguments = raw.get("arguments")
    if not isinstance(arguments, dict):
        for alias in ("args", "input"):
            if isinstance(raw.get(alias), dict):
                arguments = raw.get(alias)
                break
    if arguments is None:
        arguments = {}
    return {"operation": operation, "arguments": arguments}


def _wire_memory_item(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    item = deepcopy(raw)
    op = item.get("op")
    if not isinstance(op, str) or not op.strip():
        op = item.get("action")
    op = _memory_op(op)
    if isinstance(item.get("arguments"), dict):
        args = deepcopy(item["arguments"])
    else:
        args = {k: deepcopy(v) for k, v in item.items() if k not in {"op", "action", "arguments"}}
    if "supports" not in args and "support" in args:
        args["supports"] = args.pop("support")
    retention = args.get("retention")
    token = str(retention or "").strip().lower()
    if token in {"temp", "temporary", "transient", "short", "short_term"}:
        args["retention"] = "temporary"
    elif token in {"permanent", "durable", "persistent", "long", "long_term"}:
        args["retention"] = "persistent"
    # Flat epistemic fields are a serialization alias only; no value is inferred.
    if "epistemic" not in args:
        epi = {k: args.pop(k) for k in ("nature", "confidence", "volatility", "temporal", "context") if k in args}
        if epi:
            args["epistemic"] = epi
    # Associative-recall aliases remain Main-authored semantics. Eyle
    # merely nests the strings into the canonical recall object.
    if "recall" not in args:
        recall = {}
        for canonical, aliases in {
            "aliases": ("recall_aliases", "aliases"),
            "concepts": ("recall_concepts", "concepts"),
            "cues": ("recall_cues", "cues"),
        }.items():
            for alias in aliases:
                if alias in args:
                    recall[canonical] = args.pop(alias)
                    break
        if recall:
            args["recall"] = recall
    return {"op": op, "arguments": args}


def _canonicalize_wire(raw: Any) -> tuple[Dict[str, Any], list[str]]:
    value = parse_json_representation(raw)
    steps: list[str] = []
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        value = value[0]
        steps.append("unwrap_single_array")
    if not isinstance(value, dict):
        raise StructuredResponseError("STRUCTURED_OBJECT_REQUIRED", "top-level wire value must be an object")
    value = deepcopy(value)
    for wrapper in ("output", "result", "ecc"):
        if "decision" not in value and "type" not in value and isinstance(value.get(wrapper), dict):
            value = deepcopy(value[wrapper])
            steps.append(f"unwrap_{wrapper}")
            break

    memory_source = None
    for key in ("memory_delta", "memory", "memories"):
        if key in value:
            memory_source = value.get(key)
            if key != "memory_delta":
                steps.append(f"alias_{key}_to_memory_delta")
            break
    if memory_source is None:
        memory_source = []
        steps.append("default_memory_delta")
    elif isinstance(memory_source, dict):
        memory_source = [memory_source]
        steps.append("wrap_single_memory")

    decision = value.get("decision")
    if isinstance(decision, str) and decision.strip() and "type" not in value:
        decision = {"type": decision.strip()}
        for key in ("response", "answer", "operation", "operations", "arguments"):
            if key in value:
                decision[key] = deepcopy(value[key])
        steps.append("decision_string_to_object")
    if not isinstance(decision, dict):
        if any(k in value for k in ("type", "operation", "operations", "response", "answer", "final", "text")):
            decision = {k: deepcopy(v) for k, v in value.items() if k in {
                "type", "operation", "operations", "arguments", "response", "answer", "final", "text", "on_success"
            }}
            steps.append("wrap_flat_decision")
        else:
            decision = value.get("decision")
    if not isinstance(decision, dict):
        return {"decision": decision, "memory_delta": memory_source}, steps

    kind = _movement(decision.get("type") or decision.get("kind"))
    out_decision: Dict[str, Any] = {"type": kind}
    if kind == "explorar":
        operations = decision.get("operations")
        if isinstance(operations, dict):
            operations = [operations]
            steps.append("wrap_single_operation")
        if not isinstance(operations, list) and decision.get("operation") is not None:
            operations = [{"operation": decision.get("operation"), "arguments": decision.get("arguments") or {}}]
            steps.append("single_explore_to_batch")
        if isinstance(operations, list):
            operations = [_wire_operation(item) for item in operations]
        out_decision["operations"] = operations
    elif kind == "construir":
        op = _wire_operation({
            "operation": decision.get("operation") or decision.get("name") or decision.get("tool"),
            "arguments": decision.get("arguments") if isinstance(decision.get("arguments"), dict) else decision.get("args") if isinstance(decision.get("args"), dict) else {},
        })
        if isinstance(op, dict):
            out_decision.update(op)
        # on_success is a retired wire field. Ignoring it cannot change the
        # physical operation chosen by Main and avoids reviving old semantics.
        if "on_success" in decision:
            steps.append("drop_retired_on_success")
    elif kind == "concluir":
        response = decision.get("response")
        if response is None:
            for alias in ("answer", "final", "text"):
                if alias in decision:
                    response = decision.get(alias)
                    steps.append(f"alias_{alias}_to_response")
                    break
        out_decision["response"] = response
    else:
        # Preserve unknown decision data so strict canonical validation can give
        # Main a precise error rather than the canonicalizer guessing semantics.
        out_decision.update({k: deepcopy(v) for k, v in decision.items() if k not in {"type", "kind"}})

    normalized_memory = memory_source
    if isinstance(memory_source, list):
        normalized_memory = [_wire_memory_item(item) for item in memory_source]
        if normalized_memory != memory_source:
            steps.append("normalize_memory_wire")
    return {"decision": out_decision, "memory_delta": normalized_memory}, steps


def canonicalize_wire_response(raw: Any) -> Dict[str, Any]:
    """Deterministically turn tolerant wire JSON into the canonical envelope."""
    envelope, _ = _canonicalize_wire(raw)
    return envelope


def wire_canonicalization_steps(raw: Any) -> list[str]:
    """Diagnostic-only list of deterministic boundary normalizations."""
    _, steps = _canonicalize_wire(raw)
    return steps


def _object(raw: Any) -> Dict[str, Any]:
    # Backward-compatible helper now means "recover a wire object", not strict
    # canonical parsing.
    value = parse_json_representation(raw)
    if not isinstance(value, dict):
        raise StructuredResponseError("STRUCTURED_OBJECT_REQUIRED", "top-level JSON must be an object")
    return value


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
      {kind:memory,memory_id:mem-*|@alias}
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
        if value.startswith("material:") and re.fullmatch(_MATERIAL_REF, value.split(":", 1)[1]):
            return {"kind": "material", "material_id": value.split(":", 1)[1]}
        if value.startswith("memory:") and re.fullmatch(_MEMORY_REF, value.split(":", 1)[1]):
            return {"kind": "memory", "memory_id": value.split(":", 1)[1]}
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} support reference invalid")
    if not isinstance(raw, dict):
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} support must be object or safe reference")

    data = dict(raw)
    kind = str(data.get("kind") or "").strip().lower()
    # Safe inference for common wire shorthands.  Ambiguous shapes still fail.
    if not kind:
        if "material_id" in data and set(data) <= {"material_id", "selector"}:
            kind = "material"
        elif "memory_id" in data and set(data) == {"memory_id"}:
            kind = "memory"
    if kind == "current_request":
        kind = "request"
    if kind == "material" and "material_id" not in data and "id" in data:
        data["material_id"] = data.pop("id")
    if kind == "memory" and "memory_id" not in data and "id" in data:
        data["memory_id"] = data.pop("id")

    if kind == "request":
        if set(data) - {"kind"}:
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} request support shape invalid")
        return {"kind": "request"}
    if kind == "memory":
        memory_id = data.get("memory_id")
        if set(data) - {"kind", "memory_id"} or not isinstance(memory_id, str) or re.fullmatch(_MEMORY_REF, memory_id) is None:
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} memory support invalid")
        return {"kind": "memory", "memory_id": memory_id}
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


def _strings(value: Any, label: str, maximum: int | None, item_max: int | None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label} invalid")
    # ``maximum`` is retained for source compatibility but intentionally not
    # used as a semantic count ceiling in Rev2.8.4.
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
    if raw is None:
        if not default_unclassified:
            return None
        return {"nature": "unclassified", "volatility": "unknown", "temporal": {}, "context": {}}
    if not isinstance(raw, dict):
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.epistemic must be object")
    allowed = {"nature", "confidence", "volatility", "temporal", "context"}
    if set(raw) - allowed:
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.epistemic fields invalid")
    nature = raw.get("nature")
    if not isinstance(nature, str) or not nature.strip() or len(nature.strip()) > 96:
        raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.epistemic.nature invalid")
    out: Dict[str, Any] = {"nature": nature.strip()}
    confidence = raw.get("confidence")
    if confidence is not None:
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0.0 <= float(confidence) <= 1.0:
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.epistemic.confidence invalid")
        out["confidence"] = float(confidence)
    volatility = raw.get("volatility")
    if volatility is not None:
        if not isinstance(volatility, str) or not volatility.strip() or len(volatility.strip()) > 96:
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.epistemic.volatility invalid")
        out["volatility"] = volatility.strip()
    else:
        out["volatility"] = "unknown"
    temporal = raw.get("temporal")
    if temporal is not None:
        if not isinstance(temporal, dict):
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.epistemic.temporal invalid")
        out["temporal"] = _clean_json_value(temporal, f"{label}.epistemic.temporal")
    else:
        out["temporal"] = {}
    context = raw.get("context")
    if context is not None:
        if not isinstance(context, dict):
            raise StructuredResponseError("EYLE_MEMORY_INVALID", f"{label}.epistemic.context invalid")
        out["context"] = _clean_json_value(context, f"{label}.epistemic.context")
    else:
        out["context"] = {}
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
            tags = _strings(args.get("tags"), f"{label}.tags", None, None)
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
            add_tags = _strings(args.get("add_tags"), f"{label}.add_tags", None, None)
            remove_tags = _strings(args.get("remove_tags"), f"{label}.remove_tags", None, None)
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
    for prefix in ("explorar.", "construir."):
        if operation.startswith(prefix):
            operation = operation[len(prefix):]
            break
    if re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?", operation) is None:
        raise StructuredResponseError("ECC_OPERATION_INVALID", f"{label}.operation invalid")
    return {"operation": operation, "arguments": dict(arguments)}


def parse_ecc_response(raw: Any) -> Dict[str, Any]:
    # Tolerant wire -> deterministic canonical envelope -> strict
    # semantic validator. Only the final canonical result can reach Runtime.
    envelope = canonicalize_wire_response(raw)
    if set(envelope) != {"decision", "memory_delta"}:
        raise StructuredResponseError("EYLE_ENVELOPE_INVALID", "canonical top-level must contain exactly decision and memory_delta")
    decision = envelope.get("decision")
    if not isinstance(decision, dict):
        raise StructuredResponseError("ECC_DECISION_INVALID", "decision must be an object")
    kind = decision.get("type")
    normalized: Dict[str, Any] = {"type": kind, "memory_delta": _clean_memory(envelope.get("memory_delta"))}
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
        if set(decision) != {"type", "response"}:
            raise StructuredResponseError("ECC_SHAPE_INVALID", "concluir requires exactly type and response")
        response = decision.get("response")
        if not isinstance(response, str) or not response.strip():
            raise StructuredResponseError("ECC_RESPONSE_INVALID", "concluir requires a non-empty response")
        normalized["response"] = response.strip()
        return normalized
    raise StructuredResponseError("ECC_TYPE_INVALID", "decision.type must be explorar, construir or concluir")


def parse_profile_response(raw: Any, profile: str) -> Dict[str, Any]:
    if profile != "ecc":
        raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
    return parse_ecc_response(raw)
