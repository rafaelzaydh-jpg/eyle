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
from urllib.parse import quote

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
log = logging.getLogger("eyle-llm-adapter")

STRUCTURED_BACKENDS = {"native_schema", "native_tool", "native_json", "text"}
THINKING_STYLES = {"auto", "qwen", "deepseek", "openrouter", "anthropic", "none"}
PROTOCOLS = {"openai", "anthropic", "gemini"}


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
class Endpoint:
    protocol: str
    base_url: str
    api_key: str
    model: str
    model_override: str | None
    provider_profile: str
    structured_backend: str
    thinking_style: str


@dataclass(frozen=True)
class Settings:
    primary: Endpoint
    fallback: Endpoint | None

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


def _endpoint_from_env(prefix: str, *, primary: bool) -> Endpoint | None:
    p = f"{prefix}_" if prefix else ""

    if primary:
        protocol = os.getenv(f"{p}LLM_PROTOCOL", "openai").strip().lower()
        base_url = os.getenv(
            f"{p}UPSTREAM_BASE_URL",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        ).rstrip("/")
        api_key = os.getenv(f"{p}UPSTREAM_API_KEY", "").strip()
        model = os.getenv(f"{p}DEFAULT_MODEL", "qwen3.8-max").strip()
    else:
        base_url = os.getenv(f"{p}UPSTREAM_BASE_URL", "").strip().rstrip("/")
        model = os.getenv(f"{p}DEFAULT_MODEL", "").strip()
        if not base_url or not model:
            return None
        protocol = os.getenv(f"{p}LLM_PROTOCOL", "openai").strip().lower()
        api_key = os.getenv(f"{p}UPSTREAM_API_KEY", "").strip()

    return Endpoint(
        protocol=protocol,
        base_url=base_url,
        api_key=api_key,
        model=model,
        model_override=os.getenv(f"{p}MODEL_OVERRIDE", "").strip() or None,
        provider_profile=os.getenv(f"{p}PROVIDER_PROFILE", "auto").strip().lower(),
        structured_backend=os.getenv(f"{p}STRUCTURED_BACKEND", "auto").strip().lower(),
        thinking_style=os.getenv(f"{p}THINKING_STYLE", "auto").strip().lower(),
    )


