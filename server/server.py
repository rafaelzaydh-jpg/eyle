"""Simple DeepSeek V4 transport adapter for Eyle Rev3.7.5.1.

The Adapter does only provider-boundary work: connect/authenticate, translate the
current local request to the one configured DeepSeek model, recover JSON syntax
mechanically, validate the caller-supplied JSON Schema, permit one format-only
repair, and report provider transport/usage facts.

It does not own ECC, Memory, Task, tool semantics, planning, relevance, capability
negotiation or Eyle execution policy.
"""
from __future__ import annotations

import copy
import hmac
import ipaddress
import json
import logging
import os
import re
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
# Preserve the old Adapter ergonomics: normal dotenv discovery works, while a
# server-local .env remains supported for the bundled launcher.
load_dotenv(override=False)
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("eyle-deepseek-adapter")


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on", "sim"}


@dataclass(frozen=True)
class Settings:
    provider_profile: str
    upstream_base_url: str
    upstream_api_key: str
    model: str
    host: str
    port: int
    timeout: float
    max_body: int
    proxy_key: str | None
    proxy_allow_loopback_no_auth: bool


S = Settings(
    provider_profile=os.getenv("PROVIDER_PROFILE", "deepseek_v4").strip().lower(),
    upstream_base_url=os.getenv("UPSTREAM_BASE_URL", "https://api.deepseek.com").strip().rstrip("/"),
    upstream_api_key=(os.getenv("UPSTREAM_API_KEY") or "").strip(),
    model=(os.getenv("MODEL") or "deepseek-v4-flash").strip(),
    host=os.getenv("HOST", "127.0.0.1").strip(),
    port=int(os.getenv("PORT", "8080")),
    timeout=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "1800")),
    max_body=int(os.getenv("MAX_REQUEST_BYTES", str(10 * 1024 * 1024))),
    proxy_key=os.getenv("PROXY_API_KEY", "").strip() or None,
    proxy_allow_loopback_no_auth=env_bool("PROXY_ALLOW_LOOPBACK_NO_AUTH", True),
)

ADAPTER_VERSION = "2.7.5-rev3.7.5.1"
ADAPTER_PROFILE = "eyle-deepseek-v4-simple-wire-v3"
ADAPTER_TRANSPORT_PROTOCOL = "eyle-adapter-transport-v2"
PROVIDER_PROFILE = "deepseek_v4"
STRUCTURED_UPSTREAM_MODE = "json_object"
MAX_UPSTREAM_ATTEMPTS_PER_LOGICAL_CALL = 2  # one generation + at most one format repair


def check_config() -> None:
    if S.provider_profile != PROVIDER_PROFILE:
        raise RuntimeError(
            f"PROVIDER_PROFILE inválido: {S.provider_profile!r}. "
            f"Esta revisão implementa somente {PROVIDER_PROFILE!r}."
        )
    if not S.upstream_base_url:
        raise RuntimeError("UPSTREAM_BASE_URL não configurada")
    if not S.model:
        raise RuntimeError("MODEL não configurado")
    if S.timeout <= 0:
        raise RuntimeError("REQUEST_TIMEOUT_SECONDS deve ser positivo")
    if S.max_body <= 0:
        raise RuntimeError("MAX_REQUEST_BYTES deve ser positivo")


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
    if S.proxy_allow_loopback_no_auth and _request_is_loopback(request):
        return
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    supplied = bearer or request.headers.get("x-api-key", "").strip()
    if not supplied or not hmac.compare_digest(supplied, S.proxy_key):
        raise HTTPException(401, "Chave do proxy inválida")


def model_for(_payload: dict[str, Any]) -> str:
    """Return the configured model. Incoming model IDs never trigger discovery."""
    return S.model


