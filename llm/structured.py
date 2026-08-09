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

_WORKSPACE_SCOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["none", "read", "write"]},
        "reason": {"type": "string", "minLength": 1, "maxLength": 300},
    },
    "required": ["mode", "reason"],
    "additionalProperties": False,
}

_AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["tool_calls", "patches", "needs_user", "final"]},
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
        "needs_user": {"anyOf": [{"type": "null"}, {"type": "string", "minLength": 1}]},
        "final": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string", "minLength": 1},
                        "evidence_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
                        "limitations": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["answer", "evidence_ids", "limitations"],
                    "additionalProperties": False,
                },
            ]
        },
        "workspace_scope": _WORKSPACE_SCOPE_SCHEMA,
        "investigation": {"type": "array", "items": _INVESTIGATION_TARGET_SCHEMA},
    },
    "required": ["action", "tool_calls", "patches", "needs_user", "final", "workspace_scope", "investigation"],
    "additionalProperties": False,
}

_CLAIM_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "answer_ref": {"type": "string", "minLength": 1, "maxLength": 80},
                    "target_id": {"anyOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 80}]},
                    "statement": {"type": "string", "minLength": 1, "maxLength": 500},
                    "kind": {"type": "string", "enum": ["fact", "bug", "risk", "recommendation"]},
                    "evidence_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "verdict": {"type": "string", "enum": ["supported", "contradicted", "insufficient"]},
                    "reason": {"type": "string", "maxLength": 160},
                },
                "required": ["id", "answer_ref", "target_id", "statement", "kind", "evidence_ids", "verdict", "reason"],
                "additionalProperties": False,
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "enum": ["fact", "bug", "risk", "recommendation"]},
                    "claim_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
                },
                "required": ["id", "type", "claim_ids"],
                "additionalProperties": False,
            },
        },
        "semantic_gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "enum": ["material_omission", "conflicting_evidence", "scope_gap"]},
                    "target_id": {"anyOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 80}]},
                    "evidence_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                "required": ["id", "type", "target_id", "evidence_ids", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims", "findings", "semantic_gaps"],
    "additionalProperties": False,
}

_CLAIM_REPAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "repairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string", "minLength": 1},
                    "target": {"type": "string", "minLength": 1},
                    "replacement": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
                },
                "required": ["claim_id", "target", "replacement", "evidence_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["repairs"],
    "additionalProperties": False,
}

_PROFILE_SCHEMAS = {
    "agent": _AGENT_SCHEMA,
    "claim_verifier": _CLAIM_REVIEW_SCHEMA,
    "claim_repair": _CLAIM_REPAIR_SCHEMA,
}

_PROFILE_NAMES = {
    "agent": "eyle_agent_decision",
    "claim_verifier": "eyle_claim_review",
    "claim_repair": "eyle_claim_repair",
}

_PROFILE_TOP_LEVEL = {
    "agent": ("action", "tool_calls", "patches", "needs_user", "final", "workspace_scope", "investigation"),
    "claim_verifier": ("claims", "findings", "semantic_gaps"),
    "claim_repair": ("repairs",),
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
            "Top-level JSON contract: return exactly one object containing exactly the mandatory arrays "
            "claims, findings, semantic_gaps. All three keys are always required. Use [] when an array has no items. "
            "Never omit a key and never wrap these fields inside another object."
        )
    if profile == "claim_repair":
        return (
            "Top-level JSON contract: return exactly one object containing exactly the mandatory array repairs. "
            "Use [] when no local repair is possible. Never wrap repairs inside another object."
        )
    return (
        "Top-level JSON contract: return exactly one object containing exactly these mandatory fields: "
        + ", ".join(keys) + "."
    )


