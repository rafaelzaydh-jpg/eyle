"""Provider-neutral OpenAI-compatible transport adapter for Eyle Rev3.

The Adapter owns transport only: provider capability negotiation, JSON-object
recovery, caching knobs, usage accounting and network errors. It intentionally
does not know ECC, Memory, decision types, or any Eyle semantic schema.

Structured requests may carry a client-provided JSON Schema. In auto mode the
Adapter tries the strongest provider transport mechanically:

    native_json_schema -> json_object -> prompt_json

A mode is degraded/cached only when the provider technically rejects that
transport. Semantic/schema mistakes never teach the Adapter to use a weaker
mode. If assistant content is JSON-recoverable it is returned to Eyle; Eyle is
the sole semantic canonicalizer/validator.
"""
from __future__ import annotations

import ast
import copy
import hmac
import json
import logging
import os
import re
import ipaddress
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from starlette.background import BackgroundTask

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=False)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("eyle-openai-adapter")


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on", "sim"}


def env_json(name: str) -> dict[str, Any]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} precisa conter JSON válido") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} precisa ser objeto JSON")
    return value


@dataclass(frozen=True)
class Settings:
    upstream_base_url: str
    upstream_api_key: str
    default_model: str
    model_override: str | None
    structured_mode: str
    cache_mode: str
    extra_headers: dict[str, Any]
    extra_body: dict[str, Any]
    cache_headers: dict[str, Any]
    cache_body: dict[str, Any]
    model_discovery_ttl: float
    model_discovery_negative_ttl: float
    host: str
    port: int
    timeout: float
    max_body: int
    proxy_key: str | None
    proxy_allow_loopback_no_auth: bool


S = Settings(
    upstream_base_url=os.getenv("UPSTREAM_BASE_URL", "").strip().rstrip("/"),
    upstream_api_key=os.getenv("UPSTREAM_API_KEY", "").strip(),
    default_model=os.getenv("DEFAULT_MODEL", "auto").strip(),
    model_override=os.getenv("MODEL_OVERRIDE", "").strip() or None,
    structured_mode=os.getenv("UPSTREAM_STRUCTURED_MODE", "auto").strip().lower(),
    cache_mode=os.getenv("UPSTREAM_CACHE_MODE", "auto").strip().lower(),
    extra_headers=env_json("UPSTREAM_EXTRA_HEADERS_JSON"),
    extra_body=env_json("UPSTREAM_EXTRA_BODY_JSON"),
    cache_headers=env_json("UPSTREAM_CACHE_HEADERS_JSON"),
    cache_body=env_json("UPSTREAM_CACHE_BODY_JSON"),
    model_discovery_ttl=float(os.getenv("MODEL_DISCOVERY_TTL_SECONDS", "300")),
    model_discovery_negative_ttl=float(os.getenv("MODEL_DISCOVERY_NEGATIVE_TTL_SECONDS", "30")),
    host=os.getenv("HOST", "127.0.0.1"),
    port=int(os.getenv("PORT", "8080")),
    timeout=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "1800")),
    max_body=int(os.getenv("MAX_REQUEST_BYTES", str(10 * 1024 * 1024))),
    proxy_key=os.getenv("PROXY_API_KEY", "").strip() or None,
    proxy_allow_loopback_no_auth=env_bool("PROXY_ALLOW_LOOPBACK_NO_AUTH", True),
)

ADAPTER_PROFILE = "eyle-provider-transport-v3"
ADAPTER_TRANSPORT_PROTOCOL = "eyle-adapter-transport-v1"
ADAPTER_HANDSHAKE_SCHEMA = "eyle-adapter-handshake-v1"
ADAPTER_VERSION = "2.7.5-rev3"
# Three capability probes plus one optional format-only repair after a provider
# accepted a mode. This is a mechanical transport bound, not a cognition retry
# policy; Eyle's task deadline/generated-token fuse own semantic recovery.
MAX_UPSTREAM_ATTEMPTS_PER_LOGICAL_CALL = 4
_ALLOWED_STRUCTURED = {"auto", "native_json_schema", "json_object", "prompt_json"}
_ALLOWED_CACHE = {"none", "implicit", "explicit", "session", "auto"}
_STRUCTURED_MODE_ORDER = ("native_json_schema", "json_object", "prompt_json")
_TECHNICAL_MODE_REJECTION_STATUS = {400, 404, 415, 422}

# Cache only proven provider transport *incompatibilities*. A malformed model
# answer says nothing about response_format support and must never weaken future
# requests.
_STRUCTURED_UNSUPPORTED_CACHE: dict[tuple[str, str], set[str]] = {}
_MODEL_DISCOVERY_CACHE: dict[str, dict[str, Any]] = {}