def schema_for(payload: dict[str, Any]) -> dict[str, Any] | None:
    fmt = payload.get("response_format")
    if not isinstance(fmt, dict):
        return None
    kind = str(fmt.get("type") or "").strip()
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
    if not isinstance(fmt, dict):
        raise HTTPException(400, "response_format inválido")
    kind = str(fmt.get("type") or "").strip()
    if kind == "text":
        return False
    if kind not in {"json_object", "json_schema"}:
        raise HTTPException(400, "response_format inválido para o Adapter DeepSeek")
    schema = schema_for(payload)
    if schema is None:
        raise HTTPException(400, "response_format estruturado sem schema/formato válido")
    if kind == "json_schema":
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise HTTPException(400, f"JSON Schema inválido: {exc.message}") from exc
    return True


def _schema_instruction(schema: dict[str, Any]) -> str:
    """Describe only the caller-supplied representation contract to DeepSeek.

    The Adapter is intentionally ignorant of Eyle semantics. It transports the
    schema it received instead of maintaining a second ECC/Memory prompt.
    """
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (
        "PROVIDER OUTPUT CONTRACT (representation only): "
        "Return exactly one JSON object that validates against the JSON Schema below. "
        "Treat JSON and field names elsewhere in the conversation as input/context, not as an output template. "
        "Do not copy input keys unless the output schema permits them. "
        "No markdown, code fences, commentary, or text outside the JSON object. "
        "JSON_SCHEMA=" + encoded
    )


def _attach_schema_instruction(
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
) -> list[dict[str, Any]]:
    """Attach one provider-facing wire contract without duplicating Eyle semantics."""
    out = copy.deepcopy(messages)
    instruction = _schema_instruction(schema)
    if out and isinstance(out[0], dict) and out[0].get("role") == "system":
        first = copy.deepcopy(out[0])
        first["content"] = str(first.get("content") or "").rstrip() + "\n\n" + instruction
        out[0] = first
    else:
        out.insert(0, {"role": "system", "content": instruction})
    return out


def _repair_messages(
    schema: dict[str, Any],
    previous: str,
    errors: list[str],
) -> list[dict[str, str]]:
    """Build a representation-only retry without replaying Eyle's whole context."""
    compact_errors = "; ".join(str(item)[:400] for item in errors[:8])
    return [
        {"role": "system", "content": _schema_instruction(schema)},
        {"role": "assistant", "content": str(previous or "")},
        {
            "role": "user",
            "content": (
                "FORMAT REPAIR ONLY. Re-emit the same intended content as exactly one JSON object "
                "valid against the provider output contract above. Do not reconsider the task, "
                "change the intended decision, add analysis, or copy unrelated input fields. "
                f"Validation errors: {compact_errors or 'invalid structured representation'}."
            ),
        },
    ]


def _reasoning_override(mode: Any, *, repair: bool = False) -> dict[str, Any]:
    """Translate Eyle's stable reasoning switch directly to DeepSeek V4."""
    if repair:
        return {"thinking": {"type": "disabled"}}
    normalized = str(mode or "provider_default").strip().lower()
    if normalized in {"", "provider_default"}:
        return {}
    if normalized == "off":
        return {"thinking": {"type": "disabled"}}
    if normalized == "on":
        return {"thinking": {"type": "enabled"}}
    raise HTTPException(400, "reasoning_mode inválido")


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return int(value)
    return None