def retry_instruction(profile: str, error: StructuredResponseError, observed: Any = None) -> str:
    keys = mandatory_top_level_keys(profile)
    observed_keys = sorted(observed.keys()) if isinstance(observed, dict) else []
    missing = [key for key in keys if key not in observed_keys]
    pieces = [
        f"STRUCTURAL RETRY for {profile}: previous output violated the canonical contract ({error.code}).",
        "Return exactly one JSON object with no prose, markdown, reasoning, or alternate fields.",
        contract_instruction(profile),
    ]
    if observed_keys:
        pieces.append("Observed top-level keys: " + ", ".join(observed_keys) + ".")
    if missing:
        pieces.append("Missing mandatory top-level keys: " + ", ".join(missing) + ".")
    if error.detail and error.detail != error.code:
        pieces.append("Validation detail: " + error.detail + ".")
    return " ".join(pieces)


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
    action = value.get("action")
    if action not in {"tool_calls", "patches", "needs_user", "final"}:
        raise StructuredResponseError("AGENT_ACTION_INVALID", "action must be tool_calls, patches, needs_user, or final")
    workspace_scope = value.get("workspace_scope")
    workspace_scope = _exact_item(
        workspace_scope, {"mode", "reason"}, code="AGENT_WORKSPACE_SCOPE_SHAPE_INVALID",
        detail="workspace_scope must contain exactly mode and reason",
    )
    if workspace_scope.get("mode") not in {"none", "read", "write"}:
        raise StructuredResponseError("AGENT_WORKSPACE_SCOPE_MODE_INVALID", "workspace_scope.mode must be none, read, or write")
    _string(
        workspace_scope.get("reason"), code="AGENT_WORKSPACE_SCOPE_REASON_INVALID",
        detail="workspace_scope.reason must be a non-empty string",
    )
    normalized_scope = {"mode": workspace_scope["mode"], "reason": workspace_scope["reason"].strip()}
    investigation = value.get("investigation")
    if not isinstance(investigation, list):
        raise StructuredResponseError("AGENT_INVESTIGATION_INVALID", "investigation must be an array")
    target_keys = {"id", "goal", "status", "evidence_ids", "reason"}
    for index, item in enumerate(investigation, start=1):
        item = _exact_item(item, target_keys, code="AGENT_INVESTIGATION_TARGET_SHAPE_INVALID", detail=f"investigation[{index}] must contain exactly id, goal, status, evidence_ids and reason")
        _string(item["id"], code="AGENT_INVESTIGATION_TARGET_ID_INVALID", detail=f"investigation[{index}].id must be non-empty")
        _string(item["goal"], code="AGENT_INVESTIGATION_TARGET_GOAL_INVALID", detail=f"investigation[{index}].goal must be non-empty")
        if item["status"] not in {"open", "established", "dismissed"}:
            raise StructuredResponseError("AGENT_INVESTIGATION_TARGET_STATUS_INVALID", f"investigation[{index}].status is invalid")
        _string_list(item["evidence_ids"], code="AGENT_INVESTIGATION_TARGET_EVIDENCE_INVALID", detail=f"investigation[{index}].evidence_ids must be an array of non-empty IDs")
        _string(item["reason"], code="AGENT_INVESTIGATION_TARGET_REASON_INVALID", detail=f"investigation[{index}].reason must be a string", nonempty=False)

    calls = value.get("tool_calls")
    patches = value.get("patches")
    question = value.get("needs_user")
    final = value.get("final")
    if action == "tool_calls":
        if not isinstance(calls, list) or not calls or patches is not None or question is not None or final is not None:
            raise StructuredResponseError("AGENT_TOOL_CALLS_INVALID", "tool_calls action requires only a non-empty tool_calls payload")
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
        return {"tool_calls": normalized, "workspace_scope": normalized_scope, "investigation": value["investigation"]}
    if action == "patches":
        if not isinstance(patches, list) or not patches or calls is not None or question is not None or final is not None:
            raise StructuredResponseError("AGENT_PATCHES_INVALID", "patches action requires only a non-empty patches payload")
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
        return {"patches": normalized_patches, "workspace_scope": normalized_scope, "investigation": value["investigation"]}
    if action == "needs_user":
        if not isinstance(question, str) or not question.strip() or calls is not None or patches is not None or final is not None:
            raise StructuredResponseError("AGENT_NEEDS_USER_INVALID", "needs_user action requires only a non-empty needs_user payload")
        return {"needs_user": question.strip(), "workspace_scope": normalized_scope, "investigation": value["investigation"]}
    if final is None or calls is not None or patches is not None or question is not None:
        raise StructuredResponseError("AGENT_FINAL_INVALID", "final action requires only the final payload")
    final_keys = {"answer", "evidence_ids", "limitations"}
    final = _exact_item(
        final, final_keys, code="AGENT_FINAL_SHAPE_INVALID",
        detail="final must contain exactly answer, evidence_ids and limitations",
    )
    _string(final["answer"], code="AGENT_FINAL_ANSWER_INVALID", detail="final.answer must be a non-empty string")
    _string_list(final["evidence_ids"], code="AGENT_FINAL_EVIDENCE_IDS_INVALID", detail="final.evidence_ids must be an array of non-empty IDs")
    if not isinstance(final["limitations"], list) or any(not isinstance(item, str) for item in final["limitations"]):
        raise StructuredResponseError("AGENT_FINAL_LIMITATIONS_INVALID", "final.limitations must be an array of strings")
    return {"final": dict(final), "workspace_scope": normalized_scope, "investigation": value["investigation"]}