def _mode_key(model: str) -> tuple[str, str]:
    return (S.upstream_base_url, str(model or ""))


def _structured_mode_chain(model: str) -> list[str]:
    if S.structured_mode != "auto":
        return [S.structured_mode]
    unsupported = _STRUCTURED_UNSUPPORTED_CACHE.get(_mode_key(model), set())
    chain = [mode for mode in _STRUCTURED_MODE_ORDER if mode not in unsupported]
    return chain or ["prompt_json"]


def _first_structured_mode(model: str) -> str:
    return _structured_mode_chain(model)[0]


def _record_transport_rejection(model: str, mode: str) -> None:
    if S.structured_mode != "auto" or mode == "prompt_json":
        return
    _STRUCTURED_UNSUPPORTED_CACHE.setdefault(_mode_key(model), set()).add(mode)


def _fallback_structured_mode(mode: str) -> str:
    # Backward-compatible helper for tests/callers; never used for semantic
    # failures. It simply returns the next physical transport in the chain.
    try:
        index = _STRUCTURED_MODE_ORDER.index(mode)
    except ValueError:
        return "prompt_json"
    return _STRUCTURED_MODE_ORDER[min(index + 1, len(_STRUCTURED_MODE_ORDER) - 1)]


def check_config() -> None:
    if not S.upstream_base_url:
        raise RuntimeError("UPSTREAM_BASE_URL não configurada")
    if not (S.model_override or S.default_model):
        raise RuntimeError("DEFAULT_MODEL/MODEL_OVERRIDE não configurado")
    if S.structured_mode not in _ALLOWED_STRUCTURED:
        raise RuntimeError(f"UPSTREAM_STRUCTURED_MODE inválido: {S.structured_mode}")
    if S.cache_mode not in _ALLOWED_CACHE:
        raise RuntimeError(f"UPSTREAM_CACHE_MODE inválido: {S.cache_mode}")
    if S.cache_mode in {"explicit", "session"} and not (S.cache_headers or S.cache_body):
        raise RuntimeError("UPSTREAM_CACHE_MODE explicit/session exige UPSTREAM_CACHE_HEADERS_JSON e/ou UPSTREAM_CACHE_BODY_JSON")


def _request_is_loopback(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = str(getattr(client, "host", "") or "").strip()
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def client_auth(request: Request) -> None:
    if not S.proxy_key:
        return
    # Eyle Core does not send an Authorization header to its configured
    # OpenAI-compatible endpoint. Keep direct localhost integration working
    # while still requiring PROXY_API_KEY for non-loopback clients.
    if S.proxy_allow_loopback_no_auth and _request_is_loopback(request):
        return
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    supplied = bearer or request.headers.get("x-api-key", "").strip()
    if not supplied or not hmac.compare_digest(supplied, S.proxy_key):
        raise HTTPException(401, "Chave do proxy inválida")


def model_for(payload: dict[str, Any]) -> str:
    """Return an explicit configured/requested model, or ``auto`` as a sentinel.

    ``auto`` is never sent upstream; ``resolve_model`` must replace it
    with a real ID discovered from the upstream /models endpoint.
    """
    if S.model_override:
        return str(S.model_override).strip()
    requested = str(payload.get("model") or "").strip()
    default = str(S.default_model or "auto").strip() or "auto"
    if requested and requested.lower() != "auto":
        return requested
    if default.lower() != "auto":
        return default
    return "auto"


def _model_ids_from_payload(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("data")
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            value = str(item.get("id") or "").strip()
            if value and value not in out:
                out.append(value)
    return out


async def discover_models(client: httpx.AsyncClient, *, force: bool = False) -> list[str]:
    """Discover real upstream model IDs with bounded positive/negative caching."""
    key = S.upstream_base_url
    now = time.monotonic()
    cached = _MODEL_DISCOVERY_CACHE.get(key)
    if not force and isinstance(cached, dict) and float(cached.get("expires_at") or 0) > now:
        if cached.get("error"):
            raise RuntimeError(str(cached.get("error")))
        return list(cached.get("models") or [])

    headers = {"Accept": "application/json"}
    if S.upstream_api_key:
        headers["Authorization"] = f"Bearer {S.upstream_api_key}"
    for name, value in S.extra_headers.items():
        headers[str(name)] = str(value)
    url = f"{S.upstream_base_url}/models"
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        models = _model_ids_from_payload(response.json())
        if not models:
            raise RuntimeError("UPSTREAM_MODELS_EMPTY")
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:300]}"
        _MODEL_DISCOVERY_CACHE[key] = {
            "models": [], "error": detail,
            "expires_at": now + max(1.0, S.model_discovery_negative_ttl),
        }
        raise RuntimeError(detail) from exc
    _MODEL_DISCOVERY_CACHE[key] = {
        "models": models, "error": None,
        "expires_at": now + max(1.0, S.model_discovery_ttl),
    }
    return models


