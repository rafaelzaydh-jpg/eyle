from __future__ import annotations

import copy
import hmac
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from starlette.background import BackgroundTask

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("eyle-deepseek-adapter")


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on", "sim"}


def env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    value = int(os.getenv(name, str(default)))
    if minimum is not None and value < minimum:
        raise RuntimeError(f"{name} deve ser >= {minimum}.")
    if maximum is not None and value > maximum:
        raise RuntimeError(f"{name} deve ser <= {maximum}.")
    return value


@dataclass(frozen=True)
class Settings:
    upstream_base_url: str
    upstream_api_key: str
    default_model: str
    model_override: str | None
    default_thinking: bool
    structured_thinking: bool
    force_thinking: bool
    structured_repair_attempts: int
    host: str
    port: int
    timeout: float
    max_body: int
    default_max_output: int
    proxy_key: str | None


S = Settings(
    upstream_base_url=os.getenv("UPSTREAM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
    upstream_api_key=os.getenv("UPSTREAM_API_KEY", "").strip(),
    default_model=os.getenv("DEFAULT_MODEL", "deepseek-v4-flash").strip(),
    model_override=os.getenv("MODEL_OVERRIDE", "").strip() or None,
    default_thinking=env_bool("DEFAULT_ENABLE_THINKING", True),
    structured_thinking=env_bool("STRUCTURED_ENABLE_THINKING", False),
    force_thinking=env_bool("FORCE_ENABLE_THINKING", False),
    structured_repair_attempts=env_int("STRUCTURED_REPAIR_ATTEMPTS", 1, minimum=0, maximum=2),
    host=os.getenv("HOST", "127.0.0.1"),
    port=int(os.getenv("PORT", "8080")),
    timeout=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "600")),
    max_body=int(os.getenv("MAX_REQUEST_BYTES", str(10 * 1024 * 1024))),
    default_max_output=int(os.getenv("DEFAULT_MAX_OUTPUT_TOKENS", "4096")),
    proxy_key=os.getenv("PROXY_API_KEY", "").strip() or None,
)

ADAPTER_PROFILE = "deepseek-stable-json-local-schema-v2"


def check_config() -> None:
    if not S.upstream_base_url:
        raise RuntimeError("UPSTREAM_BASE_URL não configurada.")
    if not (S.model_override or S.default_model):
        raise RuntimeError("DEFAULT_MODEL/MODEL_OVERRIDE não configurado.")
    if not S.upstream_api_key:
        log.warning("UPSTREAM_API_KEY vazia; o upstream provavelmente recusará chamadas autenticadas.")


def client_auth(request: Request) -> None:
    if not S.proxy_key:
        return
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    supplied = bearer or request.headers.get("x-api-key", "").strip()
    if not supplied or not hmac.compare_digest(supplied, S.proxy_key):
        raise HTTPException(401, "Chave do proxy inválida.")


def model_for(payload: dict[str, Any]) -> str:
    return S.model_override or str(payload.get("model") or "").strip() or S.default_model


def schema_for(payload: dict[str, Any]) -> dict[str, Any] | None:
    fmt = payload.get("response_format")
    if not isinstance(fmt, dict):
        return None
    kind = fmt.get("type")
    if kind == "json_object":
        return {"type": "object"}
    if kind != "json_schema":
        return None
    block = fmt.get("json_schema")
    return block.get("schema") if isinstance(block, dict) and isinstance(block.get("schema"), dict) else None


def structured(payload: dict[str, Any]) -> bool:
    fmt = payload.get("response_format")
    if fmt is None:
        return False
    if not isinstance(fmt, dict) or fmt.get("type") not in {"json_object", "json_schema"}:
        raise HTTPException(400, "response_format inválido para o adaptador DeepSeek.")
    schema = schema_for(payload)
    if schema is None:
        raise HTTPException(400, "response_format estruturado sem schema/formato válido.")
    if fmt.get("type") == "json_schema":
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise HTTPException(400, f"JSON Schema inválido: {exc.message}") from exc
    return True


def max_output(payload: dict[str, Any]) -> int:
    for key in ("max_completion_tokens", "max_tokens"):
        if isinstance(payload.get(key), int) and payload[key] > 0:
            return payload[key]
    return S.default_max_output


def thinking_enabled(payload: dict[str, Any]) -> bool:
    if S.force_thinking:
        return True
    if isinstance(payload.get("enable_thinking"), bool):
        return payload["enable_thinking"]
    thinking = payload.get("thinking")
    if isinstance(thinking, dict):
        kind = str(thinking.get("type") or "").strip().lower()
        if kind == "enabled":
            return True
        if kind == "disabled":
            return False
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and isinstance(reasoning.get("enabled"), bool):
        return reasoning["enabled"]
    return S.structured_thinking if structured(payload) else S.default_thinking


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}:
                if isinstance(item.get("text"), str):
                    out.append(item["text"])
        return "\n".join(out)
    return ""


