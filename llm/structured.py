"""Canonical structured contracts for Eyle LLM profiles.

One profile specification owns both provider-side JSON Schema and local strict
validation.  The provider may help enforce the contract, but local validation is
always authoritative.  This module never scans prose for embedded JSON, accepts
markdown fences, or translates alternate protocol shapes.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, Iterable


class StructuredResponseError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


_PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {"type": "string", "enum": ["replace", "create", "delete", "update"]},
        "path": {"type": "string", "minLength": 1},
        "content": {"type": "string"},
        "line_start": {"type": "integer", "minimum": 1},
        "line_end": {"type": "integer", "minimum": 1},
        "new_code": {"type": "string"},
    },
    "required": ["operation", "path"],
    "additionalProperties": False,
}


_INVESTIGATION_TARGET_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 80},
        "goal": {"type": "string", "minLength": 1, "maxLength": 500},
        "status": {"type": "string", "enum": ["open", "established", "dismissed"]},
        "evidence_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["id", "goal", "status", "evidence_ids", "reason"],
    "additionalProperties": False,
}

_AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "tool_calls": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "array", "minItems": 1, "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string", "minLength": 1},
                            "arguments": {"type": "object"},
                        },
                        "required": ["tool", "arguments"],
                        "additionalProperties": False,
                    },
                },
            ],
        },
        "patches": {"anyOf": [{"type": "null"}, {"type": "array", "minItems": 1, "items": _PATCH_SCHEMA}]},
        "needs_user": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "minLength": 1},
                        "missing_information": {"type": "string", "minLength": 1},
                    },
                    "required": ["question", "missing_information"],
                    "additionalProperties": False,
                },
            ]
        },
        "final": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string", "minLength": 1},
                        "limitations": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["answer", "limitations"],
                    "additionalProperties": False,
                },
            ]
        },
        "investigation_updates": {"type": "array", "items": _INVESTIGATION_TARGET_SCHEMA},
    },
    "required": ["tool_calls", "patches", "needs_user", "final", "investigation_updates"],
    "additionalProperties": False,
}

_GROUNDING_REFS_SCHEMA = {
    "type": "array", "minItems": 1,
    "items": {"type": "string", "minLength": 1, "maxLength": 160},
}

_CLAIM_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "material_satisfaction": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["satisfied", "gap", "blocked"]},
                "grounding_refs": _GROUNDING_REFS_SCHEMA,
                "reason": {"type": "string", "minLength": 1, "maxLength": 240},
            },
            "required": ["status", "grounding_refs", "reason"],
            "additionalProperties": False,
        },
        "answer_consistency": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["consistent", "conflict"]},
                "grounding_refs": _GROUNDING_REFS_SCHEMA,
                "reason": {"type": "string", "minLength": 1, "maxLength": 240},
            },
            "required": ["status", "grounding_refs", "reason"],
            "additionalProperties": False,
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "answer_ref": {"type": "string", "minLength": 1, "maxLength": 80},
                    "target_id": {"anyOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 80}]},
                    "statement": {"type": "string", "minLength": 1, "maxLength": 500},
                    "grounding_refs": _GROUNDING_REFS_SCHEMA,
                    "verdict": {"type": "string", "enum": ["supported", "contradicted", "insufficient"]},
                    "reason": {"type": "string", "maxLength": 160},
                },
                "required": ["answer_ref", "target_id", "statement", "grounding_refs", "verdict", "reason"],
                "additionalProperties": False,
            },
        },
        "semantic_gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["material_omission", "conflicting_evidence", "scope_gap"]},
                    "target_id": {"anyOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 80}]},
                    "grounding_refs": _GROUNDING_REFS_SCHEMA,
                    "required_property": {"type": "string", "minLength": 1, "maxLength": 300},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                "required": ["type", "target_id", "grounding_refs", "required_property", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["material_satisfaction", "answer_consistency", "claims", "semantic_gaps"],
    "additionalProperties": False,
}


_PROFILE_SCHEMAS = {
    "agent": _AGENT_SCHEMA,
    "claim_verifier": _CLAIM_REVIEW_SCHEMA,
}

_PROFILE_NAMES = {
    "agent": "eyle_agent_decision",
    "claim_verifier": "eyle_claim_review",
}

_PROFILE_TOP_LEVEL = {
    "agent": ("tool_calls", "patches", "needs_user", "final", "investigation_updates"),
    "claim_verifier": ("material_satisfaction", "answer_consistency", "claims", "semantic_gaps"),
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
    keys = mandatory_top_level_keys(profile)
    if profile == "claim_verifier":
        return (
            "Top-level JSON contract: return exactly one object containing material_satisfaction, answer_consistency, "
            "and the arrays claims, semantic_gaps. All four keys are always required. material_satisfaction is "
            "exactly {status,reason}, status=satisfied|gap; answer_consistency is exactly {status,reason}, "
            "status=consistent|conflict. Use [] when an array has no items. Never omit a key or wrap them."
        )
    return (
        "Top-level JSON contract: return exactly one object containing exactly these mandatory fields: "
        + ", ".join(keys) + "."
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
    _exact_keys(value, required=required, allowed=required, profile="agent")
    investigation = value.get("investigation_updates")
    if not isinstance(investigation, list):
        raise StructuredResponseError("AGENT_INVESTIGATION_INVALID", "investigation_updates must be an array")
    target_keys = {"id", "goal", "status", "evidence_ids", "reason"}
    for index, item in enumerate(investigation, start=1):
        item = _exact_item(item, target_keys, code="AGENT_INVESTIGATION_TARGET_SHAPE_INVALID", detail=f"investigation_updates[{index}] must contain exactly id, goal, status, evidence_ids and reason")
        _string(item["id"], code="AGENT_INVESTIGATION_TARGET_ID_INVALID", detail=f"investigation_updates[{index}].id must be non-empty")
        _string(item["goal"], code="AGENT_INVESTIGATION_TARGET_GOAL_INVALID", detail=f"investigation_updates[{index}].goal must be non-empty")
        if item["status"] not in {"open", "established", "dismissed"}:
            raise StructuredResponseError("AGENT_INVESTIGATION_TARGET_STATUS_INVALID", f"investigation_updates[{index}].status is invalid")
        _string_list(item["evidence_ids"], code="AGENT_INVESTIGATION_TARGET_EVIDENCE_INVALID", detail=f"investigation_updates[{index}].evidence_ids must be an array of non-empty IDs")
        _string(item["reason"], code="AGENT_INVESTIGATION_TARGET_REASON_INVALID", detail=f"investigation_updates[{index}].reason must be a string", nonempty=False)

    calls = value.get("tool_calls")
    patches = value.get("patches")
    question = value.get("needs_user")
    final = value.get("final")
    active_payloads = [
        name for name, payload in (
            ("tool_calls", calls), ("patches", patches), ("needs_user", question), ("final", final)
        ) if payload is not None
    ]
    if len(active_payloads) != 1:
        raise StructuredResponseError(
            "AGENT_PAYLOAD_AMBIGUOUS",
            "exactly one of tool_calls, patches, needs_user, or final must be non-null",
        )
    active = active_payloads[0]
    if active == "tool_calls":
        if not isinstance(calls, list) or not calls:
            raise StructuredResponseError("AGENT_TOOL_CALLS_INVALID", "tool_calls must be a non-empty array when selected")
        if len(calls) > 4:
            raise StructuredResponseError("AGENT_TOOL_CALL_LIMIT_EXCEEDED", "tool_calls may contain at most 4 calls per turn")
        normalized = []
        for index, item in enumerate(calls, start=1):
            if not isinstance(item, dict) or set(item) != {"tool", "arguments"}:
                raise StructuredResponseError("AGENT_TOOL_CALL_INVALID", f"tool_calls[{index}] must contain exactly tool and arguments")
            tool = item.get("tool")
            arguments = item.get("arguments")
            if not isinstance(tool, str) or not tool.strip() or not isinstance(arguments, dict):
                raise StructuredResponseError("AGENT_TOOL_CALL_INVALID", f"tool_calls[{index}] is invalid")
            normalized.append({"tool": tool.strip(), "arguments": arguments})
        return {"tool_calls": normalized, "investigation_updates": value["investigation_updates"]}
    if active == "patches":
        if not isinstance(patches, list) or not patches:
            raise StructuredResponseError("AGENT_PATCHES_INVALID", "patches must be a non-empty array when selected")
        normalized_patches = []
        for index, item in enumerate(patches, start=1):
            if not isinstance(item, dict):
                raise StructuredResponseError("AGENT_PATCH_INVALID", f"patches[{index}] must be an object")
            operation = item.get("operation")
            if operation in {"replace", "create"}:
                keys = {"operation", "path", "content"}
            elif operation == "delete":
                keys = {"operation", "path"}
            elif operation == "update":
                keys = {"operation", "path", "line_start", "line_end", "new_code"}
            else:
                raise StructuredResponseError("AGENT_PATCH_OPERATION_INVALID", f"patches[{index}].operation is invalid")
            if set(item) != keys:
                raise StructuredResponseError("AGENT_PATCH_SHAPE_INVALID", f"patches[{index}] must contain exactly the canonical fields for {operation}")
            if not isinstance(item.get("path"), str) or not item["path"].strip():
                raise StructuredResponseError("AGENT_PATCH_PATH_INVALID", f"patches[{index}].path must be a non-empty string")
            if operation in {"replace", "create"} and not isinstance(item.get("content"), str):
                raise StructuredResponseError("AGENT_PATCH_CONTENT_INVALID", f"patches[{index}].content must be a string")
            if operation == "update":
                if not isinstance(item.get("line_start"), int) or isinstance(item.get("line_start"), bool) or item["line_start"] < 1:
                    raise StructuredResponseError("AGENT_PATCH_RANGE_INVALID", f"patches[{index}].line_start must be a positive integer")
                if not isinstance(item.get("line_end"), int) or isinstance(item.get("line_end"), bool) or item["line_end"] < item["line_start"]:
                    raise StructuredResponseError("AGENT_PATCH_RANGE_INVALID", f"patches[{index}].line_end must be >= line_start")
                if not isinstance(item.get("new_code"), str):
                    raise StructuredResponseError("AGENT_PATCH_CONTENT_INVALID", f"patches[{index}].new_code must be a string")
            normalized_patches.append(dict(item))
        return {"patches": normalized_patches, "investigation_updates": value["investigation_updates"]}
    if active == "needs_user":
        if not isinstance(question, dict) or set(question) != {"question", "missing_information"}:
            raise StructuredResponseError(
                "AGENT_NEEDS_USER_INVALID",
                "needs_user must contain exactly question and missing_information when selected",
            )
        q = question.get("question")
        missing = question.get("missing_information")
        if not isinstance(q, str) or not q.strip() or not isinstance(missing, str) or not missing.strip():
            raise StructuredResponseError(
                "AGENT_NEEDS_USER_INVALID",
                "needs_user.question and needs_user.missing_information must be non-empty strings",
            )
        return {
            "needs_user": {"question": q.strip(), "missing_information": missing.strip()},
            "investigation_updates": value["investigation_updates"],
        }
    if active != "final" or final is None:
        raise StructuredResponseError("AGENT_FINAL_INVALID", "final must be the only non-null result payload")
    final_keys = {"answer", "limitations"}
    final = _exact_item(
        final, final_keys, code="AGENT_FINAL_SHAPE_INVALID",
        detail="final must contain exactly answer and limitations",
    )
    _string(final["answer"], code="AGENT_FINAL_ANSWER_INVALID", detail="final.answer must be a non-empty string")
    if not isinstance(final["limitations"], list) or any(not isinstance(item, str) for item in final["limitations"]):
        raise StructuredResponseError("AGENT_FINAL_LIMITATIONS_INVALID", "final.limitations must be an array of strings")
    return {"final": dict(final), "investigation_updates": value["investigation_updates"]}


def parse_claim_review_response(raw: Any) -> Dict[str, Any]:
    value = _object(raw)
    top = set(mandatory_top_level_keys("claim_verifier"))
    _exact_keys(value, required=top, allowed=top, profile="claim_review")

    satisfaction = _exact_item(
        value["material_satisfaction"], {"status", "grounding_refs", "reason"},
        code="CLAIM_REVIEW_MATERIAL_SATISFACTION_SHAPE_INVALID",
        detail="material_satisfaction must contain exactly status, grounding_refs and reason",
    )
    if satisfaction["status"] not in {"satisfied", "gap", "blocked"}:
        raise StructuredResponseError("CLAIM_REVIEW_MATERIAL_SATISFACTION_STATUS_INVALID", "material_satisfaction.status is invalid")
    _string_list(satisfaction["grounding_refs"], code="CLAIM_REVIEW_GROUNDING_REFS_INVALID", detail="material_satisfaction.grounding_refs must be a non-empty array")
    if not satisfaction["grounding_refs"]:
        raise StructuredResponseError("CLAIM_REVIEW_GROUNDING_REFS_REQUIRED", "material_satisfaction.grounding_refs must not be empty")
    _string(satisfaction["reason"], code="CLAIM_REVIEW_MATERIAL_SATISFACTION_REASON_INVALID", detail="material_satisfaction.reason must be non-empty")

    consistency = _exact_item(
        value["answer_consistency"], {"status", "grounding_refs", "reason"},
        code="CLAIM_REVIEW_ANSWER_CONSISTENCY_SHAPE_INVALID",
        detail="answer_consistency must contain exactly status, grounding_refs and reason",
    )
    if consistency["status"] not in {"consistent", "conflict"}:
        raise StructuredResponseError("CLAIM_REVIEW_ANSWER_CONSISTENCY_STATUS_INVALID", "answer_consistency.status is invalid")
    _string_list(consistency["grounding_refs"], code="CLAIM_REVIEW_GROUNDING_REFS_INVALID", detail="answer_consistency.grounding_refs must be a non-empty array")
    if not consistency["grounding_refs"]:
        raise StructuredResponseError("CLAIM_REVIEW_GROUNDING_REFS_REQUIRED", "answer_consistency.grounding_refs must not be empty")
    _string(consistency["reason"], code="CLAIM_REVIEW_ANSWER_CONSISTENCY_REASON_INVALID", detail="answer_consistency.reason must be non-empty")

    for key in ("claims", "semantic_gaps"):
        if not isinstance(value[key], list):
            raise StructuredResponseError(f"CLAIM_REVIEW_{key.upper()}_LIST_REQUIRED", f"{key} must be an array")

    claim_keys = {"answer_ref", "target_id", "statement", "grounding_refs", "verdict", "reason"}
    for index, item in enumerate(value["claims"], start=1):
        item = _exact_item(item, claim_keys, code="CLAIM_REVIEW_CLAIM_SHAPE_INVALID", detail=f"claims[{index}] must contain exactly the canonical Claim fields")
        _string(item["answer_ref"], code="CLAIM_REVIEW_ANSWER_REF_INVALID", detail=f"claims[{index}].answer_ref must be non-empty")
        if item["target_id"] is not None:
            _string(item["target_id"], code="CLAIM_REVIEW_TARGET_INVALID", detail=f"claims[{index}].target_id must be null or non-empty")
        _string(item["statement"], code="CLAIM_REVIEW_STATEMENT_INVALID", detail=f"claims[{index}].statement must be non-empty")
        _string_list(item["grounding_refs"], code="CLAIM_REVIEW_GROUNDING_REFS_INVALID", detail=f"claims[{index}].grounding_refs must be a non-empty array")
        if not item["grounding_refs"]:
            raise StructuredResponseError("CLAIM_REVIEW_GROUNDING_REFS_REQUIRED", f"claims[{index}].grounding_refs must not be empty")
        if item["verdict"] not in {"supported", "contradicted", "insufficient"}:
            raise StructuredResponseError("CLAIM_REVIEW_VERDICT_INVALID", f"claims[{index}].verdict is invalid")
        _string(item["reason"], code="CLAIM_REVIEW_REASON_INVALID", detail=f"claims[{index}].reason must be a string", nonempty=False)

    gap_keys = {"type", "target_id", "grounding_refs", "required_property", "reason"}
    for index, item in enumerate(value["semantic_gaps"], start=1):
        item = _exact_item(item, gap_keys, code="CLAIM_REVIEW_SEMANTIC_GAP_SHAPE_INVALID", detail=f"semantic_gaps[{index}] must contain exactly type, target_id, grounding_refs, required_property and reason")
        if item["type"] not in {"material_omission", "conflicting_evidence", "scope_gap"}:
            raise StructuredResponseError("CLAIM_REVIEW_SEMANTIC_GAP_TYPE_INVALID", f"semantic_gaps[{index}].type is invalid")
        if item["target_id"] is not None:
            _string(item["target_id"], code="CLAIM_REVIEW_SEMANTIC_GAP_TARGET_INVALID", detail=f"semantic_gaps[{index}].target_id must be null or non-empty")
        _string_list(item["grounding_refs"], code="CLAIM_REVIEW_GROUNDING_REFS_INVALID", detail=f"semantic_gaps[{index}].grounding_refs must be a non-empty array")
        if not item["grounding_refs"]:
            raise StructuredResponseError("CLAIM_REVIEW_GROUNDING_REFS_REQUIRED", f"semantic_gaps[{index}].grounding_refs must not be empty")
        _string(item["required_property"], code="CLAIM_REVIEW_REQUIRED_PROPERTY_INVALID", detail=f"semantic_gaps[{index}].required_property must be non-empty")
        _string(item["reason"], code="CLAIM_REVIEW_SEMANTIC_GAP_REASON_INVALID", detail=f"semantic_gaps[{index}].reason must be non-empty")
    return value


def parse_profile_response(raw: Any, profile: str) -> Dict[str, Any]:
    if profile == "agent":
        return parse_agent_response(raw)
    if profile == "claim_verifier":
        return parse_claim_review_response(raw)
    raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