async def resolve_model(client: httpx.AsyncClient, payload: dict[str, Any]) -> str:
    candidate = model_for(payload)
    if candidate.lower() != "auto":
        return candidate
    models = await discover_models(client)
    if not models:
        raise RuntimeError("MODEL_DISCOVERY_REQUIRED")
    return models[0]


def schema_for(payload: dict[str, Any]) -> dict[str, Any] | None:
    fmt = payload.get("response_format")
    if not isinstance(fmt, dict):
        return None
    if fmt.get("type") == "json_object":
        return {"type": "object"}
    if fmt.get("type") != "json_schema":
        return None
    block = fmt.get("json_schema")
    return block.get("schema") if isinstance(block, dict) and isinstance(block.get("schema"), dict) else None


def structured(payload: dict[str, Any]) -> bool:
    fmt = payload.get("response_format")
    if fmt is None:
        return False
    if not isinstance(fmt, dict) or fmt.get("type") not in {"json_object", "json_schema"}:
        raise HTTPException(400, "response_format inválido")
    schema = schema_for(payload)
    if schema is None:
        raise HTTPException(400, "response_format estruturado sem schema/formato válido")
    if fmt.get("type") == "json_schema":
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise HTTPException(400, f"JSON Schema inválido: {exc.message}") from exc
    return True


def _json_only_instruction() -> str:
    return (
        "Return exactly one JSON object and no markdown/prose outside it. "
        "Preserve the response shape and semantics requested by the caller."
    )