def _is_ecc_schema(schema: dict[str, Any]) -> bool:
    variants = schema.get("oneOf")
    if not isinstance(variants, list) or len(variants) != 3:
        return False
    seen: list[str] = []
    for variant in variants:
        if not isinstance(variant, dict):
            return False
        prop = ((variant.get("properties") or {}).get("type") or {})
        enum = prop.get("enum")
        if not isinstance(enum, list) or len(enum) != 1 or not isinstance(enum[0], str):
            return False
        seen.append(enum[0])
    return seen == ["explorar", "construir", "concluir"]


def _example_from_schema(node: Any, depth: int = 0) -> Any:
    if depth > 5 or not isinstance(node, dict):
        return None
    if "const" in node:
        return node["const"]
    enum = node.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    variants = node.get("oneOf") or node.get("anyOf")
    if isinstance(variants, list) and variants:
        return _example_from_schema(variants[0], depth + 1)
    typ = node.get("type")
    if typ == "object" or isinstance(node.get("properties"), dict):
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        required = set(node.get("required") or [])
        out: dict[str, Any] = {}
        for name, child in props.items():
            if name in required:
                out[name] = _example_from_schema(child, depth + 1)
        return out
    if typ == "array":
        return []
    if typ == "integer" or typ == "number":
        return node.get("minimum", 1)
    if typ == "boolean":
        return False
    return "value"


def _singleton_enum(node: Any) -> str | None:
    if not isinstance(node, dict):
        return None
    if "const" in node and isinstance(node.get("const"), str):
        return str(node["const"])
    enum = node.get("enum")
    if isinstance(enum, list) and len(enum) == 1 and isinstance(enum[0], str):
        return enum[0]
    return None


def _variant_for(schema: dict[str, Any], discriminator: str, value: str) -> dict[str, Any] | None:
    variants = schema.get("oneOf")
    if not isinstance(variants, list):
        return None
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        prop = ((variant.get("properties") or {}).get(discriminator) or {})
        if _singleton_enum(prop) == value:
            return variant
    return None


def _ecc_memory_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    branch = _variant_for(schema, "type", "explorar")
    memory = ((branch or {}).get("properties") or {}).get("memory")
    return memory if isinstance(memory, dict) else None


def _ecc_objective_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    branch = _variant_for(schema, "type", "explorar")
    objective = ((branch or {}).get("properties") or {}).get("objective")
    return objective if isinstance(objective, dict) else None


def _objective_state_schema(objective_schema: dict[str, Any]) -> dict[str, Any] | None:
    state = ((objective_schema.get("properties") or {}).get("state") or {})
    variants = state.get("oneOf") if isinstance(state, dict) else None
    if not isinstance(variants, list):
        return None
    for variant in variants:
        if isinstance(variant, dict) and (variant.get("type") == "object" or isinstance(variant.get("properties"), dict)):
            return variant
    return None


def _field_hint(name: str, node: dict[str, Any], required: bool) -> str:
    enum = node.get("enum") if isinstance(node, dict) else None
    typ = str(node.get("type") or "value") if isinstance(node, dict) else "value"
    if isinstance(enum, list) and enum:
        shape = "|".join(str(v) for v in enum)
    elif typ == "array":
        shape = "array"
    elif typ == "object":
        shape = "object"
    elif typ == "integer":
        shape = "int"
    else:
        shape = typ
    return f"{name}:{shape}{'' if required else '?'}"


def _operation_grammar(memory_schema: dict[str, Any]) -> tuple[list[str], list[str]]:
    operations = ((memory_schema.get("properties") or {}).get("operations") or {})
    item_schema = operations.get("items") if isinstance(operations, dict) else None
    variants = item_schema.get("oneOf") if isinstance(item_schema, dict) else None
    lines: list[str] = []
    examples: list[str] = []
    if not isinstance(variants, list):
        return lines, examples
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        props = variant.get("properties") if isinstance(variant.get("properties"), dict) else {}
        op = _singleton_enum(props.get("op"))
        if not op:
            continue
        required = set(variant.get("required") or [])
        fields = [_field_hint(name, child, name in required) for name, child in props.items()]
        lines.append(f"{op}={{" + ",".join(fields) + "}")
        sample: dict[str, Any] = {"op": op}
        if op == "remember":
            if "key" in props: sample["key"] = "session"
            sample.update({"scope": "world", "kind": "architecture_component", "content": "AgentSession stores active task state."})
            if "tags" in props: sample["tags"] = ["ecc", "session"]
            if "supports" in props: sample["supports"] = [{"kind": "material", "material_id": "mat-1"}]
        elif op == "revise":
            sample.update({"id": "mem-abc", "expected_revision": 1, "content": "Updated understanding."})
        elif op == "relate":
            sample.update({"source": "@session", "relation": "part_of", "target": "mem-ecc"})
        elif op == "archive":
            sample.update({"id": "mem-abc", "expected_revision": 1})
        elif op == "supersede":
            sample.update({"id": "mem-old", "expected_revision": 1, "replacement": "@new"})
        elif op == "retire_relation":
            sample.update({"id": "rel-abc", "expected_revision": 1})
        examples.append(json.dumps(sample, ensure_ascii=False, separators=(",", ":")))
    return lines, examples