def prepare_upstream(
    payload: dict[str, Any],
    *,
    repair_candidate: str | None = None,
    repair_errors: list[str] | None = None,
    completion_cap: int | None = None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any] | None]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(400, "messages inválido")

    is_structured = structured(payload)
    schema = schema_for(payload) if is_structured else None
    body = copy.deepcopy(payload)
    reasoning_mode = body.pop("reasoning_mode", "provider_default")
    requested_cap = _positive_int(body.pop("max_completion_tokens", None))
    if "max_tokens" in body:
        raise HTTPException(400, "max_tokens não faz parte do wire canônico; use max_completion_tokens")
    cap = _positive_int(completion_cap) or requested_cap
    # MODEL is configured once in the Adapter; no request can make us discover or
    # silently switch providers/models.
    body["model"] = model_for(payload)
    if cap is not None:
        body["max_tokens"] = cap

    is_repair = repair_candidate is not None
    if schema is not None:
        body["messages"] = (
            _repair_messages(schema, repair_candidate or "", repair_errors or [])
            if is_repair
            else _attach_schema_instruction(messages, schema)
        )
        body["response_format"] = {"type": "json_object"}
    else:
        body["messages"] = copy.deepcopy(messages)
        if isinstance(body.get("response_format"), dict) and body["response_format"].get("type") == "text":
            body.pop("response_format", None)

    if is_repair:
        body["temperature"] = 0

    # The DeepSeek V4 profile owns this translation mechanically.
    body.pop("thinking", None)
    body.update(_reasoning_override(reasoning_mode, repair=is_repair))

    if bool(body.get("stream")):
        stream_options = body.get("stream_options") if isinstance(body.get("stream_options"), dict) else {}
        stream_options = copy.deepcopy(stream_options)
        stream_options["include_usage"] = True
        body["stream_options"] = stream_options

    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if S.upstream_api_key:
        headers["Authorization"] = f"Bearer {S.upstream_api_key}"
    return body, headers, schema


@dataclass
class UsageAccumulator:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0
    cache_miss_tokens: int = 0
    reasoning_tokens: int = 0
    usage_calls: int = 0
    provider_total_calls: int = 0

    def add(self, data: dict[str, Any]) -> bool:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return False
        prompt_raw = usage.get("prompt_tokens")
        completion_raw = usage.get("completion_tokens")
        reported_total = usage.get("total_tokens")
        total_known = isinstance(reported_total, (int, float)) and not isinstance(reported_total, bool)
        components_known = (
            isinstance(prompt_raw, (int, float)) and not isinstance(prompt_raw, bool)
            and isinstance(completion_raw, (int, float)) and not isinstance(completion_raw, bool)
        )
        if not total_known and not components_known:
            return False
        prompt = max(0, int(prompt_raw or 0)) if isinstance(prompt_raw, (int, float)) and not isinstance(prompt_raw, bool) else 0
        completion = max(0, int(completion_raw or 0)) if isinstance(completion_raw, (int, float)) and not isinstance(completion_raw, bool) else 0
        if total_known:
            total = max(0, int(reported_total))
            self.provider_total_calls += 1
        else:
            total = prompt + completion

        hit = usage.get("prompt_cache_hit_tokens")
        miss = usage.get("prompt_cache_miss_tokens")
        prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
        hit_candidates = [hit, prompt_details.get("cached_tokens")]
        hit_values = [int(v) for v in hit_candidates if isinstance(v, (int, float)) and not isinstance(v, bool) and int(v) >= 0]
        if hit_values:
            cached = min(prompt, max(hit_values))
            cache_miss = max(0, prompt - cached)
        elif isinstance(miss, (int, float)) and not isinstance(miss, bool) and int(miss) >= 0:
            cache_miss = min(prompt, int(miss))
            cached = max(0, prompt - cache_miss)
        else:
            cached = 0
            cache_miss = prompt

        completion_details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
        reasoning = completion_details.get("reasoning_tokens")

        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.cached_prompt_tokens += cached
        self.cache_miss_tokens += cache_miss
        self.reasoning_tokens += max(0, int(reasoning or 0)) if isinstance(reasoning, (int, float)) else 0
        self.usage_calls += 1
        return True

    @property
    def provider_total_authoritative(self) -> bool:
        return self.usage_calls > 0 and self.provider_total_calls == self.usage_calls

    def as_dict(self) -> dict[str, Any]:
        if self.usage_calls <= 0:
            return {}
        out: dict[str, Any] = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "prompt_cache_hit_tokens": min(self.prompt_tokens, self.cached_prompt_tokens),
            "prompt_cache_miss_tokens": min(self.prompt_tokens, self.cache_miss_tokens),
        }
        if self.reasoning_tokens:
            out["completion_tokens_details"] = {"reasoning_tokens": self.reasoning_tokens}
        return out

    def apply(self, data: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(data)
        if self.usage_calls > 0:
            result["usage"] = self.as_dict()
        return result


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


def assistant_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    return text_of(message.get("content")) if isinstance(message, dict) else ""


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
    quote: str | None = None
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
        elif char in "}]" and stack and stack[-1] == pairs[char]:
            stack.pop()
            if not stack:
                return text[start:index + 1]
    return None


def _decode_jsonish(candidate: str) -> Any:
    """Decode JSON syntax only; never accept Python literals or semantic aliases."""
    value = json.loads(candidate)
    if isinstance(value, str):
        nested = value.strip()
        if nested.startswith(("{", "[")):
            value = json.loads(nested)
    return value


def parse_json_value(text: str) -> tuple[Any, list[str]]:
    raw = str(text or "").strip()
    candidates: list[tuple[str, str]] = []
    if raw:
        candidates.append(("direct_json", raw))
    fence = _FENCE_RE.match(raw)
    if fence:
        candidates.append(("fence_removed", fence.group(1).strip()))
    fragment = _balanced_json_fragment(raw)
    if fragment and fragment != raw:
        candidates.append(("balanced_fragment", fragment))

    seen: set[str] = set()
    for step, candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            value = _decode_jsonish(candidate)
            return value, ([] if step == "direct_json" else [step])
        except ValueError:
            continue
    raise ValueError("assistant content does not contain recoverable JSON")


def _schema_errors(value: Any, schema: dict[str, Any], limit: int = 8) -> list[str]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(value), key=lambda err: list(err.absolute_path))
    out: list[str] = []
    for error in errors[:limit]:
        path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path)
        out.append(f"{path}: {error.message}")
    return out