def _prepare_upstream(payload: dict[str, Any], *, repair: str | None = None, structured_mode: str | None = None, resolved_model: str | None = None) -> tuple[dict[str, Any], dict[str, str], dict[str, Any] | None]:
    if not isinstance(payload.get("messages"), list):
        raise HTTPException(400, "messages inválido")
    body = copy.deepcopy(payload)
    body["model"] = str(resolved_model or model_for(payload))
    if body["model"].strip().lower() == "auto":
        raise HTTPException(503, "MODEL_DISCOVERY_REQUIRED")
    schema = schema_for(payload) if structured(payload) else None
    messages = list(body.get("messages") or [])

    mode = structured_mode or _first_structured_mode(str(body.get("model") or ""))
    if schema is not None:
        if mode == "native_json_schema":
            pass
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
            # Insert before the first dynamic user message, preserving a stable prefix.
            insert_at = 1 if messages and messages[0].get("role") == "system" else 0
            messages.insert(insert_at, {"role": "system", "content": _json_only_instruction()})
        elif mode == "prompt_json":
            body.pop("response_format", None)
            insert_at = 1 if messages and messages[0].get("role") == "system" else 0
            messages.insert(insert_at, {"role": "system", "content": _json_only_instruction()})

    # Format repair is a suffix. Everything before it stays byte-identical, which
    # is cache-friendly for providers with prefix caching and harmless elsewhere.
    if repair:
        messages.append({"role": "user", "content": repair})
        body["temperature"] = 0
    body["messages"] = messages

    # Provider-specific transport knobs live only in configuration, not Core.
    for key, value in S.extra_body.items():
        body[key] = copy.deepcopy(value)
    if S.cache_mode in {"explicit", "session"}:
        for key, value in S.cache_body.items():
            body[key] = copy.deepcopy(value)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if S.upstream_api_key:
        headers["Authorization"] = f"Bearer {S.upstream_api_key}"
    for key, value in S.extra_headers.items():
        headers[str(key)] = str(value)
    if S.cache_mode in {"explicit", "session"}:
        for key, value in S.cache_headers.items():
            headers[str(key)] = str(value)
    return body, headers, schema


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
        prompt = max(0, int(usage.get("prompt_tokens") or 0))
        completion = max(0, int(usage.get("completion_tokens") or 0))
        details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
        candidates = [
            details.get("cached_tokens"), usage.get("prompt_cache_hit_tokens"),
            usage.get("cached_prompt_tokens"), usage.get("cached_tokens"),
        ]
        cached_values = [int(v) for v in candidates if isinstance(v, (int, float)) and int(v) >= 0]
        cached = min(prompt, max(cached_values)) if cached_values else 0
        cdetails = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cached_prompt_tokens += cached
        self.reasoning_tokens += max(0, int(cdetails.get("reasoning_tokens") or 0))

    def apply(self, data: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(data)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        usage.update({
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "prompt_cache_hit_tokens": min(self.prompt_tokens, self.cached_prompt_tokens),
            "prompt_cache_miss_tokens": max(0, self.prompt_tokens - min(self.prompt_tokens, self.cached_prompt_tokens)),
        })
        if self.reasoning_tokens:
            details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
            details["reasoning_tokens"] = self.reasoning_tokens
            usage["completion_tokens_details"] = details
        result["usage"] = usage
        return result

    def as_dict(self) -> dict[str, int]:
        cached = min(self.prompt_tokens, self.cached_prompt_tokens)
        return {
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "prompt_cache_hit_tokens": cached, "prompt_cache_miss_tokens": max(0, self.prompt_tokens - cached),
        }


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out=[]
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text","input_text","output_text"} and isinstance(item.get("text"), str):
                out.append(item["text"])
        return "\n".join(out)
    return ""


def assistant_content(data: dict[str, Any]) -> str:
    choices=data.get("choices")
    if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
        return ""
    message=choices[0].get("message")
    return text_of(message.get("content")) if isinstance(message,dict) else ""


_FENCE_RE = re.compile(r"^\s*```(?:json|javascript|python)?\s*(.*?)\s*```\s*$", re.I | re.S)


def _balanced_json_fragment(text: str) -> str | None:
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
    last: Exception | None = None
    for decoder in (json.loads, ast.literal_eval):
        try:
            value = decoder(candidate)
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
        except Exception as exc:
            last = exc
    if last is not None:
        raise last
    raise ValueError("no decoder")


def parse_json_value(text: str) -> tuple[Any, list[str]]:
    """Purely syntactic recovery; never normalizes client semantics."""
    candidate = str(text or "").strip()
    if not candidate:
        raise ValueError("empty assistant content")
    candidates: list[tuple[str, str]] = []
    match = _FENCE_RE.match(candidate)
    if match:
        candidates.append((match.group(1).strip(), "strip_markdown_fence"))
    candidates.append((candidate, "direct_json"))
    fragment = _balanced_json_fragment(candidate)
    if fragment and fragment != candidate:
        candidates.append((fragment, "extract_balanced_json"))
    last: Exception | None = None
    for item, step in candidates:
        try:
            value = _decode_jsonish(item)
            return value, ([] if step == "direct_json" else [step])
        except Exception as exc:
            last = exc
    raise last or ValueError("JSON recovery failed")


def normalize_structured(data: dict[str, Any], schema: dict[str, Any] | None = None) -> tuple[Any | None, list[str], list[str]]:
    """Recover JSON only. ``schema`` is intentionally ignored semantically."""
    text = assistant_content(data)
    if not text.strip():
        return None, ["$: assistant content is empty"], []
    try:
        value, steps = parse_json_value(text)
    except Exception as exc:
        return None, [f"$: JSON is not recoverable ({type(exc).__name__})"], []
    return value, [], steps


def canonicalize(data: dict[str, Any], value: Any) -> dict[str, Any]:
    result = copy.deepcopy(data)
    choices = result.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if not isinstance(message, dict):
            message = {"role": "assistant"}
            choices[0]["message"] = message
        message["content"] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return result


@dataclass(frozen=True)
class AttemptResult:
    data: dict[str,Any]|None
    status_code: int
    media_type: str
    raw: bytes


async def call_once(client:httpx.AsyncClient,payload:dict[str,Any],request_id:str,attempt_no:int,*,repair:str|None=None,structured_mode:str|None=None,resolved_model:str|None=None)->AttemptResult:
    body,headers,_=_prepare_upstream(payload,repair=repair,structured_mode=structured_mode,resolved_model=resolved_model)
    url=f"{S.upstream_base_url}/chat/completions"
    effective_mode = structured_mode or _first_structured_mode(str(body.get("model") or ""))
    log.info("request=%s upstream_attempt=%s model=%s structured_mode=%s configured_mode=%s cache_mode=%s",request_id,attempt_no,body.get("model"),effective_mode,S.structured_mode,S.cache_mode)
    response=await client.post(url,headers=headers,json=body)
    media=response.headers.get("content-type","application/json")
    if response.status_code>=400:
        return AttemptResult(None,response.status_code,media,response.content)
    try: data=response.json()
    except Exception: return AttemptResult(None,502,media,response.content)
    return AttemptResult(data if isinstance(data,dict) else None,response.status_code,media,response.content)


def adapter_headers(usage:UsageAccumulator,attempts:int,enforcement:str,*,repairs:int=0,normalized:bool=False,effective_mode:str|None=None)->dict[str,str]:
    return {
        "X-Eyle-Adapter-Profile":ADAPTER_PROFILE,
        "X-Eyle-Structured-Upstream-Mode":effective_mode or S.structured_mode,
        "X-Eyle-Structured-Configured-Mode":S.structured_mode,
        "X-Eyle-Cache-Mode":S.cache_mode,
        "X-Eyle-Schema-Enforcement":enforcement,
        "X-Eyle-Structured-Repairs":str(repairs),
        "X-Eyle-Upstream-Attempts":str(attempts),
        "X-Eyle-Max-Upstream-Attempts":str(MAX_UPSTREAM_ATTEMPTS_PER_LOGICAL_CALL),
        "X-Eyle-Local-Normalized":"1" if normalized else "0",
        "X-Eyle-Usage-Prompt-Tokens":str(usage.prompt_tokens),
        "X-Eyle-Usage-Completion-Tokens":str(usage.completion_tokens),
        "X-Eyle-Usage-Cached-Prompt-Tokens":str(min(usage.prompt_tokens,usage.cached_prompt_tokens)),
    }


def _safe_transport_detail(exc: BaseException) -> dict[str, Any]:
    return {
        "exception": type(exc).__name__,
        "detail": str(exc)[:300],
        "target": S.upstream_base_url,
    }


def _transport_failure(usage: UsageAccumulator, attempts: int, enforcement: str, exc: BaseException, *, repairs: int = 0, effective_mode: str | None = None, timeout: bool = False) -> JSONResponse:
    billed = usage.prompt_tokens > 0 or usage.completion_tokens > 0
    error = {
        "type": "upstream_timeout" if timeout else "upstream_connection_error",
        **_safe_transport_detail(exc),
        "upstream_attempts": attempts,
        "repairs": repairs,
        "billing_may_have_occurred": bool(billed or timeout),
        "retry_cost_risk": bool(billed or timeout),
    }
    headers = adapter_headers(usage, attempts, enforcement, repairs=repairs, effective_mode=effective_mode)
    headers["X-Eyle-Billing-May-Have-Occurred"] = "1" if error["billing_may_have_occurred"] else "0"
    headers["X-Eyle-Retry-Cost-Risk"] = "1" if error["retry_cost_risk"] else "0"
    log.warning("adapter transport failure: %s", json.dumps(error, ensure_ascii=False))
    return JSONResponse(status_code=504 if timeout else 502, headers=headers, content={"error": error, "usage": usage.as_dict()})


def repair_instruction(previous: str) -> str:
    previous = str(previous or "")[-12000:]
    return (
        "FORMAT RECOVERY ONLY. Re-express the previous assistant output as exactly one JSON object. "
        "Preserve the same semantic fields, values and intended action; do not add new meaning, remove meaning, "
        "or explain anything. Follow the caller's requested response shape. No markdown. "
        f"Previous assistant output:\n{previous}"
    )


def _candidate_response(data: dict[str, Any], usage: UsageAccumulator) -> dict[str, Any]:
    # Return the original candidate content while applying all billed usage from
    # transport probes/format repair. Eyle will canonicalize or ask Main again.
    return usage.apply(copy.deepcopy(data))


async def execute_structured(client: httpx.AsyncClient, incoming: dict[str, Any], request_id: str) -> Response:
    # The client schema is transport guidance only. Adapter validates its syntax
    # in structured()/schema_for(), but never validates assistant semantics.
    if schema_for(incoming) is None:
        raise HTTPException(400, "Structured request sem schema/formato")
    usage = UsageAccumulator()
    attempts = 0
    repairs = 0
    try:
        model = await resolve_model(client, incoming)
    except Exception as exc:
        return _transport_failure(usage, 0, "model_discovery", exc, effective_mode=S.structured_mode)

    selected_data: dict[str, Any] | None = None
    selected_mode: str | None = None
    selected_steps: list[str] = []
    selected_errors: list[str] = []

    for mode in _structured_mode_chain(model):
        attempts += 1
        try:
            result = await call_once(client, incoming, request_id, attempts, structured_mode=mode, resolved_model=model)
        except httpx.TimeoutException as exc:
            return _transport_failure(usage, attempts, "adapter_timeout", exc, effective_mode=mode, timeout=True)
        except httpx.HTTPError as exc:
            return _transport_failure(usage, attempts, "adapter_transport", exc, effective_mode=mode)

        if result.data is None:
            if S.structured_mode == "auto" and mode != "prompt_json" and result.status_code in _TECHNICAL_MODE_REJECTION_STATUS:
                _record_transport_rejection(model, mode)
                log.info("request=%s provider_rejected_structured_transport mode=%s status=%s; trying weaker transport", request_id, mode, result.status_code)
                continue
            return Response(
                content=result.raw, status_code=result.status_code, media_type=result.media_type,
                headers=adapter_headers(usage, attempts, "provider_http", effective_mode=mode),
            )

        usage.add(result.data)
        selected_data = result.data
        selected_mode = mode
        value, errors, steps = normalize_structured(result.data, schema_for(incoming))
        selected_steps = steps
        selected_errors = errors
        if not errors:
            enforcement = "adapter_json_recovered" if steps else "adapter_json_valid"
            return JSONResponse(
                usage.apply(canonicalize(result.data, value)),
                headers=adapter_headers(usage, attempts, enforcement, repairs=0, normalized=bool(steps), effective_mode=mode),
            )

        # The provider accepted this transport; a malformed answer is not a
        # capability rejection. Never degrade/cache another mode for this.
        break

    if selected_data is None:
        # Auto chain exhausted only through technical rejections. Return the last
        # physical provider error is impossible here because each rejection was
        # consumed above; expose a transport-level diagnostic rather than an ECC error.
        return JSONResponse(
            status_code=502,
            headers=adapter_headers(usage, attempts, "structured_transport_unavailable", effective_mode=selected_mode or S.structured_mode),
            content={"error": {"type": "structured_transport_unavailable", "upstream_attempts": attempts}, "usage": usage.as_dict()},
        )

    # Optional cheap repair exists only for syntactically unrecoverable content.
    # It stays on the same already-accepted transport and never sees/interprets
    # ECC validation errors.
    repairs = 1
    repair = repair_instruction(assistant_content(selected_data))
    attempts += 1
    try:
        repaired = await call_once(
            client, incoming, request_id, attempts, repair=repair,
            structured_mode=selected_mode, resolved_model=model,
        )
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        headers = adapter_headers(
            usage, attempts, "adapter_repair_failed_candidate_returned", repairs=repairs,
            normalized=False, effective_mode=selected_mode,
        )
        headers["X-Eyle-Billing-May-Have-Occurred"] = "1"
        headers["X-Eyle-Retry-Cost-Risk"] = "1"
        return JSONResponse(_candidate_response(selected_data, usage), headers=headers)

    if repaired.data is not None:
        usage.add(repaired.data)
        value2, errors2, steps2 = normalize_structured(repaired.data, schema_for(incoming))
        if not errors2:
            return JSONResponse(
                usage.apply(canonicalize(repaired.data, value2)),
                headers=adapter_headers(
                    usage, attempts, "adapter_format_repaired", repairs=repairs,
                    normalized=True, effective_mode=selected_mode,
                ),
            )
        selected_errors = errors2

    # Never turn a format failure into a fatal 502 after a model generation.
    # Return the original semantic candidate and let Eyle's wire parser provide
    # precise cognitive feedback to the same Main.
    headers = adapter_headers(
        usage, attempts, "adapter_candidate_unparsed", repairs=repairs,
        normalized=bool(selected_steps), effective_mode=selected_mode,
    )
    headers["X-Eyle-Structured-Recovery-Error"] = (selected_errors[0] if selected_errors else "JSON recovery failed")[:240]
    return JSONResponse(_candidate_response(selected_data, usage), headers=headers)


async def read_body(request:Request)->dict[str,Any]:
    raw=await request.body()
    if len(raw)>S.max_body: raise HTTPException(413,"Requisição grande demais")
    try: data=json.loads(raw)
    except Exception as exc: raise HTTPException(400,"JSON inválido") from exc
    if not isinstance(data,dict): raise HTTPException(400,"Corpo precisa ser objeto JSON")
    return data


@asynccontextmanager
async def lifespan(app:FastAPI):
    check_config()
    app.state.http=httpx.AsyncClient(timeout=httpx.Timeout(connect=20,read=S.timeout,write=60,pool=20),follow_redirects=False)
    log.info(
        "Eyle provider-neutral adapter -> %s | structured=%s | cache=%s | env=%s",
        S.upstream_base_url, S.structured_mode, S.cache_mode, ENV_FILE,
    )
    if S.proxy_key and S.proxy_allow_loopback_no_auth:
        log.info("PROXY_API_KEY ativo para clientes remotos; localhost permanece liberado para a Eyle Core")
    try: yield
    finally: await app.state.http.aclose()


app=FastAPI(title="Eyle Provider-Neutral OpenAI Adapter",version=ADAPTER_VERSION,lifespan=lifespan)


@app.middleware("http")
async def advertise_transport_protocol(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Eyle-Adapter-Protocol"] = ADAPTER_TRANSPORT_PROTOCOL
    response.headers["X-Eyle-Adapter-Profile"] = ADAPTER_PROFILE
    return response


@app.get("/v1/eyle/handshake")
async def handshake(request: Request)->Response:
    """Formal Eyle<->Adapter transport negotiation with no paid generation.

    The handshake advertises only mechanical transport capabilities. It does
    not expose or validate any ECC/Memory semantics. A caller that declares an
    incompatible transport protocol receives HTTP 426 rather than discovering
    the mismatch during a paid generation.
    """
    client_auth(request)
    requested = str(request.headers.get("x-eyle-transport-protocol") or "").strip()
    if requested and requested != ADAPTER_TRANSPORT_PROTOCOL:
        return JSONResponse(status_code=426, content={
            "status": "incompatible",
            "handshake_schema": ADAPTER_HANDSHAKE_SCHEMA,
            "adapter_protocol": ADAPTER_TRANSPORT_PROTOCOL,
            "requested_protocol": requested,
            "error_code": "ADAPTER_PROTOCOL_INCOMPATIBLE",
        })
    explicit = S.model_override or (S.default_model if S.default_model.lower() != "auto" else "")
    return JSONResponse({
        "status": "ok",
        "handshake_schema": ADAPTER_HANDSHAKE_SCHEMA,
        "adapter_protocol": ADAPTER_TRANSPORT_PROTOCOL,
        "adapter_profile": ADAPTER_PROFILE,
        "adapter_version": ADAPTER_VERSION,
        "authority": "transport-only",
        "semantic_protocol": "client-owned",
        "endpoints": {
            "chat_completions": "/v1/chat/completions",
            "readiness": "/ready",
            "models": "/v1/models",
        },
        "capabilities": {
            "chat_completions": True,
            "client_json_schema_hint": True,
            "json_candidate_passthrough": True,
            "syntactic_json_recovery": True,
            "structured_modes": sorted(_ALLOWED_STRUCTURED),
            "structured_auto_policy": "degrade only on technical provider rejection",
            "usage_accounting": "best-effort-openai-usage",
            "cache_modes": sorted(_ALLOWED_CACHE),
        },
        "limits": {
            "max_request_bytes": int(S.max_body),
            "adapter_request_timeout_seconds": float(S.timeout),
            "max_upstream_attempts_per_logical_call": int(MAX_UPSTREAM_ATTEMPTS_PER_LOGICAL_CALL),
        },
        "provider": {
            "model_policy": "configured" if explicit else "discover",
            **({"configured_model": explicit} if explicit else {}),
            "structured_mode": S.structured_mode,
            "cache_mode": S.cache_mode,
        },
    })


@app.get("/")
@app.get("/health")
async def health()->dict[str,Any]:
    return {
        "status":"ok","upstream":S.upstream_base_url,"model":S.model_override or S.default_model,
        "adapter_profile":ADAPTER_PROFILE,"adapter_protocol":ADAPTER_TRANSPORT_PROTOCOL,"handshake_schema":ADAPTER_HANDSHAKE_SCHEMA,"semantic_protocol":"client-owned",
        "structured_upstream_mode":S.structured_mode,"structured_auto_policy":"native_json_schema -> json_object -> prompt_json; degrade/cache only on technical provider rejection","cache_mode":S.cache_mode,
        "supported_structured_modes":sorted(_ALLOWED_STRUCTURED),
        "cache_warmup":"POST /v1/eyle/cache/warmup; explicit/session require configured provider-specific cache knobs",
        "openai_base_url":f"http://{S.host}:{S.port}/v1",
        "env_file":str(ENV_FILE),
        "env_file_exists":ENV_FILE.exists(),
        "proxy_auth":("remote_only" if S.proxy_key and S.proxy_allow_loopback_no_auth else "required" if S.proxy_key else "disabled"),
    }


@app.get("/ready")
async def ready(request:Request)->Response:
    """Verify configuration without forcing providers to implement GET /models."""
    client_auth(request)
    explicit=S.model_override or (S.default_model if S.default_model.lower()!="auto" else "")
    if explicit:
        return JSONResponse({
            "status":"ready_configured",
            "upstream":S.upstream_base_url,
            "model":explicit,
            "note":"Modelo configurado explicitamente; nenhuma chamada paga foi feita ao provider.",
        })

    client: httpx.AsyncClient=request.app.state.http
    try:
        ids=await discover_models(client,force=True)
        return JSONResponse({"status":"ready","upstream":S.upstream_base_url,"models":ids})
    except Exception as exc:
        return JSONResponse(status_code=503,content={
            "status":"not_ready",
            "upstream":S.upstream_base_url,
            "error":_safe_transport_detail(exc),
            "hint":"Verifique UPSTREAM_BASE_URL/API key. Se o provider não expõe /models, configure DEFAULT_MODEL com o ID real.",
        })


@app.get("/v1/models")
async def models(request:Request)->Response:
    client_auth(request)
    # Se um modelo foi configurado explicitamente, a Eyle não precisa que o
    # provider exponha GET /models. Isso mantém o Adapter realmente agnóstico:
    # ele anuncia localmente o modelo configurado e usa a API remota somente
    # quando houver uma geração real.
    explicit = S.model_override or (S.default_model if S.default_model.lower() != "auto" else "")
    if explicit:
        return JSONResponse(
            {"object":"list","data":[{"id":explicit,"object":"model","created":0,"owned_by":"configured"}]},
            headers={"X-Eyle-Model-Discovery":"configured"},
        )

    client: httpx.AsyncClient=request.app.state.http
    try:
        ids = await discover_models(client)
    except Exception as exc:
        detail = _safe_transport_detail(exc)
        return JSONResponse(status_code=502, content={"error":{
            "type":"model_discovery_failed",**detail,
            "hint":"Configure UPSTREAM_BASE_URL/API key. Se o provider não expõe /models, defina DEFAULT_MODEL com o ID real do modelo.",
        }})
    return JSONResponse({"object":"list","data":[{"id":item,"object":"model","created":0,"owned_by":"upstream"} for item in ids]}, headers={"X-Eyle-Model-Discovery":"upstream"})


@app.post("/v1/eyle/cache/warmup")
async def cache_warmup(request:Request)->Response:
    """Optional provider-level prefix priming. Never called by Core automatically."""
    client_auth(request)
    incoming=await read_body(request)
    client: httpx.AsyncClient=request.app.state.http
    if S.cache_mode == "none":
        raise HTTPException(409,"Cache warmup desativado por UPSTREAM_CACHE_MODE=none")
    try:
        model = await resolve_model(client, incoming)
        result=await call_once(client,incoming,str(uuid.uuid4()),1,resolved_model=model)
    except httpx.HTTPError as exc:
        raise HTTPException(502,f"Falha no warmup: {type(exc).__name__}: {str(exc)[:200]}") from exc
    except Exception as exc:
        raise HTTPException(502,f"Falha no warmup/model discovery: {type(exc).__name__}: {str(exc)[:200]}") from exc
    if result.data is None: return Response(content=result.raw,status_code=result.status_code,media_type=result.media_type)
    usage=UsageAccumulator(); usage.add(result.data)
    return JSONResponse({"status":"ok","cache_mode":S.cache_mode,"usage":usage.as_dict()})


@app.post("/v1/chat/completions")
@app.post("/chat/completions",include_in_schema=False)
async def chat(request:Request)->Response:
    client_auth(request)
    incoming=await read_body(request)
    request_id=str(uuid.uuid4())
    is_structured=structured(incoming)
    stream=bool(incoming.get("stream"))
    client: httpx.AsyncClient=request.app.state.http
    if is_structured and stream: raise HTTPException(400,"Validação estruturada requer stream=false")
    if is_structured: return await execute_structured(client,incoming,request_id)
    try:
        resolved_model = await resolve_model(client, incoming)
    except Exception as exc:
        return _transport_failure(UsageAccumulator(),0,"model_discovery",exc)
    body,headers,_=_prepare_upstream(incoming,resolved_model=resolved_model)
    url=f"{S.upstream_base_url}/chat/completions"
    try:
        if stream:
            req=client.build_request("POST",url,headers=headers,json=body)
            upstream=await client.send(req,stream=True)
            if upstream.status_code>=400:
                content=await upstream.aread(); status=upstream.status_code; media=upstream.headers.get("content-type","application/json"); await upstream.aclose()
                return Response(content=content,status_code=status,media_type=media)
            return StreamingResponse(upstream.aiter_raw(),status_code=upstream.status_code,media_type=upstream.headers.get("content-type","text/event-stream"),headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"},background=BackgroundTask(upstream.aclose))
        result=await call_once(client,incoming,request_id,1,resolved_model=resolved_model)
        if result.data is None: return Response(content=result.raw,status_code=result.status_code,media_type=result.media_type)
        return JSONResponse(result.data)
    except httpx.TimeoutException as exc:
        return _transport_failure(UsageAccumulator(),1,"adapter_timeout",exc,timeout=True)
    except httpx.HTTPError as exc:
        return _transport_failure(UsageAccumulator(),1,"adapter_transport",exc)


if __name__=="__main__":
    uvicorn.run(app,host=S.host,port=S.port,reload=False,log_level=os.getenv("LOG_LEVEL","info").lower())