def _support_grammar(memory_schema: dict[str, Any]) -> list[str]:
    operations = ((memory_schema.get("properties") or {}).get("operations") or {})
    item_schema = operations.get("items") if isinstance(operations, dict) else None
    variants = item_schema.get("oneOf") if isinstance(item_schema, dict) else None
    support_schema = None
    if isinstance(variants, list):
        for variant in variants:
            props = variant.get("properties") if isinstance(variant, dict) and isinstance(variant.get("properties"), dict) else {}
            supports = props.get("supports")
            if isinstance(supports, dict) and isinstance(supports.get("items"), dict):
                support_schema = supports["items"]
                break
    out: list[str] = []
    for variant in (support_schema or {}).get("oneOf") or []:
        props = variant.get("properties") if isinstance(variant, dict) and isinstance(variant.get("properties"), dict) else {}
        kind = _singleton_enum(props.get("kind"))
        if not kind:
            continue
        required = set(variant.get("required") or [])
        fields = [_field_hint(name, child, name in required) for name, child in props.items()]
        out.append(f"support.{kind}={{" + ",".join(fields) + "}")
    return out


def _objective_grammar(objective_schema: dict[str, Any]) -> str:
    state_schema = _objective_state_schema(objective_schema) or {}
    props = state_schema.get("properties") if isinstance(state_schema.get("properties"), dict) else {}
    required = set(state_schema.get("required") or [])
    fields = [_field_hint(name, child, name in required) for name, child in props.items()]
    child_schema = ((props.get("children") or {}).get("items") or {}) if isinstance(props.get("children"), dict) else {}
    cprops = child_schema.get("properties") if isinstance(child_schema.get("properties"), dict) else {}
    crequired = set(child_schema.get("required") or [])
    child_fields = [_field_hint(name, child, name in crequired) for name, child in cprops.items()]
    return "objective.state={" + ",".join(fields) + "}; objective.child={" + ",".join(child_fields) + "}"


def _compact_schema_rules(schema: dict[str, Any]) -> str:
    if _is_ecc_schema(schema):
        memory_schema = _ecc_memory_schema(schema) or {}
        objective_schema = _ecc_objective_schema(schema) or {}
        op_lines, op_examples = _operation_grammar(memory_schema)
        support_lines = _support_grammar(memory_schema)
        grammar = "; ".join([*op_lines, *support_lines])
        objective_grammar = _objective_grammar(objective_schema)
        examples = " | ".join(op_examples)
        return (
            "Return exactly one JSON object for the Eyle ECC decision. 'type' is the only family authority. "
            "For explorar/construir use a SHORT operation name without family prefix. "
            "Every ECC object MUST include objective={disposition,state}; Objective is transient semantic state, NOT a fourth action or plan. "
            "Use objective.disposition=unchanged or cleared only with state=null; updated requires the full objective.state object. "
            "Objective state describes what is being pursued, not execution steps. Status strings are semantic labels, not Runtime control codes. "
            + objective_grammar + ". "
            "Every ECC object MUST include memory={focus,disposition,operations}; Memory is internal cognition, NOT a fourth action. "
            "Use disposition=unchanged only with operations=[]; use disposition=updated only with 1+ valid graph operations. "
            "Memory refs are mem-* or @alias. A remember key creates @key for later operations in the SAME memory transaction. "
            "Do not invent memory merely to satisfy the field. Supports ground semantic memory: material points to observed mat-N and may include opaque selector; request grounds in the current request; memory points to an existing mem-* or @alias. "
            "ECC examples: "
            "{\"type\":\"explorar\",\"operation\":\"search\",\"arguments\":{\"source\":\"eyle\",\"query\":\"AgentSession\"},\"objective\":{\"disposition\":\"unchanged\",\"state\":null},\"memory\":{\"focus\":[],\"disposition\":\"unchanged\",\"operations\":[]}} | "
            "{\"type\":\"concluir\",\"response\":\"...\",\"objective\":{\"disposition\":\"updated\",\"state\":{\"summary\":\"Answer the compound request\",\"status\":\"active\",\"children\":[{\"key\":\"part1\",\"description\":\"First subobjective\",\"status\":\"resolved\",\"outcome\":\"done\"}],\"constraints\":[]}},\"memory\":{\"focus\":[\"mem-ecc\"],\"disposition\":\"updated\",\"operations\":[{\"op\":\"remember\",\"key\":\"session\",\"scope\":\"world\",\"kind\":\"architecture_component\",\"content\":\"AgentSession stores active task state.\",\"supports\":[{\"kind\":\"material\",\"material_id\":\"mat-1\"}]}]}}. "
            "Memory operation grammar: " + grammar + ". Examples: " + examples + ". "
            "Return JSON only; no markdown or commentary."
        )
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    example = json.dumps(_example_from_schema(schema), ensure_ascii=False, separators=(",", ":"))
    return (
        "Return only valid JSON satisfying this JSON Schema. No markdown or commentary. "
        f"Example JSON shape: {example}. Canonical JSON Schema: {schema_text}"
    )

