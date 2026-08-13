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
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["id", "goal", "status", "grounding_ids", "reason"],
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
        "status": {"type": "string", "enum": ["open", "completed", "dropped"]},
        "result": {"type": "string", "maxLength": 1200},
    },
    "required": ["id", "parent_id", "description", "status", "result"],
    "additionalProperties": False,
}


_TOOL_CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "minLength": 1},
        "arguments": {"type": "object"},
    },
    "required": ["tool", "arguments"],
    "additionalProperties": False,
}

_AGENT_ACTION_SCHEMA = {
    "anyOf": [
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["tool_calls"]},
                "calls": {"type": "array", "minItems": 1, "maxItems": 4, "items": _TOOL_CALL_SCHEMA},
            },
            "required": ["kind", "calls"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["patches"]},
                "patches": {"type": "array", "minItems": 1, "items": _PATCH_SCHEMA},
            },
            "required": ["kind", "patches"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["needs_user"]},
                "question": {"type": "string", "minLength": 1},
                "missing_information": {"type": "string", "minLength": 1},
            },
            "required": ["kind", "question", "missing_information"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["final"]},
                "answer": {"type": "string", "minLength": 1},
                "limitations": {"type": "array", "items": {"type": "string"}},
                "grounding_ids": {"type": "array", "items": {"type": "string", "minLength": 1, "pattern": r"^mat-[0-9]+$"}},
            },
            "required": ["kind", "answer", "limitations", "grounding_ids"],
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
    },
    "required": ["action", "investigation_updates", "task_updates"],
    "additionalProperties": False,
}

_GROUNDING_REF_PATTERN = r"^(?:request|observation:mat-[0-9]+)$"

