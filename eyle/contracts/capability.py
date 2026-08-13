"""Universal capability contracts.

This module contains only mechanics shared by every capability provider.  It
knows nothing about workspaces, source code, networks, robots, pets, files or
other domains.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict

from .observation import normalize_effect

RESULT_FIELDS = (
    "status", "ok", "executed", "changed", "error_code", "detail", "retryable",
    "failure_scope", "failure_resource", "physical_effect", "observations",
    "coverage", "frontiers",
)

PHYSICAL_EFFECT_PERSISTENCE = {"call", "job", "persistent"}


def physical_effect(resource: str, operation: str, persistence: str, *, changed: bool = False) -> Dict[str, Any]:
    """Create the domain-neutral physical effect carried by capability results.

    The presence of this record means an executed capability produced a real
    effect. ``changed`` distinguishes state mutation from executions whose
    physical fact is the execution itself.
    """
    resource = str(resource or "").strip()
    operation = str(operation or "").strip()
    persistence = str(persistence or "").strip()
    if not resource:
        raise ValueError("PHYSICAL_EFFECT_RESOURCE_REQUIRED")
    if not operation:
        raise ValueError("PHYSICAL_EFFECT_OPERATION_REQUIRED")
    if persistence not in PHYSICAL_EFFECT_PERSISTENCE:
        raise ValueError("PHYSICAL_EFFECT_PERSISTENCE_INVALID")
    return {
        "resource": resource,
        "operation": operation,
        "persistence": persistence,
        "changed": bool(changed),
    }


def normalize_physical_effect(value: Any) -> Dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("PHYSICAL_EFFECT_INVALID")
    expected = {"resource", "operation", "persistence", "changed"}
    if set(value) != expected:
        raise ValueError("PHYSICAL_EFFECT_INVALID")
    return physical_effect(
        value.get("resource"), value.get("operation"), value.get("persistence"),
        changed=value.get("changed", False),
    )


def result(status: str, ok: bool, executed: bool, *, changed: bool = False,
           error_code: str | None = None, detail: Any = None, retryable: bool | None = None,
           failure_scope: str | None = None, failure_resource: str | None = None,
           observations: Any = None, coverage: Any = None, frontiers: Any = None,
           physical_effect_value: Any = None) -> Dict[str, Any]:
    return {
        "status": str(status),
        "ok": bool(ok),
        "executed": bool(executed),
        "changed": bool(changed),
        "error_code": error_code,
        "detail": detail,
        "retryable": None if retryable is None else bool(retryable),
        "failure_scope": str(failure_scope) if failure_scope else None,
        "failure_resource": str(failure_resource) if failure_resource else None,
        "physical_effect": normalize_physical_effect(physical_effect_value),
        "observations": [copy.deepcopy(v) for v in (observations or []) if isinstance(v, dict)],
        "coverage": copy.deepcopy(coverage) if isinstance(coverage, dict) else {},
        "frontiers": [copy.deepcopy(v) for v in (frontiers or []) if isinstance(v, dict)],
    }


def failure(code: str, detail: Any, *, executed: bool = False, changed: bool = False,
            retryable: bool | None = None, failure_scope: str | None = None,
            failure_resource: str | None = None) -> Dict[str, Any]:
    return result(
        "failed", False, executed, changed=changed, error_code=str(code), detail=detail,
        retryable=retryable, failure_scope=failure_scope, failure_resource=failure_resource,
    )


def _json_type_valid(value: Any, kind: str) -> bool:
    if kind == "integer": return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number": return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "string": return isinstance(value, str)
    if kind == "boolean": return isinstance(value, bool)
    if kind == "object": return isinstance(value, dict)
    if kind == "array": return isinstance(value, list)
    if kind == "null": return value is None
    return True


def validate_schema_value(value: Any, rule: Dict[str, Any], path: str) -> str | None:
    """Validate the small JSON-Schema subset accepted by capability inputs."""
    rule = rule if isinstance(rule, dict) else {}
    kind = rule.get("type")
    if kind and not _json_type_valid(value, str(kind)):
        return f"argument '{path}' must be {kind}"
    if "enum" in rule and value not in list(rule.get("enum") or []):
        return f"argument '{path}' must be one of: " + ", ".join(str(v) for v in rule.get("enum") or [])
    if kind == "string":
        if len(value.strip()) < int(rule.get("minLength", 0) or 0):
            return f"argument '{path}' cannot be empty"
        if "maxLength" in rule and len(value) > int(rule["maxLength"]):
            return f"argument '{path}' exceeds maxLength={rule['maxLength']}"
        if rule.get("pattern") and re.fullmatch(str(rule["pattern"]), value) is None:
            return f"argument '{path}' does not match the required format"
    if kind in {"integer", "number"}:
        if "minimum" in rule and value < rule["minimum"]:
            return f"argument '{path}' must be >= {rule['minimum']}"
        if "maximum" in rule and value > rule["maximum"]:
            return f"argument '{path}' must be <= {rule['maximum']}"
    if kind == "object":
        props = rule.get("properties") if isinstance(rule.get("properties"), dict) else {}
        if rule.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(props))
            if unknown:
                return f"argument '{path}' has unknown fields: " + ", ".join(unknown)
        missing = [name for name in rule.get("required", []) if name not in value]
        if missing:
            return f"argument '{path}' requires fields: " + ", ".join(missing)
        for name, child in value.items():
            if isinstance(props.get(name), dict):
                err = validate_schema_value(child, props[name], f"{path}.{name}")
                if err: return err
    if kind == "array":
        if "minItems" in rule and len(value) < int(rule["minItems"]):
            return f"argument '{path}' needs at least {rule['minItems']} item(s)"
        if "maxItems" in rule and len(value) > int(rule["maxItems"]):
            return f"argument '{path}' allows at most {rule['maxItems']} item(s)"
        item_rule = rule.get("items")
        if isinstance(item_rule, dict):
            for index, child in enumerate(value):
                err = validate_schema_value(child, item_rule, f"{path}[{index}]")
                if err: return err
    return None


def compact_input_contract(schema: Any) -> Dict[str, str]:
    schema = schema if isinstance(schema, dict) else {"type": "object", "properties": {}, "required": []}
    required = set(schema.get("required") or [])
    labels = {"string": "str", "integer": "int", "number": "num", "boolean": "bool", "object": "obj", "array": "list"}
    inputs: Dict[str, str] = {}
    for name, spec in (schema.get("properties") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        enum = [str(v) for v in spec.get("enum") or []]
        if enum and len(enum) <= 8:
            head = "|".join(enum)
            if name not in required: head += "?"
        else:
            head = labels.get(spec.get("type"), str(spec.get("type") or "any"))
            if name not in required: head += "?"
            bounds = []
            if spec.get("minimum") is not None: bounds.append(f">={spec['minimum']}")
            if spec.get("maximum") is not None: bounds.append(f"<={spec['maximum']}")
            if bounds: head += " " + " ".join(bounds)
        desc = str(spec.get("description") or "").strip()
        inputs[str(name)] = f"{head} | {desc}" if desc else head
    return inputs


def capability_public_contract(name: str, provider_id: str, spec: Dict[str, Any], config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    limits: Dict[str, Any] = {}
    for limit_name, source in (spec.get("limits") or {}).items():
        value: Any = config or {}
        for part in str(source.get("config_key") or "").split("."):
            if not part or not isinstance(value, dict) or part not in value:
                value = source.get("default")
                break
            value = value[part]
        limits[str(limit_name)] = value
    item = {
        "name": str(name),
        "provider": str(provider_id),
        "purpose": str(spec.get("description") or ""),
        "effect": normalize_effect(spec.get("effect")),
        "inputs": compact_input_contract(spec.get("input_schema")),
        "returns": str(spec.get("returns") or ""),
        "caveats": [str(v) for v in (spec.get("caveats") or [])],
        "limits": limits,
    }
    item["confirmation"] = str(spec.get("confirmation") or "none")
    return item