def _inject_deepseek_json_instruction(body: dict[str, Any], schema: dict[str, Any]) -> None:
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(400, "messages inválido.")
    instruction = _compact_schema_rules(schema)
    body["messages"] = [{"role": "system", "content": instruction}, *messages]


def prepare_upstream(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], dict[str, Any] | None]:
    if not isinstance(payload.get("messages"), list):
        raise HTTPException(400, "messages inválido.")
    is_structured = structured(payload)
    body = copy.deepcopy(payload)
    body["model"] = model_for(payload)
    schema = schema_for(payload) if is_structured else None

    # DeepSeek stable supports JSON Output via json_object. OpenAI json_schema is never forwarded.
    if schema is not None:
        _inject_deepseek_json_instruction(body, schema)
        body["response_format"] = {"type": "json_object"}

    enabled = thinking_enabled(payload)
    for key in ("enable_thinking", "reasoning"):
        body.pop(key, None)
    body["thinking"] = {"type": "enabled" if enabled else "disabled"}
    if enabled:
        effort = str(payload.get("reasoning_effort") or "high").strip().lower()
        body["reasoning_effort"] = effort if effort in {"low", "high", "max"} else "high"
        for key in ("temperature", "top_p", "presence_penalty", "frequency_penalty"):
            body.pop(key, None)
    else:
        body.pop("reasoning_effort", None)

    # OpenAI aliases that DeepSeek stable does not need.
    if "max_completion_tokens" in body:
        if "max_tokens" not in body:
            body["max_tokens"] = body["max_completion_tokens"]
        body.pop("max_completion_tokens", None)

    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if S.upstream_api_key:
        headers["Authorization"] = f"Bearer {S.upstream_api_key}"
    return body, headers, schema


@dataclass
class UsageAccumulator:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    cache_miss_tokens: int = 0
    reasoning_tokens: int = 0

    def add(self, data: dict[str, Any]) -> None:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return
        prompt = max(0, int(usage.get("prompt_tokens") or 0))
        completion = max(0, int(usage.get("completion_tokens") or 0))
        hit = usage.get("prompt_cache_hit_tokens")
        miss = usage.get("prompt_cache_miss_tokens")
        details = usage.get("prompt_tokens_details")
        detail_hit = details.get("cached_tokens") if isinstance(details, dict) else None
        hit_values = [int(x) for x in (hit, detail_hit) if isinstance(x, (int, float)) and int(x) >= 0]
        if hit_values:
            cached = min(prompt, max(hit_values))
            cache_miss = max(0, prompt - cached)
        elif isinstance(miss, (int, float)) and int(miss) >= 0:
            cache_miss = min(prompt, int(miss))
            cached = max(0, prompt - cache_miss)
        else:
            cached = 0
            cache_miss = prompt
        cdetails = usage.get("completion_tokens_details")
        reasoning = int(cdetails.get("reasoning_tokens") or 0) if isinstance(cdetails, dict) else 0

        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cached_prompt_tokens += cached
        self.cache_miss_tokens += cache_miss
        self.reasoning_tokens += max(0, reasoning)

    def apply(self, data: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(data)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        usage["prompt_tokens"] = self.prompt_tokens
        usage["completion_tokens"] = self.completion_tokens
        usage["total_tokens"] = self.prompt_tokens + self.completion_tokens
        usage["prompt_cache_hit_tokens"] = min(self.prompt_tokens, self.cached_prompt_tokens)
        usage["prompt_cache_miss_tokens"] = min(self.prompt_tokens, self.cache_miss_tokens)
        if self.reasoning_tokens:
            cdetails = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
            cdetails["reasoning_tokens"] = self.reasoning_tokens
            usage["completion_tokens_details"] = cdetails
        result["usage"] = usage
        return result

    def as_dict(self) -> dict[str, int]:
        out = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "prompt_cache_hit_tokens": min(self.prompt_tokens, self.cached_prompt_tokens),
            "prompt_cache_miss_tokens": min(self.prompt_tokens, self.cache_miss_tokens),
        }
        if self.reasoning_tokens:
            out["reasoning_tokens"] = self.reasoning_tokens
        return out