S = Settings(
    primary=_endpoint_from_env("", primary=True),  # type: ignore[arg-type]
    fallback=_endpoint_from_env("FALLBACK", primary=False),
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


def infer_provider(endpoint: Endpoint) -> str:
    if endpoint.provider_profile != "auto":
        return endpoint.provider_profile
    if endpoint.protocol == "anthropic":
        return "anthropic"
    if endpoint.protocol == "gemini":
        return "gemini"

    url = endpoint.base_url.lower()
    if "api.deepseek.com" in url:
        return "deepseek"
    if "dashscope" in url or "aliyuncs.com" in url:
        return "qwen"
    if "openrouter.ai" in url:
        return "openrouter"
    if "api.openai.com" in url:
        return "openai"
    if "anthropic.com" in url:
        return "anthropic"
    return "generic"


def structured_backend_for(endpoint: Endpoint) -> str:
    if endpoint.structured_backend != "auto":
        return endpoint.structured_backend
    provider = infer_provider(endpoint)
    if provider == "deepseek":
        return "native_json"
    if provider in {"qwen", "openai", "openrouter", "anthropic", "gemini"}:
        return "native_schema"
    # OpenAI-compatible desconhecido: JSON mode é um default mais conservador
    # que fingir suporte a constrained JSON Schema.
    return "native_json" if endpoint.protocol == "openai" else "native_schema"


def thinking_style_for(endpoint: Endpoint) -> str:
    if endpoint.thinking_style != "auto":
        return endpoint.thinking_style
    provider = infer_provider(endpoint)
    if provider in {"qwen", "deepseek", "openrouter", "anthropic"}:
        return provider
    return "none"


def check_endpoint(endpoint: Endpoint, *, label: str) -> None:
    if endpoint.protocol not in PROTOCOLS:
        raise RuntimeError(f"{label}: LLM_PROTOCOL deve ser openai, anthropic ou gemini.")
    if not endpoint.base_url:
        raise RuntimeError(f"{label}: UPSTREAM_BASE_URL não configurada.")
    if not (endpoint.model_override or endpoint.model):
        raise RuntimeError(f"{label}: DEFAULT_MODEL/MODEL_OVERRIDE não configurado.")
    if endpoint.protocol in {"anthropic", "gemini"} and not endpoint.api_key:
        raise RuntimeError(f"{label}: UPSTREAM_API_KEY é obrigatória para API nativa.")
    if endpoint.structured_backend != "auto" and endpoint.structured_backend not in STRUCTURED_BACKENDS:
        raise RuntimeError(
            f"{label}: STRUCTURED_BACKEND deve ser auto, native_schema, native_tool, native_json ou text."
        )
    if endpoint.thinking_style not in THINKING_STYLES:
        raise RuntimeError(
            f"{label}: THINKING_STYLE deve ser auto, qwen, deepseek, openrouter, anthropic ou none."
        )
    if endpoint.protocol != "openai" and structured_backend_for(endpoint) == "native_tool":
        raise RuntimeError(f"{label}: native_tool é suportado somente no protocolo OpenAI-compatible.")


def check_config() -> None:
    check_endpoint(S.primary, label="primary")
    if S.fallback is not None:
        check_endpoint(S.fallback, label="fallback")


def client_auth(request: Request) -> None:
    if not S.proxy_key:
        return
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    supplied = bearer or request.headers.get("x-api-key", "").strip()
    if not supplied or not hmac.compare_digest(supplied, S.proxy_key):
        raise HTTPException(401, "Chave do proxy inválida.")


def model_for(payload: dict[str, Any], endpoint: Endpoint) -> str:
    return endpoint.model_override or str(payload.get("model") or "").strip() or endpoint.model


def structured(payload: dict[str, Any]) -> bool:
    fmt = payload.get("response_format")
    if fmt is None:
        return False
    if not isinstance(fmt, dict) or fmt.get("type") not in {"json_object", "json_schema"}:
        raise HTTPException(400, "response_format inválido.")
    if fmt.get("type") == "json_schema":
        block = fmt.get("json_schema")
        if not isinstance(block, dict) or not isinstance(block.get("schema"), dict):
            raise HTTPException(400, "response_format.json_schema.schema inválido.")
        try:
            Draft202012Validator.check_schema(block["schema"])
        except SchemaError as exc:
            raise HTTPException(400, f"JSON Schema inválido: {exc.message}") from exc
    return True


def schema_for(payload: dict[str, Any]) -> dict[str, Any] | None:
    fmt = payload.get("response_format")
    if not isinstance(fmt, dict):
        return None
    if fmt.get("type") == "json_object":
        return {"type": "object", "additionalProperties": True}
    block = fmt.get("json_schema")
    return block.get("schema") if isinstance(block, dict) else None


def thinking_enabled(payload: dict[str, Any]) -> bool:
    if S.force_thinking:
        return True
    if isinstance(payload.get("enable_thinking"), bool):
        return payload["enable_thinking"]
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and isinstance(reasoning.get("enabled"), bool):
        return reasoning["enabled"]
    thinking = payload.get("thinking")
    if isinstance(thinking, dict):
        thinking_type = str(thinking.get("type") or "").strip().lower()
        if thinking_type == "enabled":
            return True
        if thinking_type == "disabled":
            return False
    if isinstance(payload.get("reasoning_effort"), str) and payload["reasoning_effort"].strip():
        return True
    return S.structured_thinking if structured(payload) else S.default_thinking


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}:
                if isinstance(item.get("text"), str):
                    out.append(item["text"])
        return "\n".join(out)
    return ""