def normalize_structured(data: dict[str, Any], schema: dict[str, Any]) -> tuple[Any | None, list[str], list[str]]:
    """Recover JSON syntax and validate the caller-supplied generic schema only."""
    text = assistant_content(data)
    if not text.strip():
        return None, ["$: assistant content is empty"], []
    try:
        value, steps = parse_json_value(text)
    except ValueError as exc:
        return None, [f"$: JSON is not recoverable ({exc})"], []
    errors = _schema_errors(value, schema)
    return value, errors, steps


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


def _remaining_completion_cap(incoming: dict[str, Any], usage: UsageAccumulator) -> int | None:
    raw = _positive_int(incoming.get("max_completion_tokens"))
    if raw is None:
        return None
    return max(0, raw - usage.completion_tokens)


def _effective_completion_cap(incoming: dict[str, Any], usage: UsageAccumulator) -> int | None:
    # Enforce only the caller-provided output ceiling for this logical Adapter
    # call. Eyle's global execution/provider budget remains Runtime-owned.
    return _remaining_completion_cap(incoming, usage)


@dataclass(frozen=True)
class AttemptResult:
    data: dict[str, Any] | None
    status_code: int
    media_type: str
    raw: bytes


async def call_once(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    request_id: str,
    attempt_no: int,
    *,
    repair_candidate: str | None = None,
    repair_errors: list[str] | None = None,
    completion_cap: int | None = None,
) -> AttemptResult:
    body, headers, _ = prepare_upstream(
        payload,
        repair_candidate=repair_candidate,
        repair_errors=repair_errors,
        completion_cap=completion_cap,
    )
    url = f"{S.upstream_base_url}/chat/completions"
    log.info(
        "request=%s upstream_attempt=%s model=%s structured=%s repair=%s",
        request_id,
        attempt_no,
        body.get("model"),
        bool(schema_for(payload)),
        bool(repair_candidate is not None),
    )
    response = await client.post(url, headers=headers, json=body)
    media = response.headers.get("content-type", "application/json")
    if response.status_code >= 400:
        log.warning("request=%s upstream_attempt=%s http=%s", request_id, attempt_no, response.status_code)
        return AttemptResult(None, response.status_code, media, response.content)
    try:
        data = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        log.error("request=%s upstream_attempt=%s invalid_json_envelope", request_id, attempt_no)
        return AttemptResult(None, 502, media, response.content)
    if not isinstance(data, dict):
        return AttemptResult(None, 502, media, response.content)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    log.info(
        "request=%s upstream_attempt=%s ok prompt=%s completion=%s total=%s",
        request_id,
        attempt_no,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
    )
    return AttemptResult(data, response.status_code, media, response.content)