def _assistant_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    return text_of(message.get("content")) if isinstance(message, dict) else ""


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)


def parse_json_representation(text: str) -> Any:
    candidate = text.strip()
    match = _FENCE_RE.match(candidate)
    if match:
        candidate = match.group(1).strip()
    value = json.loads(candidate)
    if isinstance(value, str):
        nested = value.strip()
        if nested.startswith(("{", "[")):
            value = json.loads(nested)
    return value


def _json_path(error: ValidationError, prefix: str = "$") -> str:
    path = prefix
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    if error.validator == "required":
        match = re.search(r"'([^']+)' is a required property", error.message)
        if match:
            path += f".{match.group(1)}"
    return path


def _concise_errors(instance: Any, schema: dict[str, Any], *, prefix: str, limit: int = 8) -> list[str]:
    errors = sorted(Draft202012Validator(schema).iter_errors(instance), key=lambda e: (list(e.absolute_path), e.message))
    out: list[str] = []
    for error in errors:
        if error.validator in {"oneOf", "anyOf"} and error.context:
            continue
        msg = error.message.replace("\n", " ")
        item = f"{_json_path(error, prefix)}: {msg[:260]}"
        if item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _diagnose_support(value: Any, schema: dict[str, Any], *, prefix: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{prefix}: support must be an object"]
    kind = str(value.get("kind") or "")
    branch = _variant_for(schema, "kind", kind)
    if branch is None:
        allowed = []
        for candidate in schema.get("oneOf") or []:
            prop = ((candidate.get("properties") or {}).get("kind") or {}) if isinstance(candidate, dict) else {}
            found = _singleton_enum(prop)
            if found: allowed.append(found)
        return [f"{prefix}.kind: expected one of {allowed}, got {kind!r}"]
    return _concise_errors(value, branch, prefix=prefix)


def _diagnose_ecc_contract(value: Any, schema: dict[str, Any]) -> list[str]:
    if not isinstance(value, dict):
        return ["$: top-level ECC value must be an object"]
    kind = str(value.get("type") or "")
    branch = _variant_for(schema, "type", kind)
    if branch is None:
        return [f"$.type: expected explorar, construir or concluir; got {kind!r}"]

    errors: list[str] = []
    props = branch.get("properties") if isinstance(branch.get("properties"), dict) else {}
    required = set(branch.get("required") or [])
    for name in sorted(required - set(value)):
        errors.append(f"$.{name}: required property missing")
    if branch.get("additionalProperties") is False:
        for name in sorted(set(value) - set(props)):
            errors.append(f"$.{name}: additional property is not allowed")
    for name, child in props.items():
        if name in {"memory", "objective"} or name not in value:
            continue
        errors.extend(_concise_errors(value[name], child, prefix=f"$.{name}", limit=4))

    objective_schema = props.get("objective") if isinstance(props.get("objective"), dict) else None
    objective = value.get("objective")
    if objective_schema is not None:
        if not isinstance(objective, dict):
            errors.append("$.objective: required object missing or invalid")
        else:
            oprops = objective_schema.get("properties") if isinstance(objective_schema.get("properties"), dict) else {}
            orequired = set(objective_schema.get("required") or [])
            for name in sorted(orequired - set(objective)):
                errors.append(f"$.objective.{name}: required property missing")
            if objective_schema.get("additionalProperties") is False:
                for name in sorted(set(objective) - set(oprops)):
                    errors.append(f"$.objective.{name}: additional property is not allowed")
            disposition = objective.get("disposition")
            if "disposition" in objective:
                errors.extend(_concise_errors(disposition, oprops.get("disposition") or {}, prefix="$.objective.disposition", limit=2))
            state_value = objective.get("state")
            if disposition in {"unchanged", "cleared"} and state_value is not None:
                errors.append(f"$.objective.state: disposition={disposition} requires null state")
            elif disposition == "updated":
                state_schema = _objective_state_schema(objective_schema)
                if not isinstance(state_value, dict):
                    errors.append("$.objective.state: disposition=updated requires an objective state object")
                elif state_schema is not None:
                    errors.extend(_concise_errors(state_value, state_schema, prefix="$.objective.state", limit=5))

    memory_schema = props.get("memory") if isinstance(props.get("memory"), dict) else None
    memory = value.get("memory")
    if memory_schema is None:
        return errors[:8]
    if not isinstance(memory, dict):
        return (errors + ["$.memory: required object missing or invalid"])[:8]
    mprops = memory_schema.get("properties") if isinstance(memory_schema.get("properties"), dict) else {}
    mrequired = set(memory_schema.get("required") or [])
    for name in sorted(mrequired - set(memory)):
        errors.append(f"$.memory.{name}: required property missing")
    if memory_schema.get("additionalProperties") is False:
        for name in sorted(set(memory) - set(mprops)):
            errors.append(f"$.memory.{name}: additional property is not allowed")
    if "focus" in memory:
        errors.extend(_concise_errors(memory.get("focus"), mprops.get("focus") or {}, prefix="$.memory.focus", limit=3))
    disposition = memory.get("disposition")
    if "disposition" in memory:
        errors.extend(_concise_errors(disposition, mprops.get("disposition") or {}, prefix="$.memory.disposition", limit=3))
    operations = memory.get("operations")
    if not isinstance(operations, list):
        errors.append("$.memory.operations: must be an array")
        return errors[:8]
    if disposition == "updated" and not operations:
        errors.append("$.memory.operations: disposition=updated requires at least one operation")
    if disposition == "unchanged" and operations:
        errors.append("$.memory.operations: disposition=unchanged requires an empty operations array")
    op_container = mprops.get("operations") if isinstance(mprops.get("operations"), dict) else {}
    errors.extend(_concise_errors(operations, {k:v for k,v in op_container.items() if k != "items"}, prefix="$.memory.operations", limit=2))
    item_schema = op_container.get("items") if isinstance(op_container.get("items"), dict) else {}
    for index, operation in enumerate(operations):
        prefix = f"$.memory.operations[{index}]"
        if not isinstance(operation, dict):
            errors.append(f"{prefix}: operation must be an object")
            continue
        op = str(operation.get("op") or "")
        op_branch = _variant_for(item_schema, "op", op)
        if op_branch is None:
            allowed = []
            for candidate in item_schema.get("oneOf") or []:
                prop = ((candidate.get("properties") or {}).get("op") or {}) if isinstance(candidate, dict) else {}
                found = _singleton_enum(prop)
                if found: allowed.append(found)
            errors.append(f"{prefix}.op: expected one of {allowed}, got {op!r}")
            continue
        # Validate the exact operation branch while treating supports separately,
        # avoiding the unhelpful nested oneOf umbrella error.
        branch_copy = copy.deepcopy(op_branch)
        bprops = branch_copy.get("properties") if isinstance(branch_copy.get("properties"), dict) else {}
        support_schema = None
        if isinstance(bprops.get("supports"), dict):
            support_schema = bprops["supports"].get("items") if isinstance(bprops["supports"].get("items"), dict) else None
            bprops["supports"]["items"] = {"type": "object"}
        errors.extend(_concise_errors(operation, branch_copy, prefix=prefix, limit=6))
        if support_schema is not None and isinstance(operation.get("supports"), list):
            for sidx, support in enumerate(operation["supports"]):
                errors.extend(_diagnose_support(support, support_schema, prefix=f"{prefix}.supports[{sidx}]"))
        if len(errors) >= 8:
            break
    return errors[:8]


def _leaf_validation_errors(errors: list[ValidationError], *, limit: int = 8) -> list[str]:
    leaves: list[ValidationError] = []
    stack = list(errors)
    while stack:
        error = stack.pop(0)
        if error.context:
            stack[0:0] = list(error.context)
        else:
            leaves.append(error)
    leaves.sort(key=lambda e: (-len(list(e.absolute_path)), list(e.absolute_path), e.message))
    out: list[str] = []
    for error in leaves:
        msg = error.message.replace("\n", " ")
        item = f"{_json_path(error)}: {msg[:260]}"
        if item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def validate_structured_response(data: dict[str, Any], schema: dict[str, Any]) -> tuple[Any | None, list[str]]:
    text = _assistant_content(data)
    if not text.strip():
        return None, ["$: resposta estruturada sem conteúdo"]
    try:
        value = parse_json_representation(text)
    except Exception as exc:
        return None, [f"$: JSON inválido ({type(exc).__name__})"]
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: (list(e.absolute_path), e.message))
    if not errors:
        return value, []
    if _is_ecc_schema(schema):
        diagnosed = _diagnose_ecc_contract(value, schema)
        if diagnosed:
            return value, diagnosed
    return value, _leaf_validation_errors(errors)