def split_messages(payload: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages deve ser uma lista não vazia.")

    system_parts: list[str] = []
    normal: list[dict[str, str]] = []
    for m in messages:
        if not isinstance(m, dict):
            raise HTTPException(400, "Mensagem inválida.")
        role = str(m.get("role") or "").lower()
        text = text_of(m.get("content"))
        if role in {"system", "developer"}:
            if text:
                system_parts.append(text)
            continue
        if role not in {"user", "assistant"}:
            raise HTTPException(400, f"Role não suportada no modo nativo: {role}")
        if normal and normal[-1]["role"] == role:
            normal[-1]["content"] += "\n" + text
        else:
            normal.append({"role": role, "content": text})
    if not normal:
        raise HTTPException(400, "Nenhuma mensagem user/assistant.")
    return "\n".join(system_parts), normal


def max_output(payload: dict[str, Any]) -> int:
    for key in ("max_completion_tokens", "max_tokens"):
        if isinstance(payload.get(key), int) and payload[key] > 0:
            return payload[key]
    return S.default_max_output


def _schema_constraint_lines(schema: dict[str, Any], path: str = "$", *, limit: int = 16) -> list[str]:
    """Extrai somente restrições mecânicas úteis; o JSON Schema completo continua sendo a fonte de verdade."""
    lines: list[str] = []

    def walk(node: Any, here: str) -> None:
        if len(lines) >= limit or not isinstance(node, dict):
            return
        if isinstance(node.get("maxLength"), int):
            lines.append(f"- {here}: no máximo {node['maxLength']} caracteres")
        if isinstance(node.get("minLength"), int):
            lines.append(f"- {here}: no mínimo {node['minLength']} caracteres")
        if isinstance(node.get("maxItems"), int):
            lines.append(f"- {here}: no máximo {node['maxItems']} itens")
        if isinstance(node.get("minItems"), int):
            lines.append(f"- {here}: no mínimo {node['minItems']} itens")
        if isinstance(node.get("enum"), list) and len(node["enum"]) <= 8:
            values = ", ".join(json.dumps(v, ensure_ascii=False) for v in node["enum"])
            lines.append(f"- {here}: valor deve ser um de [{values}]")
        if "const" in node:
            lines.append(f"- {here}: valor deve ser {json.dumps(node['const'], ensure_ascii=False)}")
        if isinstance(node.get("required"), list) and node["required"]:
            req = ", ".join(str(x) for x in node["required"][:10])
            lines.append(f"- {here}: campos obrigatórios [{req}]")
        props = node.get("properties")
        if isinstance(props, dict):
            for name, child in props.items():
                walk(child, f"{here}.{name}")
                if len(lines) >= limit:
                    return
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, f"{here}[]")

    walk(schema, path)
    return lines[:limit]


def add_schema_instruction(body: dict[str, Any], schema: dict[str, Any], *, tool_mode: bool = False) -> None:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    constraint_lines = _schema_constraint_lines(schema)
    constraints = "\n".join(constraint_lines)
    instruction = (
        "Produza somente a saída estruturada solicitada. O contrato canônico é este JSON Schema: "
        f"{schema_text}"
    )
    if constraints:
        instruction += "\nRestrições mecânicas importantes derivadas do schema:\n" + constraints
    if tool_mode:
        instruction += "\nUse exatamente a função estruturada fornecida e não responda com texto livre."
    else:
        instruction += "\nNão use markdown, comentários ou texto fora do objeto JSON."
    body["messages"] = [{"role": "system", "content": instruction}, *messages]


def _apply_openai_thinking(body: dict[str, Any], payload: dict[str, Any], endpoint: Endpoint) -> None:
    style = thinking_style_for(endpoint)
    enabled = thinking_enabled(payload)
    if S.force_thinking:
        enabled = True

    # Não vaza aliases incompatíveis entre providers.
    for key in ("enable_thinking", "reasoning", "thinking"):
        body.pop(key, None)

    if style == "qwen":
        body["enable_thinking"] = enabled
        body.pop("reasoning_effort", None)
    elif style == "deepseek":
        body["thinking"] = {"type": "enabled" if enabled else "disabled"}
        if enabled:
            effort = str(payload.get("reasoning_effort") or "high").strip().lower()
            body["reasoning_effort"] = effort if effort in {"high", "max"} else "high"
            for key in ("temperature", "top_p", "presence_penalty", "frequency_penalty"):
                body.pop(key, None)
        else:
            body.pop("reasoning_effort", None)
    elif style == "openrouter":
        body["reasoning"] = {"enabled": enabled}
        body.pop("reasoning_effort", None)
    elif style == "anthropic":
        if enabled:
            body["thinking"] = {"type": "adaptive"}
        body.pop("reasoning_effort", None)
    else:
        body.pop("reasoning_effort", None)