def parse_claim_review_response(raw: Any) -> Dict[str, Any]:
    value = _object(raw)
    top = set(mandatory_top_level_keys("claim_verifier"))
    _exact_keys(value, required=top, allowed=top, profile="claim_review")
    for key in top:
        if not isinstance(value[key], list):
            raise StructuredResponseError(f"CLAIM_REVIEW_{key.upper()}_LIST_REQUIRED", f"{key} must be an array")

    claim_keys = {"id", "answer_ref", "target_id", "statement", "kind", "evidence_ids", "verdict", "reason"}
    for index, item in enumerate(value["claims"], start=1):
        item = _exact_item(item, claim_keys, code="CLAIM_REVIEW_CLAIM_SHAPE_INVALID", detail=f"claims[{index}] must contain exactly the canonical Claim fields")
        _string(item["id"], code="CLAIM_REVIEW_CLAIM_ID_INVALID", detail=f"claims[{index}].id must be a non-empty string")
        _string(item["answer_ref"], code="CLAIM_REVIEW_ANSWER_REF_INVALID", detail=f"claims[{index}].answer_ref must be a non-empty string")
        if item["target_id"] is not None:
            _string(item["target_id"], code="CLAIM_REVIEW_TARGET_INVALID", detail=f"claims[{index}].target_id must be null or a non-empty string")
        _string(item["statement"], code="CLAIM_REVIEW_STATEMENT_INVALID", detail=f"claims[{index}].statement must be a non-empty string")
        if item["kind"] not in {"fact", "bug", "risk", "recommendation"}:
            raise StructuredResponseError("CLAIM_REVIEW_KIND_INVALID", f"claims[{index}].kind is invalid")
        _string_list(item["evidence_ids"], code="CLAIM_REVIEW_EVIDENCE_IDS_INVALID", detail=f"claims[{index}].evidence_ids must be an array of non-empty IDs")
        if item["verdict"] not in {"supported", "contradicted", "insufficient"}:
            raise StructuredResponseError("CLAIM_REVIEW_VERDICT_INVALID", f"claims[{index}].verdict is invalid")
        _string(item["reason"], code="CLAIM_REVIEW_REASON_INVALID", detail=f"claims[{index}].reason must be a string", nonempty=False)

    finding_keys = {"id", "type", "claim_ids"}
    for index, item in enumerate(value["findings"], start=1):
        item = _exact_item(item, finding_keys, code="CLAIM_REVIEW_FINDING_SHAPE_INVALID", detail=f"findings[{index}] must contain exactly id, type and claim_ids")
        _string(item["id"], code="CLAIM_REVIEW_FINDING_ID_INVALID", detail=f"findings[{index}].id must be a non-empty string")
        if item["type"] not in {"fact", "bug", "risk", "recommendation"}:
            raise StructuredResponseError("CLAIM_REVIEW_FINDING_TYPE_INVALID", f"findings[{index}].type is invalid")
        _string_list(item["claim_ids"], code="CLAIM_REVIEW_FINDING_CLAIM_IDS_INVALID", detail=f"findings[{index}].claim_ids must be an array of non-empty Claim IDs")

    gap_keys = {"id", "type", "target_id", "evidence_ids", "reason"}
    for index, item in enumerate(value["semantic_gaps"], start=1):
        item = _exact_item(item, gap_keys, code="CLAIM_REVIEW_SEMANTIC_GAP_SHAPE_INVALID", detail=f"semantic_gaps[{index}] must contain exactly id, type, target_id, evidence_ids and reason")
        _string(item["id"], code="CLAIM_REVIEW_SEMANTIC_GAP_ID_INVALID", detail=f"semantic_gaps[{index}].id must be a non-empty string")
        if item["type"] not in {"material_omission", "conflicting_evidence", "scope_gap"}:
            raise StructuredResponseError("CLAIM_REVIEW_SEMANTIC_GAP_TYPE_INVALID", f"semantic_gaps[{index}].type is invalid")
        if item["target_id"] is not None:
            _string(item["target_id"], code="CLAIM_REVIEW_SEMANTIC_GAP_TARGET_INVALID", detail=f"semantic_gaps[{index}].target_id must be null or a non-empty string")
        _string_list(item["evidence_ids"], code="CLAIM_REVIEW_SEMANTIC_GAP_EVIDENCE_IDS_INVALID", detail=f"semantic_gaps[{index}].evidence_ids must be an array of non-empty Evidence IDs")
        _string(item["reason"], code="CLAIM_REVIEW_SEMANTIC_GAP_REASON_INVALID", detail=f"semantic_gaps[{index}].reason must be a non-empty string")
    return value