# Claim has only a protocol shape, not semantic quotas. Runtime validates the
# coordinates it understands; the fresh critic decides how many blockers are
# necessary. Physical output/context ceilings still apply at the LLM boundary.
_CLAIM_ISSUE_KINDS = ["unsupported", "contradicted", "scope", "omission", "inconsistent", "unsafe"]
_CLAIM_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "challenge"]},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": _CLAIM_ISSUE_KINDS},
                    "grounding_refs": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "pattern": _GROUNDING_REF_PATTERN},
                    },
                    "reason": {"type": "string", "minLength": 1},
                },
                "required": ["kind", "grounding_refs", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdict", "issues"],
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
    "agent": ("action", "investigation_updates", "task_updates"),
    "claim_verifier": ("verdict", "issues"),
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
            "Top-level JSON contract: return exactly {verdict,issues}. verdict=accept|challenge. "
            "issues is [] when accepted; otherwise include the smallest sufficient blocker set. "
            "Each issue is exactly {kind,grounding_refs,reason}; "
            "kind=unsupported|contradicted|scope|omission|inconsistent|unsafe. "
            "Grounding refs use request or observation:mat-* and may be empty for answer-internal defects. "
            "Never add alternate fields or prose."
        )
    return (
        "Top-level JSON contract: return exactly {action,investigation_updates,task_updates}. action is one discriminated object "
        "with exactly one kind: tool_calls={kind,calls}, patches={kind,patches}, "
        "needs_user={kind,question,missing_information}, or final={kind,answer,limitations,grounding_ids}. "
        "Investigation is only what you are trying to understand. Tasks are what you decided you need to do. "
        "Each task update is exactly {id,parent_id,description,status,result}, status=open|completed|dropped; "
        "closed tasks require a concise non-empty result. Never emit legacy nullable top-level action slots or combine action kinds."
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
    target_keys = {"id", "goal", "status", "grounding_ids", "reason"}
    for index, item in enumerate(investigation, start=1):
        item = _exact_item(item, target_keys, code="AGENT_INVESTIGATION_TARGET_SHAPE_INVALID", detail=f"investigation_updates[{index}] must contain exactly id, goal, status, grounding_ids and reason")
        _string(item["id"], code="AGENT_INVESTIGATION_TARGET_ID_INVALID", detail=f"investigation_updates[{index}].id must be non-empty")
        _string(item["goal"], code="AGENT_INVESTIGATION_TARGET_GOAL_INVALID", detail=f"investigation_updates[{index}].goal must be non-empty")
        if item["status"] not in {"open", "established", "dismissed"}:
            raise StructuredResponseError("AGENT_INVESTIGATION_TARGET_STATUS_INVALID", f"investigation_updates[{index}].status is invalid")
        grounding_ids = _string_list(item["grounding_ids"], code="AGENT_INVESTIGATION_TARGET_GROUNDING_INVALID", detail=f"investigation_updates[{index}].grounding_ids must be an array of canonical mat-* grounding IDs")
        invalid_grounding = next((ref for ref in grounding_ids if re.fullmatch(r"mat-[0-9]+", ref) is None), None)
        if invalid_grounding is not None:
            raise StructuredResponseError("AGENT_INVESTIGATION_TARGET_GROUNDING_INVALID", f"investigation_updates[{index}].grounding_ids contains a noncanonical grounding ID")
        _string(item["reason"], code="AGENT_INVESTIGATION_TARGET_REASON_INVALID", detail=f"investigation_updates[{index}].reason must be a string", nonempty=False)

    task_updates = value.get("task_updates")
    if not isinstance(task_updates, list):
        raise StructuredResponseError("AGENT_TASK_UPDATES_INVALID", "task_updates must be an array")
    task_keys = {"id", "parent_id", "description", "status", "result"}
    for index, item in enumerate(task_updates, start=1):
        item = _exact_item(
            item, task_keys,
            code="AGENT_TASK_SHAPE_INVALID",
            detail=f"task_updates[{index}] must contain exactly id, parent_id, description, status and result",
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
        status = item["status"]
        if status not in {"open", "completed", "dropped"}:
            raise StructuredResponseError("AGENT_TASK_STATUS_INVALID", f"task_updates[{index}].status is invalid")
        result = _string(item["result"], code="AGENT_TASK_RESULT_INVALID", detail=f"task_updates[{index}].result must be a string", nonempty=False)
        if len(result) > 1200:
            raise StructuredResponseError("AGENT_TASK_RESULT_INVALID", f"task_updates[{index}].result is too long")
        if status in {"completed", "dropped"} and not result.strip():
            raise StructuredResponseError("AGENT_TASK_CLOSED_RESULT_REQUIRED", f"task_updates[{index}].result is required when status is {status}")

    action = value.get("action")
    if not isinstance(action, dict):
        raise StructuredResponseError("AGENT_ACTION_INVALID", "action must be one discriminated object")
    kind = action.get("kind")
    if kind == "tool_calls":
        if set(action) != {"kind", "calls"}:
            raise StructuredResponseError("AGENT_TOOL_CALLS_SHAPE_INVALID", "tool_calls action must contain exactly kind and calls")
        calls = action.get("calls")
        if not isinstance(calls, list) or not calls:
            raise StructuredResponseError("AGENT_TOOL_CALLS_INVALID", "tool_calls.calls must be a non-empty array")
        if len(calls) > 4:
            raise StructuredResponseError("AGENT_TOOL_CALL_LIMIT_EXCEEDED", "tool_calls.calls may contain at most 4 calls per turn")
        normalized = []
        for index, item in enumerate(calls, start=1):
            if not isinstance(item, dict) or set(item) != {"tool", "arguments"}:
                raise StructuredResponseError("AGENT_TOOL_CALL_INVALID", f"action.calls[{index}] must contain exactly tool and arguments")
            tool = item.get("tool")
            arguments = item.get("arguments")
            if not isinstance(tool, str) or not tool.strip() or not isinstance(arguments, dict):
                raise StructuredResponseError("AGENT_TOOL_CALL_INVALID", f"action.calls[{index}] is invalid")
            normalized.append({"tool": tool.strip(), "arguments": arguments})
        normalized_action = {"kind": "tool_calls", "calls": normalized}
    elif kind == "patches":
        if set(action) != {"kind", "patches"}:
            raise StructuredResponseError("AGENT_PATCHES_SHAPE_INVALID", "patches action must contain exactly kind and patches")
        patches = action.get("patches")
        if not isinstance(patches, list) or not patches:
            raise StructuredResponseError("AGENT_PATCHES_INVALID", "action.patches must be a non-empty array")
        normalized_patches = []
        for index, item in enumerate(patches, start=1):
            if not isinstance(item, dict):
                raise StructuredResponseError("AGENT_PATCH_INVALID", f"action.patches[{index}] must be an object")
            operation = item.get("operation")
            if operation in {"replace", "create"}:
                keys = {"operation", "path", "content"}
            elif operation == "delete":
                keys = {"operation", "path"}
            elif operation == "update":
                keys = {"operation", "path", "line_start", "line_end", "new_code"}
            else:
                raise StructuredResponseError("AGENT_PATCH_OPERATION_INVALID", f"action.patches[{index}].operation is invalid")
            if set(item) != keys:
                raise StructuredResponseError("AGENT_PATCH_SHAPE_INVALID", f"action.patches[{index}] must contain exactly the canonical fields for {operation}")
            if not isinstance(item.get("path"), str) or not item["path"].strip():
                raise StructuredResponseError("AGENT_PATCH_PATH_INVALID", f"action.patches[{index}].path must be a non-empty string")
            if operation in {"replace", "create"} and not isinstance(item.get("content"), str):
                raise StructuredResponseError("AGENT_PATCH_CONTENT_INVALID", f"action.patches[{index}].content must be a string")
            if operation == "update":
                if not isinstance(item.get("line_start"), int) or isinstance(item.get("line_start"), bool) or item["line_start"] < 1:
                    raise StructuredResponseError("AGENT_PATCH_RANGE_INVALID", f"action.patches[{index}].line_start must be a positive integer")
                if not isinstance(item.get("line_end"), int) or isinstance(item.get("line_end"), bool) or item["line_end"] < item["line_start"]:
                    raise StructuredResponseError("AGENT_PATCH_RANGE_INVALID", f"action.patches[{index}].line_end must be >= line_start")
                if not isinstance(item.get("new_code"), str):
                    raise StructuredResponseError("AGENT_PATCH_CONTENT_INVALID", f"action.patches[{index}].new_code must be a string")
            normalized_patches.append(dict(item))
        normalized_action = {"kind": "patches", "patches": normalized_patches}
    elif kind == "needs_user":
        if set(action) != {"kind", "question", "missing_information"}:
            raise StructuredResponseError("AGENT_NEEDS_USER_INVALID", "needs_user action must contain exactly kind, question and missing_information")
        question = action.get("question")
        missing = action.get("missing_information")
        if not isinstance(question, str) or not question.strip() or not isinstance(missing, str) or not missing.strip():
            raise StructuredResponseError("AGENT_NEEDS_USER_INVALID", "needs_user question and missing_information must be non-empty strings")
        normalized_action = {"kind": "needs_user", "question": question.strip(), "missing_information": missing.strip()}
    elif kind == "final":
        final_keys = {"kind", "answer", "limitations", "grounding_ids"}
        action = _exact_item(action, final_keys, code="AGENT_FINAL_SHAPE_INVALID", detail="final action must contain exactly kind, answer, limitations and grounding_ids")
        _string(action["answer"], code="AGENT_FINAL_ANSWER_INVALID", detail="final.answer must be a non-empty string")
        if not isinstance(action["limitations"], list) or any(not isinstance(item, str) for item in action["limitations"]):
            raise StructuredResponseError("AGENT_FINAL_LIMITATIONS_INVALID", "final.limitations must be an array of strings")
        grounding_ids = _string_list(action["grounding_ids"], code="AGENT_FINAL_GROUNDING_INVALID", detail="final.grounding_ids must be an array of canonical mat-* grounding IDs")
        if any(re.fullmatch(r"mat-[0-9]+", ref) is None for ref in grounding_ids):
            raise StructuredResponseError("AGENT_FINAL_GROUNDING_INVALID", "final.grounding_ids contains a noncanonical grounding ID")
        normalized_action = dict(action)
    else:
        raise StructuredResponseError("AGENT_ACTION_KIND_INVALID", "action.kind must be tool_calls, patches, needs_user, or final")

    return {"action": normalized_action, "investigation_updates": value["investigation_updates"], "task_updates": value["task_updates"]}


def _claim_grounding_refs(value: Any, *, detail: str) -> None:
    _string_list(value, code="CLAIM_REVIEW_GROUNDING_REFS_INVALID", detail=detail)
    invalid = next((item for item in value if re.fullmatch(_GROUNDING_REF_PATTERN, item) is None), None)
    if invalid is not None:
        raise StructuredResponseError("CLAIM_REVIEW_GROUNDING_REF_FORMAT_INVALID", f"noncanonical grounding ref: {invalid}")


def parse_claim_review_response(raw: Any) -> Dict[str, Any]:
    value = _object(raw)
    top = set(mandatory_top_level_keys("claim_verifier"))
    _exact_keys(value, required=top, allowed=top, profile="claim_review")
    if value["verdict"] not in {"accept", "challenge"}:
        raise StructuredResponseError("CLAIM_REVIEW_VERDICT_INVALID", "verdict must be accept or challenge")
    if not isinstance(value["issues"], list):
        raise StructuredResponseError("CLAIM_REVIEW_ISSUES_LIST_REQUIRED", "issues must be an array")
    issue_keys = {"kind", "grounding_refs", "reason"}
    for index, item in enumerate(value["issues"], start=1):
        item = _exact_item(
            item, issue_keys,
            code="CLAIM_REVIEW_ISSUE_SHAPE_INVALID",
            detail=f"issues[{index}] must contain exactly the canonical issue fields",
        )
        if item["kind"] not in set(_CLAIM_ISSUE_KINDS):
            raise StructuredResponseError("CLAIM_REVIEW_ISSUE_KIND_INVALID", f"issues[{index}].kind is invalid")
        _claim_grounding_refs(item["grounding_refs"], detail=f"issues[{index}].grounding_refs must be a canonical-ref array")
        _string(item["reason"], code="CLAIM_REVIEW_REASON_INVALID", detail=f"issues[{index}].reason must be non-empty")
    if value["verdict"] == "accept" and value["issues"]:
        raise StructuredResponseError("CLAIM_REVIEW_ACCEPT_WITH_ISSUES", "accept requires issues=[]")
    if value["verdict"] == "challenge" and not value["issues"]:
        raise StructuredResponseError("CLAIM_REVIEW_CHALLENGE_REQUIRES_ISSUE", "challenge requires at least one issue")
    return value



def parse_profile_response(raw: Any, profile: str) -> Dict[str, Any]:
    if profile == "agent":
        return parse_agent_response(raw)
    if profile == "claim_verifier":
        return parse_claim_review_response(raw)
    raise StructuredResponseError("STRUCTURED_PROFILE_UNKNOWN", f"unknown structured profile: {profile}")