def _apply_openai_structured_backend(
    body: dict[str, Any], payload: dict[str, Any], endpoint: Endpoint
) -> str:
    backend = structured_backend_for(endpoint)
    schema = schema_for(payload)
    fmt = body.get("response_format")
    if not isinstance(fmt, dict) or fmt.get("type") != "json_schema" or not schema:
        return backend

    if backend == "native_schema":
        return backend

    if backend == "native_tool":
        add_schema_instruction(body, schema, tool_mode=True)
        body.pop("response_format", None)
        tool_name = "submit_structured_output"
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": "Entrega exatamente o objeto estruturado exigido pelo chamador.",
                    "parameters": schema,
                },
            }
        ]
        body["tool_choice"] = {"type": "function", "function": {"name": tool_name}}
        return backend

    add_schema_instruction(body, schema)
    if backend == "native_json":
        body["response_format"] = {"type": "json_object"}
    elif backend == "text":
        body.pop("response_format", None)
    return backend


@dataclass(frozen=True)
class PreparedRequest:
    url: str
    headers: dict[str, str]
    body: dict[str, Any]
    structured_backend: str
    provider_profile: str


def prepare_openai(payload: dict[str, Any], endpoint: Endpoint) -> PreparedRequest:
    if not isinstance(payload.get("messages"), list):
        raise HTTPException(400, "messages inválido.")
    structured(payload)
    body = copy.deepcopy(payload)
    body["model"] = model_for(payload, endpoint)
    backend = _apply_openai_structured_backend(body, payload, endpoint)
    _apply_openai_thinking(body, payload, endpoint)

    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if endpoint.api_key:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
    return PreparedRequest(
        url=f"{endpoint.base_url}/chat/completions",
        headers=headers,
        body=body,
        structured_backend=backend,
        provider_profile=infer_provider(endpoint),
    )


def prepare_anthropic(payload: dict[str, Any], endpoint: Endpoint) -> PreparedRequest:
    if payload.get("stream"):
        raise HTTPException(400, "Use stream=false no modo Anthropic nativo.")
    system, messages = split_messages(payload)
    body: dict[str, Any] = {
        "model": model_for(payload, endpoint),
        "max_tokens": max_output(payload),
        "messages": messages,
    }
    if system:
        body["system"] = system
    if isinstance(payload.get("temperature"), (int, float)):
        body["temperature"] = payload["temperature"]
    if isinstance(payload.get("top_p"), (int, float)):
        body["top_p"] = payload["top_p"]
    stop = payload.get("stop")
    if isinstance(stop, str):
        body["stop_sequences"] = [stop]
    elif isinstance(stop, list):
        body["stop_sequences"] = [x for x in stop if isinstance(x, str)]
    if thinking_enabled(payload):
        body["thinking"] = {"type": "adaptive"}
    schema = schema_for(payload)
    if schema:
        body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    headers = {
        "Content-Type": "application/json",
        "x-api-key": endpoint.api_key,
        "anthropic-version": "2023-06-01",
    }
    path = "/messages" if endpoint.base_url.endswith("/v1") else "/v1/messages"
    return PreparedRequest(
        url=f"{endpoint.base_url}{path}",
        headers=headers,
        body=body,
        structured_backend="native_schema",
        provider_profile="anthropic",
    )