def parse_claim_repair_response(raw: Any) -> Dict[str, Any]:
    value = _object(raw)
    _exact_keys(value, required=("repairs",), allowed=("repairs",), profile="claim_repair")
    if not isinstance(value["repairs"], list):
        raise StructuredResponseError("CLAIM_REPAIR_REPAIRS_LIST_REQUIRED", "repairs must be an array")
    keys = {"claim_id", "target", "replacement", "evidence_ids"}
    for index, item in enumerate(value["repairs"], start=1):
        item = _exact_item(item, keys, code="CLAIM_REPAIR_ITEM_SHAPE_INVALID", detail=f"repairs[{index}] must contain exactly claim_id, target, replacement and evidence_ids")
        _string(item["claim_id"], code="CLAIM_REPAIR_CLAIM_ID_INVALID", detail=f"repairs[{index}].claim_id must be a non-empty string")
        _string(item["target"], code="CLAIM_REPAIR_TARGET_INVALID", detail=f"repairs[{index}].target must be a non-empty string")
        _string(item["replacement"], code="CLAIM_REPAIR_REPLACEMENT_INVALID", detail=f"repairs[{index}].replacement must be a string", nonempty=False)
        _string_list(item["evidence_ids"], code="CLAIM_REPAIR_EVIDENCE_IDS_INVALID", detail=f"repairs[{index}].evidence_ids must be an array of non-empty Evidence IDs")
    return value


def parse_profile_response(raw: Any, profile: str) -> Dict[str, Any]:
    if profile == "agent":
        return parse_agent_response(raw)
    if profile == "claim_verifier":
        return parse_claim_review_response(raw)
    if profile == "claim_repair":
        return parse_claim_repair_response(raw)
    raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
