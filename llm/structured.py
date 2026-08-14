"""Canonical structured contracts for Eyle LLM profiles.

One profile specification owns both provider-side JSON Schema and local strict
validation.  The provider may help enforce the contract, but local validation is
always authoritative.  This module never scans prose for embedded JSON, accepts
markdown fences, or translates alternate protocol shapes.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Dict, Iterable


class StructuredResponseError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


_INVESTIGATION_ID_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 80, "pattern": r"^[A-Za-z0-9._-]+$"}
_INVESTIGATION_GOAL_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 500}
_INVESTIGATION_GROUNDING_ITEM_SCHEMA = {
    "type": "string", "minLength": 1, "maxLength": 160, "pattern": r"^mat-[0-9]+$",
}


_INVESTIGATION_TARGET_SCHEMA = {
    "type": "object",
    "properties": {
        "id": deepcopy(_INVESTIGATION_ID_SCHEMA),
        "goal": deepcopy(_INVESTIGATION_GOAL_SCHEMA),
        "status": {"type": "string", "enum": ["open", "established", "dismissed"]},
        "grounding_ids": {"type": "array", "items": deepcopy(_INVESTIGATION_GROUNDING_ITEM_SCHEMA)},
        "conclusion": {"type": "string", "maxLength": 1600},
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["id", "goal", "status", "grounding_ids", "conclusion", "reason"],
    "additionalProperties": False,
}


_TASK_ID_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 80, "pattern": r"^[A-Za-z0-9._-]+$"}
_TASK_DESCRIPTION_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 500}
_TASK_PARENT_SCHEMA = {
    "anyOf": [
        {"type": "null"},
        deepcopy(_TASK_ID_SCHEMA),
    ]
}


_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "id": deepcopy(_TASK_ID_SCHEMA),
        "parent_id": deepcopy(_TASK_PARENT_SCHEMA),
        "description": deepcopy(_TASK_DESCRIPTION_SCHEMA),
        "completion_criteria": {
            "type": "array", "minItems": 1, "maxItems": 12,
            "items": {"type": "string", "minLength": 1, "maxLength": 400},
        },
        "status": {"type": "string", "enum": ["open", "completed", "dropped"]},
        "result": {"type": "string", "maxLength": 1200},
        "grounding_ids": {
            "type": "array",
            "items": deepcopy(_INVESTIGATION_GROUNDING_ITEM_SCHEMA),
        },
    },
    "required": [
        "id", "parent_id", "description", "completion_criteria",
        "status", "result", "grounding_ids",
    ],
    "additionalProperties": False,
}


_TASK_MEMORY_EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 4, "maxLength": 80, "pattern": r"^ev-[A-Za-z0-9._-]+$"},
        "material_id": deepcopy(_INVESTIGATION_GROUNDING_ITEM_SCHEMA),
        "selector": {"type": "object"},
    },
    "required": ["id", "material_id", "selector"],
    "additionalProperties": False,
}

_TASK_MEMORY_FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 3, "maxLength": 80, "pattern": r"^f-[A-Za-z0-9._-]+$"},
        "statement": {"type": "string", "minLength": 1, "maxLength": 1200},
        "evidence_ids": {
            "type": "array", "maxItems": 16,
            "items": {"type": "string", "minLength": 4, "maxLength": 80, "pattern": r"^ev-[A-Za-z0-9._-]+$"},
        },
    },
    "required": ["id", "statement", "evidence_ids"],
    "additionalProperties": False,
}

_TASK_MEMORY_CONCLUSION_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 3, "maxLength": 80, "pattern": r"^c-[A-Za-z0-9._-]+$"},
        "statement": {"type": "string", "minLength": 1, "maxLength": 1600},
        "evidence_ids": {
            "type": "array", "maxItems": 16,
            "items": {"type": "string", "minLength": 4, "maxLength": 80, "pattern": r"^ev-[A-Za-z0-9._-]+$"},
        },
        "finding_ids": {
            "type": "array", "maxItems": 16,
            "items": {"type": "string", "minLength": 3, "maxLength": 80, "pattern": r"^f-[A-Za-z0-9._-]+$"},
        },
    },
    "required": ["id", "statement", "evidence_ids", "finding_ids"],
    "additionalProperties": False,
}

_TASK_MEMORY_UPDATES_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence": {"type": "array", "maxItems": 12, "items": _TASK_MEMORY_EVIDENCE_SCHEMA},
        "findings": {"type": "array", "maxItems": 12, "items": _TASK_MEMORY_FINDING_SCHEMA},
        "conclusions": {"type": "array", "maxItems": 8, "items": _TASK_MEMORY_CONCLUSION_SCHEMA},
    },
    "required": ["evidence", "findings", "conclusions"],
    "additionalProperties": False,
}


_CAPABILITY_CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "capability": {"type": "string", "minLength": 1},
        "arguments": {"type": "object"},
    },
    "required": ["capability", "arguments"],
    "additionalProperties": False,
}

_AGENT_ACTION_SCHEMA = {
    "anyOf": [
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["capability_calls"]},
                "calls": {"type": "array", "minItems": 1, "maxItems": 4, "items": _CAPABILITY_CALL_SCHEMA},
            },
            "required": ["kind", "calls"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["await_user"]},
                "question": {"type": "string", "minLength": 1, "maxLength": 800},
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                "options": {
                    "type": "array", "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "minLength": 1, "maxLength": 80, "pattern": r"^[A-Za-z0-9._-]+$"},
                            "label": {"type": "string", "minLength": 1, "maxLength": 200},
                        },
                        "required": ["id", "label"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["kind", "question", "reason", "options"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["complete"]},
                "answer": {"type": "string", "minLength": 1},
                "limitations": {"type": "array", "items": {"type": "string"}},
                "grounding_ids": {"type": "array", "items": {"type": "string", "minLength": 1, "pattern": r"^mat-[0-9]+$"}},
                "effect_ids": {"type": "array", "items": {"type": "string", "minLength": 1, "pattern": r"^eff-[0-9]+$"}},
            },
            "required": ["kind", "answer", "limitations", "grounding_ids", "effect_ids"],
            "additionalProperties": False,
        },
    ]
}

_AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": _AGENT_ACTION_SCHEMA,
        "investigation_updates": {"type": "array", "items": _INVESTIGATION_TARGET_SCHEMA},
        "task_updates": {"type": "array", "items": _TASK_SCHEMA},
        "memory_updates": _TASK_MEMORY_UPDATES_SCHEMA,
    },
    "required": ["action"],
    "additionalProperties": False,
}

_PROFILE_SCHEMAS = {
    "agent": _AGENT_SCHEMA,
}

_PROFILE_NAMES = {
    "agent": "eyle_agent_decision",
}

_PROFILE_TOP_LEVEL = {
    "agent": ("action",),
}

def schema_for_profile(profile: str) -> Dict[str, Any]:
    try:
        return deepcopy(_PROFILE_SCHEMAS[profile])
    except KeyError as exc:
        raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}") from exc


def json_schema_response_format(profile: str) -> Dict[str, Any]:
    try:
        name = _PROFILE_NAMES[profile]
    except KeyError as exc:
        raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}") from exc
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema_for_profile(profile),
        },
    }


def mandatory_top_level_keys(profile: str) -> tuple[str, ...]:
    try:
        return _PROFILE_TOP_LEVEL[profile]
    except KeyError as exc:
        raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}") from exc


def contract_instruction(profile: str) -> str:
    mandatory_top_level_keys(profile)
    return (
        "Return only the JSON object required by the schema. "
        "investigation_updates, task_updates and memory_updates are optional state deltas; omit them when unused. "
        "action contains exactly one action kind."
    )


def _object(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise StructuredResponseError("STRUCTURED_EMPTY", "structured response is empty")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StructuredResponseError(
            "STRUCTURED_JSON_INVALID",
            f"response must be exactly one JSON object: {exc.msg}",
        ) from exc
    if not isinstance(value, dict):
        raise StructuredResponseError("STRUCTURED_OBJECT_REQUIRED", "top-level JSON must be an object")
    return value


def observed_top_level(raw: Any) -> Dict[str, Any] | None:
    try:
        return _object(raw)
    except StructuredResponseError:
        return None


def _exact_keys(value: Dict[str, Any], *, required: Iterable[str], allowed: Iterable[str], profile: str) -> None:
    required_set = set(required)
    allowed_set = set(allowed)
    missing = sorted(required_set - set(value))
    if missing:
        raise StructuredResponseError(
            f"{profile.upper()}_MISSING_KEYS",
            "missing top-level field(s): " + ", ".join(missing),
        )
    extra = sorted(set(value) - allowed_set)
    if extra:
        raise StructuredResponseError(
            f"{profile.upper()}_UNKNOWN_KEYS",
            "unknown top-level field(s): " + ", ".join(extra),
        )


def _string(value: Any, *, code: str, detail: str, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise StructuredResponseError(code, detail)
    return value


def _string_list(value: Any, *, code: str, detail: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise StructuredResponseError(code, detail)
    return list(value)


def _exact_item(value: Any, keys: set[str], *, code: str, detail: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise StructuredResponseError(code, detail)
    return value


def parse_agent_response(raw: Any) -> Dict[str, Any]:
    value = _object(raw)
    required = set(mandatory_top_level_keys("agent"))
    allowed = {"action", "investigation_updates", "task_updates", "memory_updates"}
    _exact_keys(value, required=required, allowed=allowed, profile="agent")

    investigation = value.get("investigation_updates", [])
    if "investigation_updates" in value and not isinstance(investigation, list):
        raise StructuredResponseError("AGENT_INVESTIGATION_INVALID", "investigation_updates must be an array")
    target_keys = {"id", "goal", "status", "grounding_ids", "conclusion", "reason"}
    for index, item in enumerate(investigation, start=1):
        item = _exact_item(item, target_keys, code="AGENT_INVESTIGATION_TARGET_SHAPE_INVALID", detail=f"investigation_updates[{index}] must contain exactly id, goal, status, grounding_ids, conclusion and reason")
        _string(item["id"], code="AGENT_INVESTIGATION_TARGET_ID_INVALID", detail=f"investigation_updates[{index}].id must be non-empty")
        _string(item["goal"], code="AGENT_INVESTIGATION_TARGET_GOAL_INVALID", detail=f"investigation_updates[{index}].goal must be non-empty")
        if item["status"] not in {"open", "established", "dismissed"}:
            raise StructuredResponseError("AGENT_INVESTIGATION_TARGET_STATUS_INVALID", f"investigation_updates[{index}].status is invalid")
        grounding_ids = _string_list(item["grounding_ids"], code="AGENT_INVESTIGATION_TARGET_GROUNDING_INVALID", detail=f"investigation_updates[{index}].grounding_ids must be an array of canonical mat-* grounding IDs")
        invalid_grounding = next((ref for ref in grounding_ids if re.fullmatch(r"mat-[0-9]+", ref) is None), None)
        if invalid_grounding is not None:
            raise StructuredResponseError("AGENT_INVESTIGATION_TARGET_GROUNDING_INVALID", f"investigation_updates[{index}].grounding_ids contains a noncanonical grounding ID")
        conclusion = _string(item["conclusion"], code="AGENT_INVESTIGATION_TARGET_CONCLUSION_INVALID", detail=f"investigation_updates[{index}].conclusion must be a string", nonempty=False)
        if len(conclusion) > 1600:
            raise StructuredResponseError("AGENT_INVESTIGATION_TARGET_CONCLUSION_INVALID", f"investigation_updates[{index}].conclusion is too long")
        if item["status"] == "established" and not conclusion.strip():
            raise StructuredResponseError("AGENT_INVESTIGATION_ESTABLISHED_CONCLUSION_REQUIRED", f"investigation_updates[{index}].conclusion is required when status is established")
        _string(item["reason"], code="AGENT_INVESTIGATION_TARGET_REASON_INVALID", detail=f"investigation_updates[{index}].reason must be a string", nonempty=False)

    task_updates = value.get("task_updates", [])
    if "task_updates" in value and not isinstance(task_updates, list):
        raise StructuredResponseError("AGENT_TASK_UPDATES_INVALID", "task_updates must be an array")
    task_keys = {"id", "parent_id", "description", "completion_criteria", "status", "result", "grounding_ids"}
    for index, item in enumerate(task_updates, start=1):
        item = _exact_item(
            item, task_keys,
            code="AGENT_TASK_SHAPE_INVALID",
            detail=f"task_updates[{index}] must contain exactly id, parent_id, description, completion_criteria, status, result and grounding_ids",
        )
        task_id = _string(item["id"], code="AGENT_TASK_ID_INVALID", detail=f"task_updates[{index}].id must be non-empty").strip()
        if len(task_id) > 80 or re.fullmatch(r"[A-Za-z0-9._-]+", task_id) is None:
            raise StructuredResponseError("AGENT_TASK_ID_INVALID", f"task_updates[{index}].id is invalid")
        parent_id = item["parent_id"]
        if parent_id is not None:
            _string(parent_id, code="AGENT_TASK_PARENT_ID_INVALID", detail=f"task_updates[{index}].parent_id must be null or a canonical task ID")
            if len(parent_id) > 80 or re.fullmatch(r"[A-Za-z0-9._-]+", parent_id) is None or parent_id == task_id:
                raise StructuredResponseError("AGENT_TASK_PARENT_ID_INVALID", f"task_updates[{index}].parent_id is invalid")
        description = _string(item["description"], code="AGENT_TASK_DESCRIPTION_INVALID", detail=f"task_updates[{index}].description must be non-empty")
        if len(description) > 500:
            raise StructuredResponseError("AGENT_TASK_DESCRIPTION_INVALID", f"task_updates[{index}].description is too long")
        criteria = _string_list(item["completion_criteria"], code="AGENT_TASK_COMPLETION_CRITERIA_INVALID", detail=f"task_updates[{index}].completion_criteria must be a non-empty array of strings")
        if not criteria or len(criteria) > 12 or any(len(value.strip()) > 400 for value in criteria):
            raise StructuredResponseError("AGENT_TASK_COMPLETION_CRITERIA_INVALID", f"task_updates[{index}].completion_criteria is invalid")
        status = item["status"]
        if status not in {"open", "completed", "dropped"}:
            raise StructuredResponseError("AGENT_TASK_STATUS_INVALID", f"task_updates[{index}].status is invalid")
        result = _string(item["result"], code="AGENT_TASK_RESULT_INVALID", detail=f"task_updates[{index}].result must be a string", nonempty=False)
        if len(result) > 1200:
            raise StructuredResponseError("AGENT_TASK_RESULT_INVALID", f"task_updates[{index}].result is too long")
        if status in {"completed", "dropped"} and not result.strip():
            raise StructuredResponseError("AGENT_TASK_CLOSED_RESULT_REQUIRED", f"task_updates[{index}].result is required when status is {status}")
        grounding_ids = _string_list(item["grounding_ids"], code="AGENT_TASK_GROUNDING_INVALID", detail=f"task_updates[{index}].grounding_ids must be an array of canonical mat-* grounding IDs")
        if any(re.fullmatch(r"mat-[0-9]+", ref) is None for ref in grounding_ids):
            raise StructuredResponseError("AGENT_TASK_GROUNDING_INVALID", f"task_updates[{index}].grounding_ids contains a noncanonical grounding ID")

    memory_updates = value.get("memory_updates")
    if "memory_updates" in value:
        if not isinstance(memory_updates, dict) or set(memory_updates) != {"evidence", "findings", "conclusions"}:
            raise StructuredResponseError("AGENT_MEMORY_UPDATES_INVALID", "memory_updates must contain exactly evidence, findings and conclusions arrays")
        for key in ("evidence", "findings", "conclusions"):
            if not isinstance(memory_updates.get(key), list):
                raise StructuredResponseError("AGENT_MEMORY_UPDATES_INVALID", f"memory_updates.{key} must be an array")
        if len(memory_updates["evidence"]) > 12 or len(memory_updates["findings"]) > 12 or len(memory_updates["conclusions"]) > 8:
            raise StructuredResponseError("AGENT_MEMORY_UPDATES_INVALID", "memory_updates exceeds the per-turn update limit")
        for index, item in enumerate(memory_updates["evidence"], start=1):
            item = _exact_item(item, {"id", "material_id", "selector"}, code="AGENT_MEMORY_EVIDENCE_INVALID", detail=f"memory_updates.evidence[{index}] has invalid shape")
            if not isinstance(item.get("id"), str) or re.fullmatch(r"ev-[A-Za-z0-9._-]+", item["id"]) is None:
                raise StructuredResponseError("AGENT_MEMORY_EVIDENCE_INVALID", f"memory_updates.evidence[{index}].id is invalid")
            if not isinstance(item.get("material_id"), str) or re.fullmatch(r"mat-[0-9]+", item["material_id"]) is None:
                raise StructuredResponseError("AGENT_MEMORY_EVIDENCE_INVALID", f"memory_updates.evidence[{index}].material_id is invalid")
            if not isinstance(item.get("selector"), dict):
                raise StructuredResponseError("AGENT_MEMORY_EVIDENCE_INVALID", f"memory_updates.evidence[{index}].selector must be an object")
        for index, item in enumerate(memory_updates["findings"], start=1):
            item = _exact_item(item, {"id", "statement", "evidence_ids"}, code="AGENT_MEMORY_FINDING_INVALID", detail=f"memory_updates.findings[{index}] has invalid shape")
            if not isinstance(item.get("id"), str) or re.fullmatch(r"f-[A-Za-z0-9._-]+", item["id"]) is None:
                raise StructuredResponseError("AGENT_MEMORY_FINDING_INVALID", f"memory_updates.findings[{index}].id is invalid")
            if not isinstance(item.get("statement"), str) or not item["statement"].strip() or len(item["statement"]) > 1200:
                raise StructuredResponseError("AGENT_MEMORY_FINDING_INVALID", f"memory_updates.findings[{index}].statement is invalid")
            refs = _string_list(item.get("evidence_ids"), code="AGENT_MEMORY_FINDING_INVALID", detail=f"memory_updates.findings[{index}].evidence_ids must be an array of ev-* IDs")
            if len(refs) > 16 or any(re.fullmatch(r"ev-[A-Za-z0-9._-]+", ref) is None for ref in refs):
                raise StructuredResponseError("AGENT_MEMORY_FINDING_INVALID", f"memory_updates.findings[{index}].evidence_ids is invalid")
        for index, item in enumerate(memory_updates["conclusions"], start=1):
            item = _exact_item(item, {"id", "statement", "evidence_ids", "finding_ids"}, code="AGENT_MEMORY_CONCLUSION_INVALID", detail=f"memory_updates.conclusions[{index}] has invalid shape")
            if not isinstance(item.get("id"), str) or re.fullmatch(r"c-[A-Za-z0-9._-]+", item["id"]) is None:
                raise StructuredResponseError("AGENT_MEMORY_CONCLUSION_INVALID", f"memory_updates.conclusions[{index}].id is invalid")
            if not isinstance(item.get("statement"), str) or not item["statement"].strip() or len(item["statement"]) > 1600:
                raise StructuredResponseError("AGENT_MEMORY_CONCLUSION_INVALID", f"memory_updates.conclusions[{index}].statement is invalid")
            evidence_refs = _string_list(item.get("evidence_ids"), code="AGENT_MEMORY_CONCLUSION_INVALID", detail=f"memory_updates.conclusions[{index}].evidence_ids must be an array")
            finding_refs = _string_list(item.get("finding_ids"), code="AGENT_MEMORY_CONCLUSION_INVALID", detail=f"memory_updates.conclusions[{index}].finding_ids must be an array")
            if len(evidence_refs) > 16 or any(re.fullmatch(r"ev-[A-Za-z0-9._-]+", ref) is None for ref in evidence_refs):
                raise StructuredResponseError("AGENT_MEMORY_CONCLUSION_INVALID", f"memory_updates.conclusions[{index}].evidence_ids is invalid")
            if len(finding_refs) > 16 or any(re.fullmatch(r"f-[A-Za-z0-9._-]+", ref) is None for ref in finding_refs):
                raise StructuredResponseError("AGENT_MEMORY_CONCLUSION_INVALID", f"memory_updates.conclusions[{index}].finding_ids is invalid")

    action = value.get("action")
    if not isinstance(action, dict):
        raise StructuredResponseError("AGENT_ACTION_INVALID", "action must be one discriminated object")
    kind = action.get("kind")
    if kind == "capability_calls":
        if set(action) != {"kind", "calls"}:
            raise StructuredResponseError("AGENT_CAPABILITY_CALLS_SHAPE_INVALID", "capability_calls action must contain exactly kind and calls")
        calls = action.get("calls")
        if not isinstance(calls, list) or not calls:
            raise StructuredResponseError("AGENT_CAPABILITY_CALLS_INVALID", "capability_calls.calls must be a non-empty array")
        if len(calls) > 4:
            raise StructuredResponseError("AGENT_CAPABILITY_CALL_LIMIT_EXCEEDED", "capability_calls.calls may contain at most 4 calls per turn")
        normalized = []
        for index, item in enumerate(calls, start=1):
            if not isinstance(item, dict) or set(item) != {"capability", "arguments"}:
                raise StructuredResponseError("AGENT_CAPABILITY_CALL_INVALID", f"action.calls[{index}] must contain exactly capability and arguments")
            capability = item.get("capability")
            arguments = item.get("arguments")
            if not isinstance(capability, str) or not capability.strip() or not isinstance(arguments, dict):
                raise StructuredResponseError("AGENT_CAPABILITY_CALL_INVALID", f"action.calls[{index}] is invalid")
            normalized.append({"capability": capability.strip(), "arguments": arguments})
        normalized_action = {"kind": "capability_calls", "calls": normalized}
    elif kind == "await_user":
        if set(action) != {"kind", "question", "reason", "options"}:
            raise StructuredResponseError("AGENT_AWAIT_USER_INVALID", "await_user action must contain exactly kind, question, reason and options")
        question = action.get("question")
        reason = action.get("reason")
        options = action.get("options")
        if not isinstance(question, str) or not question.strip() or len(question.strip()) > 800:
            raise StructuredResponseError("AGENT_AWAIT_USER_INVALID", "await_user question must be a non-empty bounded string")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
            raise StructuredResponseError("AGENT_AWAIT_USER_INVALID", "await_user reason must be a non-empty bounded string")
        if not isinstance(options, list) or len(options) > 4:
            raise StructuredResponseError("AGENT_AWAIT_USER_INVALID", "await_user options must be an array with at most 4 items")
        normalized_options = []
        option_ids = set()
        for index, item in enumerate(options, start=1):
            if not isinstance(item, dict) or set(item) != {"id", "label"}:
                raise StructuredResponseError("AGENT_AWAIT_USER_INVALID", f"await_user options[{index}] must contain exactly id and label")
            option_id = item.get("id")
            label = item.get("label")
            if not isinstance(option_id, str) or re.fullmatch(r"[A-Za-z0-9._-]+", option_id.strip()) is None or len(option_id.strip()) > 80:
                raise StructuredResponseError("AGENT_AWAIT_USER_INVALID", f"await_user options[{index}].id is invalid")
            if option_id.strip() in option_ids:
                raise StructuredResponseError("AGENT_AWAIT_USER_INVALID", "await_user option IDs must be unique")
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 200:
                raise StructuredResponseError("AGENT_AWAIT_USER_INVALID", f"await_user options[{index}].label is invalid")
            option_ids.add(option_id.strip())
            normalized_options.append({"id": option_id.strip(), "label": label.strip()})
        normalized_action = {
            "kind": "await_user", "question": question.strip(), "reason": reason.strip(),
            "options": normalized_options,
        }
    elif kind == "complete":
        complete_keys = {"kind", "answer", "limitations", "grounding_ids", "effect_ids"}
        action = _exact_item(action, complete_keys, code="AGENT_COMPLETE_SHAPE_INVALID", detail="complete action must contain exactly kind, answer, limitations, grounding_ids and effect_ids")
        _string(action["answer"], code="AGENT_COMPLETE_ANSWER_INVALID", detail="complete.answer must be a non-empty string")
        if not isinstance(action["limitations"], list) or any(not isinstance(item, str) for item in action["limitations"]):
            raise StructuredResponseError("AGENT_COMPLETE_LIMITATIONS_INVALID", "complete.limitations must be an array of strings")
        grounding_ids = _string_list(action["grounding_ids"], code="AGENT_COMPLETE_GROUNDING_INVALID", detail="complete.grounding_ids must be an array of canonical mat-* grounding IDs")
        if any(re.fullmatch(r"mat-[0-9]+", ref) is None for ref in grounding_ids):
            raise StructuredResponseError("AGENT_COMPLETE_GROUNDING_INVALID", "complete.grounding_ids contains a noncanonical grounding ID")
        effect_ids = _string_list(action["effect_ids"], code="AGENT_COMPLETE_EFFECT_INVALID", detail="complete.effect_ids must be an array of canonical eff-* physical-effect IDs")
        if any(re.fullmatch(r"eff-[0-9]+", ref) is None for ref in effect_ids):
            raise StructuredResponseError("AGENT_COMPLETE_EFFECT_INVALID", "complete.effect_ids contains a noncanonical effect ID")
        normalized_action = dict(action)
    else:
        raise StructuredResponseError("AGENT_ACTION_KIND_INVALID", "action.kind must be capability_calls, await_user, or complete")

    normalized = {"action": normalized_action}
    if "investigation_updates" in value:
        normalized["investigation_updates"] = investigation
    if "task_updates" in value:
        normalized["task_updates"] = task_updates
    if "memory_updates" in value:
        normalized["memory_updates"] = memory_updates
    return normalized


def parse_profile_response(raw: Any, profile: str) -> Dict[str, Any]:
    if profile == "agent":
        return parse_agent_response(raw)
    raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