def prepare_gemini(payload: dict[str, Any], endpoint: Endpoint) -> PreparedRequest:
    if payload.get("stream"):
        raise HTTPException(400, "Use stream=false no modo Gemini nativo.")
    system, messages = split_messages(payload)
    model = model_for(payload, endpoint)
    config: dict[str, Any] = {"maxOutputTokens": max_output(payload)}
    if isinstance(payload.get("temperature"), (int, float)):
        config["temperature"] = payload["temperature"]
    if isinstance(payload.get("top_p"), (int, float)):
        config["topP"] = payload["top_p"]
    stop = payload.get("stop")
    if isinstance(stop, str):
        config["stopSequences"] = [stop]
    elif isinstance(stop, list):
        config["stopSequences"] = [x for x in stop if isinstance(x, str)]
    schema = schema_for(payload)
    if schema:
        config["responseMimeType"] = "application/json"
        config["responseJsonSchema"] = schema
    enabled = thinking_enabled(payload)
    if "2.5" in model.lower():
        config["thinkingConfig"] = {"thinkingBudget": -1 if enabled else 0}
    else:
        config["thinkingConfig"] = {"thinkingLevel": "high" if enabled else "low"}
    body: dict[str, Any] = {
        "contents": [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
        ],
        "generationConfig": config,
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    headers = {"Content-Type": "application/json", "x-goog-api-key": endpoint.api_key}
    return PreparedRequest(
        url=f"{endpoint.base_url}/models/{quote(model, safe='')}:generateContent",
        headers=headers,
        body=body,
        structured_backend="native_schema",
        provider_profile="gemini",
    )


def prepare(payload: dict[str, Any], endpoint: Endpoint) -> PreparedRequest:
    if endpoint.protocol == "anthropic":
        return prepare_anthropic(payload, endpoint)
    if endpoint.protocol == "gemini":
        return prepare_gemini(payload, endpoint)
    return prepare_openai(payload, endpoint)


def anthropic_to_openai(data: dict[str, Any]) -> dict[str, Any]:
    text, reasoning = [], []
    for block in data.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text.append(block["text"])
        elif block.get("type") in {"thinking", "redacted_thinking"}:
            value = block.get("thinking") or block.get("text")
            if isinstance(value, str):
                reasoning.append(value)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or 0)
    stop = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "refusal": "content_filter",
    }.get(data.get("stop_reason"), data.get("stop_reason"))
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    return {
        "id": data.get("id") or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": data.get("model"),
        "choices": [{"index": 0, "message": message, "finish_reason": stop}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def gemini_to_openai(data: dict[str, Any], model: str) -> dict[str, Any]:
    candidates = data.get("candidates") or []
    candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    parts = ((candidate.get("content") or {}).get("parts") or [])
    text, reasoning = [], []
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("text"), str):
            continue
        (reasoning if part.get("thought") is True else text).append(part["text"])
    usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
    prompt_tokens = int(usage.get("promptTokenCount") or 0)
    answer_tokens = int(usage.get("candidatesTokenCount") or 0)
    reasoning_tokens = int(usage.get("thoughtsTokenCount") or 0)
    completion_tokens = answer_tokens + reasoning_tokens
    finish = str(candidate.get("finishReason") or "").upper()
    finish = (
        "stop"
        if finish in {"STOP", "FINISH_REASON_UNSPECIFIED"}
        else "length"
        if finish == "MAX_TOKENS"
        else "content_filter"
        if finish in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}
        else finish.lower() or None
    )
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    result: dict[str, Any] = {
        "id": data.get("responseId") or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": data.get("modelVersion") or model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": int(usage.get("totalTokenCount") or (prompt_tokens + completion_tokens)),
        },
    }
    if reasoning_tokens:
        result["usage"]["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return result


def _openai_tool_to_content(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return data
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        return data
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return data
    first = tool_calls[0]
    function = first.get("function") if isinstance(first, dict) else None
    if not isinstance(function, dict) or function.get("name") != "submit_structured_output":
        return data
    arguments = function.get("arguments")
    if isinstance(arguments, dict):
        content = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(arguments, str):
        content = arguments
    else:
        content = ""
    normalized = copy.deepcopy(data)
    normalized_message = normalized["choices"][0].setdefault("message", {})
    normalized_message["content"] = content
    normalized_message.pop("tool_calls", None)
    normalized["choices"][0]["finish_reason"] = "stop"
    return normalized


def normalize_upstream(data: dict[str, Any], endpoint: Endpoint, backend: str) -> dict[str, Any]:
    if endpoint.protocol == "anthropic":
        return anthropic_to_openai(data)
    if endpoint.protocol == "gemini":
        return gemini_to_openai(data, model_for({}, endpoint))
    return _openai_tool_to_content(data) if backend == "native_tool" else data


def _assistant_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    return text_of(message.get("content")) if isinstance(message, dict) else ""


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)


def parse_json_representation(text: str) -> Any:
    """Normalização puramente representacional: fence e JSON duplamente serializado."""
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


def _json_path(error: ValidationError) -> str:
    path = "$"
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


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
    concise: list[str] = []
    for error in errors[:8]:
        message = error.message.replace("\n", " ")
        if len(message) > 220:
            message = message[:217] + "..."
        concise.append(f"{_json_path(error)}: {message}")
    return value, concise


def canonicalize_content(data: dict[str, Any], value: Any) -> dict[str, Any]:
    normalized = copy.deepcopy(data)
    choices = normalized.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return normalized
    message = choices[0].get("message")
    if not isinstance(message, dict):
        message = {"role": "assistant"}
        choices[0]["message"] = message
    message["content"] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return normalized


def build_repair_payload(
    original: dict[str, Any], previous_content: str, validation_errors: list[str]
) -> dict[str, Any]:
    repaired = copy.deepcopy(original)
    repaired["stream"] = False
    messages = repaired.get("messages")
    if not isinstance(messages, list):
        return repaired
    error_text = "\n".join(f"- {e}" for e in validation_errors[:8])
    instruction = (
        "A resposta estruturada anterior não satisfez o contrato solicitado. Corrija somente o necessário para "
        "produzir um objeto semanticamente equivalente que obedeça ao JSON Schema. Não acrescente explicações.\n"
        f"Erros de validação:\n{error_text}\n"
        "Responda novamente somente com a saída estruturada corrigida."
    )
    repaired["messages"] = [
        *messages,
        {"role": "assistant", "content": previous_content[:12000]},
        {"role": "user", "content": instruction},
    ]
    return repaired


@dataclass
class UsageAccumulator:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0

    def add(self, data: dict[str, Any]) -> None:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            self.cached_prompt_tokens += int(prompt_details.get("cached_tokens") or 0)
        self.cached_prompt_tokens += int(usage.get("prompt_cache_hit_tokens") or 0)
        completion_details = usage.get("completion_tokens_details")
        if isinstance(completion_details, dict):
            self.reasoning_tokens += int(completion_details.get("reasoning_tokens") or 0)

    def apply(self, data: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(data)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        usage["prompt_tokens"] = self.prompt_tokens
        usage["completion_tokens"] = self.completion_tokens
        usage["total_tokens"] = self.prompt_tokens + self.completion_tokens
        if self.cached_prompt_tokens:
            details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
            details["cached_tokens"] = self.cached_prompt_tokens
            usage["prompt_tokens_details"] = details
        if self.reasoning_tokens:
            details = (
                usage.get("completion_tokens_details")
                if isinstance(usage.get("completion_tokens_details"), dict)
                else {}
            )
            details["reasoning_tokens"] = self.reasoning_tokens
            usage["completion_tokens_details"] = details
        result["usage"] = usage
        return result

    def as_dict(self) -> dict[str, int]:
        out = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        }
        if self.cached_prompt_tokens:
            out["cached_prompt_tokens"] = self.cached_prompt_tokens
        if self.reasoning_tokens:
            out["reasoning_tokens"] = self.reasoning_tokens
        return out


@dataclass(frozen=True)
class AttemptResult:
    data: dict[str, Any] | None
    status_code: int
    media_type: str
    raw: bytes
    prepared: PreparedRequest


async def call_once(
    client: httpx.AsyncClient, payload: dict[str, Any], endpoint: Endpoint
) -> AttemptResult:
    prepared = prepare(payload, endpoint)
    upstream = await client.post(prepared.url, headers=prepared.headers, json=prepared.body)
    media = upstream.headers.get("content-type", "application/json")
    if upstream.status_code >= 400:
        return AttemptResult(None, upstream.status_code, media, upstream.content, prepared)
    try:
        raw_data = upstream.json()
    except Exception:
        return AttemptResult(None, 502, media, upstream.content, prepared)
    if not isinstance(raw_data, dict):
        return AttemptResult(None, 502, media, upstream.content, prepared)
    data = normalize_upstream(raw_data, endpoint, prepared.structured_backend)
    return AttemptResult(data, upstream.status_code, media, upstream.content, prepared)


def _adapter_headers(
    *, requested: str, backend: str, enforcement: str, repairs: int, fallback_used: bool,
    provider: str, attempts: int, usage: UsageAccumulator,
) -> dict[str, str]:
    headers = {
        "X-Eyle-Structured-Requested": requested,
        "X-Eyle-Structured-Backend": backend,
        "X-Eyle-Schema-Enforcement": enforcement,
        "X-Eyle-Structured-Repairs": str(repairs),
        "X-Eyle-Structured-Fallback": "1" if fallback_used else "0",
        "X-Eyle-Provider-Profile": provider,
        "X-Eyle-Upstream-Attempts": str(attempts),
        "X-Eyle-Usage-Prompt-Tokens": str(usage.prompt_tokens),
        "X-Eyle-Usage-Completion-Tokens": str(usage.completion_tokens),
    }
    return headers


async def execute_structured(
    client: httpx.AsyncClient, incoming: dict[str, Any], request_id: str
) -> Response:
    schema = schema_for(incoming)
    if schema is None:
        raise HTTPException(400, "Structured request sem schema.")
    requested = str((incoming.get("response_format") or {}).get("type") or "json_schema")
    usage = UsageAccumulator()
    total_attempts = 0
    total_repairs = 0
    fallback_used = False
    last_errors: list[str] = []
    last_backend = structured_backend_for(S.primary)
    last_provider = infer_provider(S.primary)

    async def run_endpoint(endpoint: Endpoint) -> tuple[dict[str, Any] | None, AttemptResult | None, list[str]]:
        nonlocal total_attempts, total_repairs, last_backend, last_provider
        payload = copy.deepcopy(incoming)
        if endpoint is not S.primary:
            payload["model"] = endpoint.model_override or endpoint.model
        for repair_index in range(S.structured_repair_attempts + 1):
            result = await call_once(client, payload, endpoint)
            total_attempts += 1
            last_backend = result.prepared.structured_backend
            last_provider = result.prepared.provider_profile
            if result.data is None:
                # Erro HTTP/provider não é transformado em decisão semântica nem em repair de schema.
                return None, result, [f"upstream HTTP {result.status_code}"]
            usage.add(result.data)
            value, errors = validate_structured_response(result.data, schema)
            if not errors:
                assert value is not None
                return canonicalize_content(result.data, value), result, []
            last_content = _assistant_content(result.data)
            last_errors[:] = errors
            if repair_index >= S.structured_repair_attempts:
                return None, result, errors
            total_repairs += 1
            payload = build_repair_payload(incoming, last_content, errors)
        return None, None, ["structured repair exhausted"]

    data, attempt, errors = await run_endpoint(S.primary)
    if data is None and errors and S.fallback is not None and not errors[0].startswith("upstream HTTP"):
        fallback_used = True
        data, attempt, errors = await run_endpoint(S.fallback)

    if data is not None and attempt is not None:
        aggregated = usage.apply(data)
        headers = _adapter_headers(
            requested=requested,
            backend=last_backend,
            enforcement="provider" if last_backend == "native_schema" and total_repairs == 0 else "adapter",
            repairs=total_repairs,
            fallback_used=fallback_used,
            provider=last_provider,
            attempts=total_attempts,
            usage=usage,
        )
        log.info(
            "request=%s structured_ok provider=%s backend=%s repairs=%s fallback=%s attempts=%s",
            request_id, last_provider, last_backend, total_repairs, fallback_used, total_attempts,
        )
        return JSONResponse(aggregated, headers=headers)

    if attempt is not None and attempt.data is None and attempt.status_code >= 400:
        headers = _adapter_headers(
            requested=requested,
            backend=last_backend,
            enforcement="adapter",
            repairs=total_repairs,
            fallback_used=fallback_used,
            provider=last_provider,
            attempts=total_attempts,
            usage=usage,
        )
        return Response(
            content=attempt.raw,
            status_code=attempt.status_code,
            media_type=attempt.media_type,
            headers=headers,
        )

    headers = _adapter_headers(
        requested=requested,
        backend=last_backend,
        enforcement="adapter_failed",
        repairs=total_repairs,
        fallback_used=fallback_used,
        provider=last_provider,
        attempts=total_attempts,
        usage=usage,
    )
    log.warning(
        "request=%s structured_contract_unsatisfied provider=%s backend=%s repairs=%s fallback=%s errors=%s",
        request_id, last_provider, last_backend, total_repairs, fallback_used, errors or last_errors,
    )
    return JSONResponse(
        status_code=502,
        headers=headers,
        content={
            "error": {
                "type": "structured_contract_unsatisfied",
                "message": "O provider não conseguiu satisfazer o contrato estruturado após a recuperação permitida.",
                "validation_errors": (errors or last_errors)[:8],
                "repairs": total_repairs,
                "fallback_used": fallback_used,
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
        "Eyle LLM Adapter -> %s | protocol=%s | model=%s | provider=%s | structured=%s | fallback=%s",
        S.primary.base_url,
        S.primary.protocol,
        S.primary.model_override or S.primary.model,
        infer_provider(S.primary),
        structured_backend_for(S.primary),
        bool(S.fallback),
    )
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(title="Eyle Universal LLM Adapter", version="2.0", lifespan=lifespan)


@app.get("/")
@app.get("/health")
async def health() -> dict[str, Any]:
    fallback = None
    if S.fallback is not None:
        fallback = {
            "protocol": S.fallback.protocol,
            "provider": infer_provider(S.fallback),
            "model": S.fallback.model_override or S.fallback.model,
            "structured_backend": structured_backend_for(S.fallback),
        }
    return {
        "status": "ok",
        "protocol": S.primary.protocol,
        "upstream": S.primary.base_url,
        "provider": infer_provider(S.primary),
        "model": S.primary.model_override or S.primary.model,
        "structured_backend": structured_backend_for(S.primary),
        "structured_repair_attempts": S.structured_repair_attempts,
        "fallback": fallback,
        "openai_base_url": f"http://{S.host}:{S.port}/v1",
    }


@app.get("/v1/models")
async def models(request: Request) -> dict[str, Any]:
    client_auth(request)
    return {
        "object": "list",
        "data": [
            {
                "id": S.primary.model_override or S.primary.model,
                "object": "model",
                "created": 0,
                "owned_by": infer_provider(S.primary),
            }
        ],
    }


@app.post("/v1/chat/completions")
@app.post("/chat/completions", include_in_schema=False)
async def chat(request: Request) -> Response:
    client_auth(request)
    incoming = await read_body(request)
    request_id = str(uuid.uuid4())
    is_structured = structured(incoming)
    stream = bool(incoming.get("stream"))
    client: httpx.AsyncClient = request.app.state.http

    log.info(
        "request=%s protocol=%s model=%s provider=%s stream=%s structured=%s backend=%s thinking=%s",
        request_id,
        S.primary.protocol,
        model_for(incoming, S.primary),
        infer_provider(S.primary),
        stream,
        is_structured,
        structured_backend_for(S.primary),
        thinking_enabled(incoming),
    )

    if is_structured and stream:
        raise HTTPException(400, "Validação estruturada do adaptador requer stream=false.")

    try:
        if is_structured:
            return await execute_structured(client, incoming, request_id)

        prepared = prepare(incoming, S.primary)
        if S.primary.protocol == "openai" and stream:
            req = client.build_request("POST", prepared.url, headers=prepared.headers, json=prepared.body)
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

        attempt = await call_once(client, incoming, S.primary)
        if attempt.data is None:
            return Response(content=attempt.raw, status_code=attempt.status_code, media_type=attempt.media_type)
        return JSONResponse(attempt.data)

    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"error": {"type": "upstream_timeout", "message": "Timeout no upstream."}},
        )
    except httpx.HTTPError as exc:
        log.exception("request=%s erro HTTP: %s", request_id, exc)
        return JSONResponse(
            status_code=502,
            content={"error": {"type": "upstream_connection_error", "message": "Falha ao conectar ao upstream."}},
        )


if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=S.host,
        port=S.port,
        reload=False,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