def adapter_headers(
    usage: UsageAccumulator,
    attempts: int,
    enforcement: str,
    *,
    repairs: int = 0,
    normalized: bool = False,
    usage_unknown: bool = False,
    billing_may_have_occurred: bool = False,
    retry_cost_risk: bool = False,
) -> dict[str, str]:
    headers = {
        "X-Eyle-Adapter-Profile": ADAPTER_PROFILE,
        "X-Eyle-Adapter-Protocol": ADAPTER_TRANSPORT_PROTOCOL,
        "X-Eyle-Structured-Upstream-Mode": STRUCTURED_UPSTREAM_MODE,
        "X-Eyle-Structured-Configured-Mode": STRUCTURED_UPSTREAM_MODE,
        "X-Eyle-Schema-Enforcement": enforcement,
        "X-Eyle-Upstream-Attempts": str(max(0, attempts)),
        "X-Eyle-Max-Upstream-Attempts": str(MAX_UPSTREAM_ATTEMPTS_PER_LOGICAL_CALL),
        "X-Eyle-Structured-Repairs": str(max(0, repairs)),
        "X-Eyle-Local-Normalized": "1" if normalized else "0",
        "X-Eyle-Upstream-Usage-Unknown": "1" if usage_unknown else "0",
        "X-Eyle-Billing-May-Have-Occurred": "1" if billing_may_have_occurred else "0",
        "X-Eyle-Retry-Cost-Risk": "1" if retry_cost_risk else "0",
        "X-Eyle-Usage-Source": (
            "provider_total_tokens" if usage.provider_total_authoritative
            else ("provider_prompt_plus_completion_fallback" if usage.usage_calls else "unknown")
        ),
    }
    if usage.usage_calls:
        headers.update({
            "X-Eyle-Usage-Prompt-Tokens": str(usage.prompt_tokens),
            "X-Eyle-Usage-Completion-Tokens": str(usage.completion_tokens),
            "X-Eyle-Usage-Total-Tokens": str(usage.total_tokens),
            "X-Eyle-Usage-Cached-Prompt-Tokens": str(min(usage.prompt_tokens, usage.cached_prompt_tokens)),
        })
    return headers


def _transport_failure(
    usage: UsageAccumulator,
    attempts: int,
    exc: BaseException,
    *,
    timeout: bool = False,
    repairs: int = 0,
) -> JSONResponse:
    if timeout:
        error_type = "upstream_timeout"
        status = 504
        message = "Timeout aguardando o DeepSeek; a geração pode ter sido processada/cobrada sem usage retornado."
        risk = True
    else:
        error_type = "upstream_connection_error"
        status = 502
        message = f"Falha de transporte ao conectar ao DeepSeek: {type(exc).__name__}: {str(exc)[:240]}"
        # ConnectError happens before a usable upstream connection; other request
        # failures can occur after bytes crossed the network boundary.
        risk = not isinstance(exc, httpx.ConnectError)
    return JSONResponse(
        status_code=status,
        headers=adapter_headers(
            usage,
            attempts,
            "adapter_timeout" if timeout else "adapter_transport",
            repairs=repairs,
            usage_unknown=risk,
            billing_may_have_occurred=risk,
            retry_cost_risk=risk,
        ),
        content={
            "error": {
                "type": error_type,
                "message": message,
                "billing_may_have_occurred": risk,
                "provider_usage_unknown": risk,
                "upstream_attempts": attempts,
                "repairs": repairs,
            },
            "usage": usage.as_dict(),
        },
    )


def _repair_candidate_text(value: Any, raw: str) -> str:
    """Compact only mechanically recovered representation; never semantic content."""
    if value is not None:
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError):
            pass
    return str(raw or "")