def canonicalize_content(data: dict[str, Any], value: Any) -> dict[str, Any]:
    result = copy.deepcopy(data)
    choices = result.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if not isinstance(message, dict):
            message = {"role": "assistant"}
            choices[0]["message"] = message
        message["content"] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return result


def build_repair_payload(original: dict[str, Any], previous_content: str, errors: list[str]) -> dict[str, Any]:
    repaired = copy.deepcopy(original)
    repaired["stream"] = False
    messages = repaired.get("messages")
    if not isinstance(messages, list):
        return repaired
    err = "\n".join(f"- {x}" for x in errors[:8])
    instruction = (
        "Repair the previous JSON object. Preserve its intended ECC decision, objective semantics and memory semantics, but satisfy the exact contract. "
        "Use the objective and memory grammars from the system instruction; do not erase a genuine objective or memory update merely to make validation pass. "
        "Return JSON only, with no markdown or explanation.\nValidation errors:\n" + err
    )
    repaired["messages"] = [
        *messages,
        {"role": "assistant", "content": previous_content[:12000]},
        {"role": "user", "content": instruction},
    ]
    return repaired


@dataclass(frozen=True)
class AttemptResult:
    data: dict[str, Any] | None
    status_code: int
    media_type: str
    raw: bytes


async def call_once(client: httpx.AsyncClient, payload: dict[str, Any], request_id: str, attempt_no: int) -> AttemptResult:
    body, headers, _ = prepare_upstream(payload)
    url = f"{S.upstream_base_url}/chat/completions"
    log.info(
        "request=%s upstream_attempt=%s start model=%s structured=%s chars=%s",
        request_id, attempt_no, body.get("model"), bool(schema_for(payload)), len(json.dumps(body, ensure_ascii=False)),
    )
    response = await client.post(url, headers=headers, json=body)
    media = response.headers.get("content-type", "application/json")
    if response.status_code >= 400:
        log.warning("request=%s upstream_attempt=%s http=%s", request_id, attempt_no, response.status_code)
        return AttemptResult(None, response.status_code, media, response.content)
    try:
        data = response.json()
    except Exception:
        return AttemptResult(None, 502, media, response.content)
    if not isinstance(data, dict):
        return AttemptResult(None, 502, media, response.content)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    log.info(
        "request=%s upstream_attempt=%s ok prompt=%s completion=%s cache_hit=%s",
        request_id, attempt_no, usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("prompt_cache_hit_tokens"),
    )
    return AttemptResult(data, response.status_code, media, response.content)