def _finish_reason(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    return str(choices[0].get("finish_reason") or "").strip().lower()


def _candidate_response(data: dict[str, Any], usage: UsageAccumulator) -> dict[str, Any]:
    return usage.apply(copy.deepcopy(data))


def _structured_headers(
    schema: dict[str, Any],
    usage: UsageAccumulator,
    attempts: int,
    enforcement: str,
    **kwargs: Any,
) -> dict[str, str]:
    """Add boundary-only observability without leaking schema semantics into Core."""
    headers = adapter_headers(usage, attempts, enforcement, **kwargs)
    headers["X-Eyle-Structured-Contract-Characters"] = str(len(_schema_instruction(schema)))
    headers["X-Eyle-Repair-Context-Mode"] = "isolated" if int(kwargs.get("repairs") or 0) > 0 else "none"
    return headers


async def execute_structured(client: httpx.AsyncClient, incoming: dict[str, Any], request_id: str) -> Response:
    schema = schema_for(incoming)
    if schema is None:
        raise HTTPException(400, "Structured request sem schema/formato")

    usage = UsageAccumulator()
    first_cap = _effective_completion_cap(incoming, usage)
    if first_cap == 0:
        return JSONResponse(
            status_code=429,
            headers=_structured_headers(schema, usage, 0, "provider_budget_exhausted"),
            content={"error": {"type": "provider_budget_exhausted"}},
        )

    try:
        first = await call_once(client, incoming, request_id, 1, completion_cap=first_cap)
    except httpx.TimeoutException as exc:
        return _transport_failure(usage, 1, exc, timeout=True)
    except httpx.RequestError as exc:
        return _transport_failure(usage, 1, exc)

    if first.data is None:
        return Response(
            content=first.raw,
            status_code=first.status_code,
            media_type=first.media_type,
            headers=_structured_headers(schema, usage, 1, "provider_http"),
        )

    first_has_usage = usage.add(first.data)
    value, errors, steps = normalize_structured(first.data, schema)
    if not errors:
        return JSONResponse(
            usage.apply(canonicalize(first.data, value)),
            headers=_structured_headers(
                schema,
                usage,
                1,
                "adapter_json_recovered" if steps else "adapter_json_valid",
                normalized=bool(steps),
            ),
        )

    if not first_has_usage:
        headers = _structured_headers(
            schema, usage, 1, "adapter_candidate_usage_unknown",
            normalized=bool(steps), usage_unknown=True,
            billing_may_have_occurred=True, retry_cost_risk=True,
        )
        headers["X-Eyle-Structured-Recovery-Error"] = errors[0][:240]
        return JSONResponse(_candidate_response(first.data, usage), headers=headers)

    repair_cap = _effective_completion_cap(incoming, usage)
    if repair_cap == 0:
        headers = _structured_headers(
            schema, usage, 1, "adapter_candidate_provider_budget_exhausted",
            normalized=bool(steps),
        )
        headers["X-Eyle-Structured-Recovery-Error"] = errors[0][:240]
        return JSONResponse(_candidate_response(first.data, usage), headers=headers)

    if _finish_reason(first.data) == "length":
        headers = _structured_headers(
            schema, usage, 1, "adapter_output_truncated", normalized=bool(steps),
        )
        headers["X-Eyle-Structured-Recovery-Error"] = "provider finish_reason=length"
        return JSONResponse(_candidate_response(first.data, usage), headers=headers)

    try:
        second = await call_once(
            client, incoming, request_id, 2,
            repair_candidate=_repair_candidate_text(value, assistant_content(first.data)),
            repair_errors=errors,
            completion_cap=repair_cap,
        )
    except httpx.TimeoutException as exc:
        return _transport_failure(usage, 2, exc, timeout=True, repairs=1)
    except httpx.RequestError as exc:
        return _transport_failure(usage, 2, exc, repairs=1)

    if second.data is None:
        return Response(
            content=second.raw,
            status_code=second.status_code,
            media_type=second.media_type,
            headers=_structured_headers(schema, usage, 2, "provider_http_repair", repairs=1),
        )

    second_has_usage = usage.add(second.data)
    value2, errors2, steps2 = normalize_structured(second.data, schema)
    if not errors2:
        return JSONResponse(
            usage.apply(canonicalize(second.data, value2)),
            headers=_structured_headers(
                schema, usage, 2, "adapter_format_repaired",
                repairs=1, normalized=True,
                usage_unknown=not second_has_usage,
                billing_may_have_occurred=not second_has_usage,
                retry_cost_risk=not second_has_usage,
            ),
        )

    headers = _structured_headers(
        schema, usage, 2, "adapter_structured_invalid_after_repair",
        repairs=1, normalized=bool(steps or steps2),
        usage_unknown=not second_has_usage,
        billing_may_have_occurred=not second_has_usage,
        retry_cost_risk=not second_has_usage,
    )
    headers["X-Eyle-Structured-Recovery-Error"] = errors2[0][:240]
    return JSONResponse(_candidate_response(second.data, usage), headers=headers)


async def read_body(request: Request) -> dict[str, Any]:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > S.max_body:
                raise HTTPException(413, "Requisição grande demais")
        except ValueError as exc:
            raise HTTPException(400, "Content-Length inválido") from exc

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > S.max_body:
            raise HTTPException(413, "Requisição grande demais")
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "JSON inválido") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "Corpo precisa ser objeto JSON")
    return data


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_config()
    timeout = httpx.Timeout(connect=20, read=S.timeout, write=60, pool=20)
    app.state.http = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    log.info(
        "Eyle simple DeepSeek Adapter -> %s | model=%s | profile=%s | attempts=%s",
        S.upstream_base_url,
        S.model,
        S.provider_profile,
        MAX_UPSTREAM_ATTEMPTS_PER_LOGICAL_CALL,
    )
    if not S.upstream_api_key:
        log.warning("UPSTREAM_API_KEY vazia; /ready ficará not_ready até a chave ser configurada")
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(title="Eyle Simple DeepSeek Adapter", version=ADAPTER_VERSION, lifespan=lifespan)