def _adapter_headers(*, usage: UsageAccumulator, attempts: int, repairs: int, enforcement: str, usage_unknown: bool = False) -> dict[str, str]:
    return {
        "X-Eyle-Adapter-Profile": ADAPTER_PROFILE,
        "X-Eyle-Structured-Backend": "deepseek_json_object_local_schema",
        "X-Eyle-Schema-Enforcement": enforcement,
        "X-Eyle-Structured-Repairs": str(repairs),
        "X-Eyle-Upstream-Attempts": str(attempts),
        "X-Eyle-Usage-Prompt-Tokens": str(usage.prompt_tokens),
        "X-Eyle-Usage-Completion-Tokens": str(usage.completion_tokens),
        "X-Eyle-Usage-Cached-Prompt-Tokens": str(min(usage.prompt_tokens, usage.cached_prompt_tokens)),
        "X-Eyle-Usage-Cache-Miss-Tokens": str(min(usage.prompt_tokens, usage.cache_miss_tokens)),
        "X-Eyle-Upstream-Usage-Unknown": "1" if usage_unknown else "0",
    }


async def execute_structured(client: httpx.AsyncClient, incoming: dict[str, Any], request_id: str) -> Response:
    schema = schema_for(incoming)
    if schema is None:
        raise HTTPException(400, "Structured request sem schema.")
    usage = UsageAccumulator()
    attempts = 0
    repairs = 0
    last_errors: list[str] = []
    payload = copy.deepcopy(incoming)

    for repair_index in range(S.structured_repair_attempts + 1):
        attempts += 1
        try:
            result = await call_once(client, payload, request_id, attempts)
        except httpx.TimeoutException:
            headers = _adapter_headers(
                usage=usage, attempts=attempts, repairs=repairs, enforcement="adapter_timeout", usage_unknown=True
            )
            log.warning("request=%s upstream_timeout attempt=%s billing_may_have_occurred=true", request_id, attempts)
            return JSONResponse(
                status_code=504,
                headers=headers,
                content={
                    "error": {
                        "type": "upstream_timeout",
                        "message": "Timeout no upstream DeepSeek; a tentativa pode ter sido processada/cobrada sem usage retornado.",
                        "billing_may_have_occurred": True,
                        "provider_usage_unknown": True,
                        "upstream_attempts": attempts,
                    },
                    "usage": usage.as_dict(),
                },
            )
        except httpx.HTTPError as exc:
            headers = _adapter_headers(usage=usage, attempts=attempts, repairs=repairs, enforcement="adapter_transport")
            log.exception("request=%s upstream_transport_error attempt=%s error=%s", request_id, attempts, exc)
            return JSONResponse(
                status_code=502,
                headers=headers,
                content={"error": {"type": "upstream_connection_error", "message": "Falha ao conectar ao DeepSeek."}, "usage": usage.as_dict()},
            )

        if result.data is None:
            headers = _adapter_headers(usage=usage, attempts=attempts, repairs=repairs, enforcement="provider_http")
            return Response(content=result.raw, status_code=result.status_code, media_type=result.media_type, headers=headers)

        usage.add(result.data)
        value, errors = validate_structured_response(result.data, schema)
        if not errors:
            canonical = canonicalize_content(result.data, value)
            aggregated = usage.apply(canonical)
            headers = _adapter_headers(usage=usage, attempts=attempts, repairs=repairs, enforcement="adapter")
            return JSONResponse(aggregated, headers=headers)

        last_errors = errors
        if repair_index >= S.structured_repair_attempts:
            break
        repairs += 1
        payload = build_repair_payload(incoming, _assistant_content(result.data), errors)

    headers = _adapter_headers(usage=usage, attempts=attempts, repairs=repairs, enforcement="adapter_failed")
    log.warning("request=%s structured_contract_unsatisfied attempts=%s repairs=%s errors=%s", request_id, attempts, repairs, last_errors)
    return JSONResponse(
        status_code=502,
        headers=headers,
        content={
            "error": {
                "type": "structured_contract_unsatisfied",
                "message": "A DeepSeek não satisfez o contrato estruturado após o repair permitido.",
                "validation_errors": last_errors[:8],
                "repairs": repairs,
                "upstream_attempts": attempts,
            },
            "usage": usage.as_dict(),
        },
    )