@app.middleware("http")
async def advertise_transport_protocol(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Eyle-Adapter-Protocol"] = ADAPTER_TRANSPORT_PROTOCOL
    response.headers["X-Eyle-Adapter-Profile"] = ADAPTER_PROFILE
    return response


@app.get("/")
@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    client_auth(request)
    return {
        "status": "ok",
        "adapter_version": ADAPTER_VERSION,
        "adapter_profile": ADAPTER_PROFILE,
        "adapter_protocol": ADAPTER_TRANSPORT_PROTOCOL,
        "provider_profile": S.provider_profile,
        "model": S.model,
        "structured_repair_attempts": 1,
    }


@app.get("/ready")
async def ready(request: Request) -> Response:
    """Local config readiness only; never calls DeepSeek."""
    client_auth(request)
    if not S.upstream_api_key:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "error_code": "UPSTREAM_API_KEY_REQUIRED",
                "hint": "Configure UPSTREAM_API_KEY.",
            },
        )
    return JSONResponse({
        "status": "ready_configured",
        "provider_profile": S.provider_profile,
        "model": S.model,
        "note": "Configuração local válida; nenhuma chamada ao provider foi feita.",
    })


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
        raise HTTPException(400, "Structured output requer stream=false")
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

        result = await call_once(client, incoming, request_id, 1, completion_cap=_positive_int(incoming.get("max_completion_tokens")))
        if result.data is None:
            return Response(content=result.raw, status_code=result.status_code, media_type=result.media_type)
        usage = UsageAccumulator()
        usage.add(result.data)
        return JSONResponse(result.data, headers=adapter_headers(usage, 1, "provider_passthrough"))
    except httpx.TimeoutException as exc:
        return _transport_failure(UsageAccumulator(), 1, exc, timeout=True)
    except httpx.RequestError as exc:
        return _transport_failure(UsageAccumulator(), 1, exc)


if __name__ == "__main__":
    uvicorn.run(app, host=S.host, port=S.port, reload=False, log_level=os.getenv("LOG_LEVEL", "info").lower())