async def read_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > S.max_body:
        raise HTTPException(413, "Requisição grande demais.")
    try:
        data = json.loads(raw)
    except Exception as exc:
        raise HTTPException(400, "JSON inválido.") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "O corpo precisa ser um objeto JSON.")
    return data


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_config()
    timeout = httpx.Timeout(connect=20, read=S.timeout, write=60, pool=20)
    app.state.http = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    log.info(
        "Eyle DeepSeek Adapter -> %s | model=%s | profile=%s | repairs=%s",
        S.upstream_base_url, S.model_override or S.default_model, ADAPTER_PROFILE, S.structured_repair_attempts,
    )
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(title="Eyle DeepSeek Adapter", version="2.5.2", lifespan=lifespan)


@app.get("/")
@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "provider": "deepseek",
        "upstream": S.upstream_base_url,
        "model": S.model_override or S.default_model,
        "structured_backend": "json_object + local Draft 2020-12 validation",
        "structured_repair_attempts": S.structured_repair_attempts,
        "adapter_profile": ADAPTER_PROFILE,
        "openai_base_url": f"http://{S.host}:{S.port}/v1",
    }


@app.get("/v1/models")
async def models(request: Request) -> dict[str, Any]:
    client_auth(request)
    return {"object": "list", "data": [{"id": S.model_override or S.default_model, "object": "model", "created": 0, "owned_by": "deepseek"}]}


@app.post("/v1/chat/completions")
@app.post("/chat/completions", include_in_schema=False)
async def chat(request: Request) -> Response:
    client_auth(request)
    incoming = await read_body(request)
    request_id = str(uuid.uuid4())
    is_structured = structured(incoming)
    stream = bool(incoming.get("stream"))
    client: httpx.AsyncClient = request.app.state.http

    if is_structured and stream:
        raise HTTPException(400, "Validação estruturada requer stream=false.")

    if is_structured:
        return await execute_structured(client, incoming, request_id)

    body, headers, _ = prepare_upstream(incoming)
    url = f"{S.upstream_base_url}/chat/completions"
    try:
        if stream:
            req = client.build_request("POST", url, headers=headers, json=body)
            upstream = await client.send(req, stream=True)
            if upstream.status_code >= 400:
                content = await upstream.aread()
                status = upstream.status_code
                media = upstream.headers.get("content-type", "application/json")
                await upstream.aclose()
                return Response(content=content, status_code=status, media_type=media)
            return StreamingResponse(
                upstream.aiter_raw(),
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "text/event-stream"),
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                background=BackgroundTask(upstream.aclose),
            )
        result = await call_once(client, incoming, request_id, 1)
        if result.data is None:
            return Response(content=result.raw, status_code=result.status_code, media_type=result.media_type)
        return JSONResponse(result.data)
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            headers={"X-Eyle-Upstream-Usage-Unknown": "1"},
            content={
                "error": {
                    "type": "upstream_timeout",
                    "message": "Timeout no upstream DeepSeek.",
                    "billing_may_have_occurred": True,
                    "provider_usage_unknown": True,
                }
            },
        )
    except httpx.HTTPError as exc:
        log.exception("request=%s erro HTTP: %s", request_id, exc)
        return JSONResponse(status_code=502, content={"error": {"type": "upstream_connection_error", "message": "Falha ao conectar ao DeepSeek."}})


if __name__ == "__main__":
    uvicorn.run("server:app", host=S.host, port=S.port, reload=False, log_level=os.getenv("LOG_LEVEL", "info").lower())
