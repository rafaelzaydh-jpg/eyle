#!/usr/bin/env python3
"""Provider-neutral Eyle->Adapter transport for structured cognition."""
import hashlib
import json
import math
import random
import socket
import sys
import threading
import time
import urllib.request
import urllib.error
from contextlib import contextmanager
from typing import Any

from eyle.runtime.execution_context import ExecutionContext, current_execution  # noqa: E402
from eyle.runtime.token_budget import estimate_tokens as estimar_tokens  # noqa: E402
from eyle.runtime import telemetry  # noqa: E402
from eyle.runtime import limiter  # noqa: E402
from eyle.runtime import progress as job_progress  # noqa: E402
from llm.protocol import CanonicalPrompt, prompt_messages, provider_policy  # noqa: E402
from llm.response_adapter import (  # noqa: E402
    NormalizedModelResponse, ResponseEnvelopeError, normalize_openai_chat_response,
)
from llm.structured import (  # noqa: E402
    StructuredResponseError, json_schema_response_format,
    mandatory_top_level_keys, observed_top_level, parse_profile_response,
)


class ErroLLM(RuntimeError):
    """Falha de transporte/backend; nunca representa uma resposta do modelo."""

    def __init__(self, mensagem, *, transient=False, status_code=None,
                 retry_after=None, error_code=None, structured_error=None,
                 structured_observed=None):
        super().__init__(mensagem)
        self.transient = bool(transient)
        self.status_code = status_code
        self.retry_after = retry_after
        self.error_code = error_code
        self.structured_error = structured_error
        self.structured_observed = structured_observed


_SEMAFOROS_LLM = {}
_SEMAFOROS_LOCK = threading.Lock()
_COOLDOWN_ATE = {}
_COOLDOWN_LOCK = threading.Lock()
_LLM_RESPONSE_LOCAL = threading.local()
# Structured schemas and validation live in llm.structured.  This transport
# layer only chooses the empirically verified mechanism for the active connection.

ADAPTER_TRANSPORT_PROTOCOL = "eyle-adapter-transport-v2"
_ADAPTER_STATUS_CACHE: dict[str, dict[str, Any]] = {}
_ADAPTER_STATUS_TTL_SECONDS = 300.0


def _diagnostico(codigo, **campos):
    """Log curto e estruturado; nunca interfere no resultado da chamada."""
    payload = {"code": codigo, **campos}
    try:
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        encoded = json.dumps({"code": "DIAGNOSTIC_SERIALIZATION_FAILED", "original_code": str(codigo), "error": str(exc)}, ensure_ascii=False)
    print("[llm] " + encoded, file=sys.stderr)


def _adapter_root(base_url: str) -> str:
    base = str(base_url or "").rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _get_json(endpoint: str, timeout: float, *, protocol: bool = False):
    headers = {"Accept": "application/json"}
    if protocol:
        headers["X-Eyle-Transport-Protocol"] = ADAPTER_TRANSPORT_PROTOCOL
    req = urllib.request.Request(endpoint, headers=headers)
    with _abrir_url(req, timeout, timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        body = json.loads(raw)
        response_headers = getattr(resp, "headers", None)
    return body, response_headers


def _validate_adapter_health(body: Any) -> dict[str, Any]:
    """Validate only the current transport identity, not a capability catalog."""
    if not isinstance(body, dict) or str(body.get("status") or "") != "ok":
        raise ValueError("ADAPTER_HEALTH_INVALID")
    if body.get("adapter_protocol") != ADAPTER_TRANSPORT_PROTOCOL:
        raise ValueError("ADAPTER_PROTOCOL_INCOMPATIBLE")
    return body


def diagnosticar_backend(config, timeout=None):
    """Check local Adapter process/protocol and local provider configuration.

    This endpoint check does not pretend to prove remote provider connectivity.
    The real POST /chat/completions result is the authority for that fact.
    """
    cfg_llm = (config or {}).get("llm", {})
    base_url = str(cfg_llm.get("base_url") or "http://127.0.0.1:8080").rstrip("/")
    limite = timeout if timeout is not None else cfg_llm.get("adapter_status_timeout_seconds", 3)
    try:
        limite = max(0.1, min(float(limite), 10.0))
    except (TypeError, ValueError):
        limite = 3.0

    root = _adapter_root(base_url)
    health_endpoint = root + "/health"
    inicio = time.monotonic()
    try:
        health, _headers = _get_json(health_endpoint, limite, protocol=True)
        _validate_adapter_health(health)
    except urllib.error.HTTPError as erro:
        detalhe = _mensagem_http_error(base_url, erro, _ler_corpo_http_error(erro))
        return {
            "ok": False, "reachable": True, "base_url": base_url, "endpoint": health_endpoint,
            "error_code": "ADAPTER_HEALTH_HTTP_ERROR", "status_code": getattr(erro, "code", None),
            "detail": detalhe, "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as erro:
        return {
            "ok": False, "reachable": False, "base_url": base_url, "endpoint": health_endpoint,
            "error_code": "BACKEND_UNREACHABLE",
            "detail": f"Nao foi possivel acessar o Adapter em {health_endpoint}: {erro}",
            "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }
    except Exception as erro:
        return {
            "ok": False, "reachable": True, "base_url": base_url, "endpoint": health_endpoint,
            "error_code": str(erro) if str(erro).startswith("ADAPTER_") else "ADAPTER_HEALTH_INVALID",
            "detail": f"Adapter incompatível: {type(erro).__name__}: {erro}",
            "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }

    ready_endpoint = root + "/ready"
    try:
        ready, _ready_headers = _get_json(ready_endpoint, limite, protocol=True)
    except urllib.error.HTTPError as erro:
        detalhe = _mensagem_http_error(base_url, erro, _ler_corpo_http_error(erro))
        return {
            "ok": False, "reachable": True, "health_ok": True, "base_url": base_url,
            "endpoint": ready_endpoint, "health": health,
            "error_code": "ADAPTER_NOT_READY", "status_code": getattr(erro, "code", None),
            "detail": detalhe, "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }
    except Exception as erro:
        return {
            "ok": False, "reachable": True, "health_ok": True, "base_url": base_url,
            "endpoint": ready_endpoint, "health": health,
            "error_code": "ADAPTER_READINESS_ERROR",
            "detail": f"Adapter respondeu health, mas readiness falhou: {type(erro).__name__}: {erro}",
            "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }

    if not isinstance(ready, dict) or str(ready.get("status") or "") != "ready_configured":
        return {
            "ok": False, "reachable": True, "health_ok": True, "base_url": base_url,
            "endpoint": ready_endpoint, "health": health, "readiness": ready,
            "error_code": "ADAPTER_NOT_READY",
            "detail": "Adapter está vivo, mas sua configuração local não está pronta.",
            "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }

    model = str(ready.get("model") or health.get("model") or "").strip()
    return {
        "ok": True, "reachable": True, "health_ok": True, "base_url": base_url,
        "endpoint": ready_endpoint, "models": [model] if model else [], "model_count": 1 if model else 0,
        "adapter_protocol": health.get("adapter_protocol"),
        "adapter_profile": health.get("adapter_profile"),
        "adapter_version": health.get("adapter_version"),
        "health": health, "readiness": ready,
        "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
    }


def _ensure_adapter_ready(config) -> dict[str, Any]:
    cfg_llm = (config or {}).get("llm") or {}
    base_url = str(cfg_llm.get("base_url") or "http://127.0.0.1:8080").rstrip("/")
    now = time.monotonic()
    cached = _ADAPTER_STATUS_CACHE.get(base_url)
    if isinstance(cached, dict) and float(cached.get("expires_at") or 0) > now:
        return cached
    diag = diagnosticar_backend(config)
    if diag.get("ok") is not True:
        code = str(diag.get("error_code") or "ADAPTER_NOT_READY")
        raise ErroLLM(
            str(diag.get("detail") or "Adapter health/readiness failed."),
            transient=code in {"BACKEND_UNREACHABLE", "ADAPTER_NOT_READY", "ADAPTER_READINESS_ERROR"},
            status_code=diag.get("status_code"), error_code=code,
        )
    entry = {
        "expires_at": now + _ADAPTER_STATUS_TTL_SECONDS,
        "adapter_protocol": diag.get("adapter_protocol"),
        "adapter_profile": diag.get("adapter_profile"),
        "adapter_version": diag.get("adapter_version"),
    }
    _ADAPTER_STATUS_CACHE[base_url] = entry
    return entry


def _retry_after_seconds(erro):
    headers = getattr(erro, "headers", None)
    valor = headers.get("Retry-After") if headers is not None else None
    if valor is None:
        return None
    try:
        return max(0.0, float(valor))
    except (TypeError, ValueError):
        return None


def _erro_http(base_url, erro, corpo_erro=""):
    codigo = getattr(erro, "code", None)
    transitorio = codigo in (408, 425, 429, 500, 502, 503, 504)
    return ErroLLM(
        _mensagem_http_error(base_url, erro, corpo_erro),
        transient=transitorio,
        status_code=codigo,
        retry_after=_retry_after_seconds(erro),
        error_code="HTTP_TRANSIENT" if transitorio else "HTTP_PERMANENT",
    )


def _adapter_error_contract(erro, corpo_erro=""):
    """Recognize Eyle-adapter errors without treating every 5xx as retry-safe.

    A structured-contract 502 may already represent multiple billed upstream
    generations. An adapter upstream timeout may likewise have been processed by
    the model provider. Retrying either blindly can multiply token cost.
    """
    try:
        payload = json.loads(str(corpo_erro or ""))
    except (json.JSONDecodeError, TypeError):
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    error_obj = error if isinstance(error, dict) else {}
    error_type = str(error_obj.get("type") or "")
    usage = payload.get("usage") if isinstance(payload, dict) and isinstance(payload.get("usage"), dict) else {}
    headers = getattr(erro, "headers", None)

    def header_int(name):
        try:
            raw = headers.get(name) if headers is not None else None
            return int(raw) if raw is not None and str(raw).strip() else None
        except (TypeError, ValueError):
            return None

    prompt_tokens = header_int("X-Eyle-Usage-Prompt-Tokens")
    completion_tokens = header_int("X-Eyle-Usage-Completion-Tokens")
    total_tokens_header = header_int("X-Eyle-Usage-Total-Tokens")
    cached_tokens = header_int("X-Eyle-Usage-Cached-Prompt-Tokens")
    upstream_attempts = header_int("X-Eyle-Upstream-Attempts")
    structured_repairs = header_int("X-Eyle-Structured-Repairs")
    if prompt_tokens is None and isinstance(usage.get("prompt_tokens"), (int, float)):
        prompt_tokens = max(0, int(usage.get("prompt_tokens") or 0))
    if completion_tokens is None and isinstance(usage.get("completion_tokens"), (int, float)):
        completion_tokens = max(0, int(usage.get("completion_tokens") or 0))
    total_tokens = total_tokens_header
    if total_tokens is None and isinstance(usage.get("total_tokens"), (int, float)):
        total_tokens = usage.get("total_tokens")
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = int(prompt_tokens) + int(completion_tokens)
    if cached_tokens is None:
        raw_cached = usage.get("cached_prompt_tokens")
        if isinstance(raw_cached, (int, float)):
            cached_tokens = max(0, int(raw_cached))

    metadata = {}
    if prompt_tokens is not None or completion_tokens is not None:
        metadata = {
            "provider_usage_from_error": True,
            "prompt_tokens": max(0, int(prompt_tokens or 0)),
            "completion_tokens": max(0, int(completion_tokens or 0)),
            "total_tokens": max(0, int(total_tokens or 0)),
        }
        if cached_tokens is not None:
            metadata["cached_prompt_tokens"] = min(metadata["prompt_tokens"], max(0, int(cached_tokens)))
    if upstream_attempts is None and isinstance(error_obj.get("upstream_attempts"), (int, float)):
        upstream_attempts = max(0, int(error_obj.get("upstream_attempts") or 0))
    if structured_repairs is None and isinstance(error_obj.get("repairs"), (int, float)):
        structured_repairs = max(0, int(error_obj.get("repairs") or 0))
    validation_errors = error_obj.get("validation_errors")
    if isinstance(validation_errors, list):
        validation_errors = [str(item)[:500] for item in validation_errors[:8] if str(item).strip()]
    else:
        validation_errors = []
    if upstream_attempts is not None:
        metadata["adapter_upstream_attempts"] = upstream_attempts
    if structured_repairs is not None:
        metadata["adapter_structured_repairs"] = structured_repairs
    if validation_errors:
        metadata["adapter_validation_errors"] = validation_errors
    if headers is not None:
        profile = headers.get("X-Eyle-Adapter-Profile")
        enforcement = headers.get("X-Eyle-Schema-Enforcement")
        if profile:
            metadata["adapter_profile"] = str(profile)
        if enforcement:
            metadata["adapter_schema_enforcement"] = str(enforcement)
        metadata.update(_adapter_response_metadata(headers))
    billed_or_risky = bool(
        metadata.get("retry_cost_risk")
        or metadata.get("billing_may_have_occurred")
        or int(metadata.get("prompt_tokens") or 0) > 0
        or int(metadata.get("completion_tokens") or 0) > 0
        or bool(error_obj.get("retry_cost_risk"))
        or bool(error_obj.get("billing_may_have_occurred"))
    )
    if billed_or_risky:
        metadata["retry_cost_risk"] = True
        metadata["billing_may_have_occurred"] = True

    if error_type == "structured_contract_unsatisfied":
        detail = f" Validação: {validation_errors[0]}" if validation_errors else ""
        return ErroLLM(
            "O adaptador esgotou a recuperação estruturada no upstream." + detail,
            transient=False, status_code=getattr(erro, "code", None),
            error_code="LLM_STRUCTURED_RESPONSE_UNSATISFIED",
        ), metadata
    if error_type == "upstream_timeout":
        return ErroLLM(
            "O adaptador excedeu o timeout aguardando o provider; a geração pode ter sido processada/cobrada.",
            transient=False, status_code=getattr(erro, "code", None),
            error_code="READ_TIMEOUT",
        ), metadata
    if error_type == "upstream_connection_error":
        detail = str(error_obj.get("detail") or "")[:240]
        suffix = f" Detalhe: {detail}" if detail else ""
        return ErroLLM(
            "O adaptador não conseguiu conectar ao provider." + suffix,
            transient=not billed_or_risky, status_code=getattr(erro, "code", None),
            error_code="TRANSPORT_ERROR",
        ), metadata
    return None, metadata


def _adapter_response_metadata(headers) -> dict[str, Any]:
    """Read adapter-only physical telemetry from successful HTTP responses."""
    if headers is None:
        return {}
    def get(name):
        return headers.get(name)
    def as_int(name):
        raw = get(name)
        try:
            return int(raw) if raw is not None and str(raw).strip() else None
        except (TypeError, ValueError):
            return None
    def as_bool(name):
        raw = get(name)
        if raw is None:
            return None
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    mapping = {
        "adapter_profile": get("X-Eyle-Adapter-Profile"),
        "adapter_protocol": get("X-Eyle-Adapter-Protocol"),
        "adapter_structured_upstream_mode": get("X-Eyle-Structured-Upstream-Mode"),
        "adapter_structured_configured_mode": get("X-Eyle-Structured-Configured-Mode"),
        "adapter_cache_mode": get("X-Eyle-Cache-Mode"),
        "adapter_schema_enforcement": get("X-Eyle-Schema-Enforcement"),
        "adapter_upstream_attempts": as_int("X-Eyle-Upstream-Attempts"),
        "adapter_max_upstream_attempts": as_int("X-Eyle-Max-Upstream-Attempts"),
        "adapter_structured_repairs": as_int("X-Eyle-Structured-Repairs"),
        "adapter_structured_contract_characters": as_int("X-Eyle-Structured-Contract-Characters"),
        "adapter_repair_context_mode": get("X-Eyle-Repair-Context-Mode"),
        "adapter_local_normalized": as_bool("X-Eyle-Local-Normalized"),
        "billing_may_have_occurred": as_bool("X-Eyle-Billing-May-Have-Occurred"),
        "retry_cost_risk": as_bool("X-Eyle-Retry-Cost-Risk"),
    }
    return {key: value for key, value in mapping.items() if value is not None and value != ""}


def _ajustar_timeout_leitura(resposta, read_timeout):
    """Atualiza o timeout do socket depois que os cabecalhos chegaram.

    Isso continua util para leituras de corpo longas, mas nao corrige o tempo
    de espera pelos cabecalhos: ``urlopen`` so devolve ``resposta`` depois de
    receber a linha de status HTTP.
    """
    if read_timeout is None:
        return
    candidatos = [
        getattr(getattr(getattr(resposta, "fp", None), "raw", None), "_sock", None),
        getattr(getattr(getattr(resposta, "fp", None), "raw", None), "socket", None),
    ]
    for sock in candidatos:
        if sock is not None and hasattr(sock, "settimeout"):
            try:
                sock.settimeout(float(read_timeout))
                return
            except (OSError, TypeError, ValueError):
                continue


@contextmanager
def _abrir_url(req, connect_timeout, read_timeout=None):
    """Abre HTTP sem usar o limite de conexao como limite de geracao.

    ``urllib`` aplica ``timeout`` tanto ao connect quanto a espera pela linha
    de status/cabecalhos. No llama-server sem streaming, esses cabecalhos so
    chegam depois que o modelo termina de gerar. Passar os 5 segundos de
    ``connect_timeout_seconds`` cancelava toda resposta mais lenta, embora o
    ``read_timeout_seconds`` estivesse configurado para 120 segundos.

    O limite de leitura vira o timeout efetivo da operacao. Para um backend
    local, conexao recusada continua falhando imediatamente. Preflight e
    descoberta continuam curtos porque nessas chamadas connect/read recebem o
    mesmo valor.
    """
    limite_operacao = read_timeout if read_timeout is not None else connect_timeout
    try:
        limite_operacao = max(0.1, float(limite_operacao))
    except (TypeError, ValueError):
        limite_operacao = max(0.1, float(connect_timeout or 1.0))

    resposta = urllib.request.urlopen(req, timeout=limite_operacao)
    try:
        _ajustar_timeout_leitura(resposta, limite_operacao)
        yield resposta
    finally:
        try:
            resposta.close()
        except OSError as exc:
            _diagnostico("HTTP_RESPONSE_CLOSE_FAILED", detail=str(exc))


def _semaforo_backend(chave, limite):
    limite = max(1, int(limite or 1))
    identidade = (chave, limite)
    with _SEMAFOROS_LOCK:
        return _SEMAFOROS_LLM.setdefault(
            identidade, threading.BoundedSemaphore(limite),
        )


def _esperar_cooldown(chave):
    with _COOLDOWN_LOCK:
        ate = _COOLDOWN_ATE.get(chave, 0.0)
    espera = max(0.0, ate - time.monotonic())
    if espera > 0:
        time.sleep(espera)


def _ativar_cooldown(chave, segundos):
    if segundos <= 0:
        return
    with _COOLDOWN_LOCK:
        _COOLDOWN_ATE[chave] = max(
            _COOLDOWN_ATE.get(chave, 0.0), time.monotonic() + segundos,
        )


def _endpoint_openai(base_url, recurso):
    """Aceita base_url com ou sem o sufixo /v1."""
    base = str(base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return base + "/" + recurso.lstrip("/")
    return base + "/v1/" + recurso.lstrip("/")


def _ler_corpo_http_error(erro):
    try:
        return erro.read().decode("utf-8", errors="replace")[:1000]
    except (OSError, ValueError, AttributeError) as exc:
        _diagnostico("HTTP_ERROR_BODY_READ_FAILED", detail=str(exc))
        return ""


def _mensagem_http_error(base_url, erro, corpo_erro=""):
    detalhe = f" Resposta do servidor: {corpo_erro[:500]}" if corpo_erro else ""
    return (
        f"O servidor em {base_url} respondeu, mas recusou o pedido "
        f"(HTTP {erro.code} {erro.reason}).{detalhe} "
        "Verifique se o Adapter em base_url esta pronto e se o modelo configurado "
        "existe no upstream selecionado pelo Adapter."
    )


def _metadata_resposta_normalizada(normalizada):
    return {
        "finish_reason": normalizada.finish_reason,
        "prompt_tokens": normalizada.prompt_tokens,
        "cached_prompt_tokens": normalizada.cached_prompt_tokens,
        "completion_tokens": normalizada.completion_tokens,
        "total_tokens": normalizada.total_tokens,
        "reasoning_tokens": normalizada.reasoning_tokens,
        "provider_model": normalizada.model,
        "response_id": normalizada.response_id,
        "streaming": bool(normalizada.streaming),
    }


def _registrar_metadata_backend(normalizada):
    if not isinstance(normalizada, NormalizedModelResponse):
        raise TypeError("normalized backend response required")
    fresh = _metadata_resposta_normalizada(normalizada)
    if normalized_stream := bool(normalizada.streaming):
        # Streaming metadata arrives piecemeal. In particular DeepSeek emits a
        # final usage-only chunk, so merge non-empty fields instead of erasing
        # the finish_reason/model learned from earlier chunks.
        previous = dict(getattr(_LLM_RESPONSE_LOCAL, "metadata", {}) or {})
        for key, value in fresh.items():
            if value is not None:
                previous[key] = value
        previous["streaming"] = normalized_stream
        _LLM_RESPONSE_LOCAL.metadata = previous
    else:
        _LLM_RESPONSE_LOCAL.metadata = fresh
    return normalizada


def _ultima_metadata_backend():
    return dict(getattr(_LLM_RESPONSE_LOCAL, "metadata", {}) or {})


def _texto_openai_backend(valor, *, streaming=False):
    return _registrar_metadata_backend(
        normalize_openai_chat_response(valor, streaming=streaming)
    ).usable_text()



def _finish_reason_truncado(metadata):
    reason = str((metadata or {}).get("finish_reason") or "").strip().lower()
    return reason in {"length", "max_tokens", "max_output_tokens", "token_limit"}


def _classify_output_truncation():
    """A backend length finish is a per-call provider ceiling, never task strategy."""
    return {
        "error_code": "MODEL_OUTPUT_TRUNCATED",
        "cause": "provider_output_ceiling",
        "message": "A resposta do modelo foi interrompida pelo limite de saída do backend.",
    }


def _latest_attempt(execution: ExecutionContext | None):
    if execution is None:
        return None
    call = execution.latest_call()
    attempts = call.get("attempts") if isinstance(call, dict) else None
    return attempts[-1] if isinstance(attempts, list) and attempts else None


def _registrar_inicio_tentativa_runtime(
    execution: ExecutionContext | None, *, profile: str,
):
    """Record a physical backend attempt only after preflight has succeeded."""
    if execution is None:
        return None
    call = execution.latest_call()
    if not isinstance(call, dict):
        call = execution.begin_call(mode=str(profile or "default"), turn=0, prompt={})
    return execution.add_attempt(call, {
        "logical_call_id": call.get("logical_call_id"),
        "profile": str(profile or "default"),
        "request_status": "started",
    })


def _registrar_metadata_runtime(execution: ExecutionContext | None, metadata, *, attempt=None):
    if execution is None:
        return None
    call = execution.latest_call()
    if not isinstance(call, dict):
        call = execution.begin_call(mode=str((metadata or {}).get("profile") or "default"), turn=0, prompt={})
    clean = {key: value for key, value in dict(metadata or {}).items() if value is not None}
    clean["logical_call_id"] = call.get("logical_call_id")
    if isinstance(attempt, dict):
        attempt.update(clean)
        attempt["request_status"] = str(clean.get("request_status") or "sent")
        return attempt
    return execution.add_attempt(call, clean)


def _registrar_falha_tentativa_runtime(attempt, error_code: str, detail: str = "", *, elapsed_ms=None):
    if not isinstance(attempt, dict):
        return
    attempt["request_status"] = str(error_code or "transport_error").lower()
    attempt["error_code"] = str(error_code or "TRANSPORT_ERROR")
    if detail:
        attempt["error_detail"] = str(detail)[:500]
    if isinstance(elapsed_ms, (int, float)):
        attempt["latency_ms"] = round(float(elapsed_ms), 2)
    if str(error_code or "").upper() == "READ_TIMEOUT":
        # The request crossed the send boundary but provider usage was never
        # returned. The provider may still have completed/billed the generation.
        attempt["provider_usage_unknown"] = True
        attempt["billing_may_have_occurred"] = True
        attempt["retry_cost_risk"] = True


def _structured_response_format(profile):
    return json_schema_response_format(profile)



def _chamar_openai_compatible(
    base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
    read_timeout=None, on_chunk=None,
    perfil=None, on_request=None, max_completion_tokens=None, reasoning_mode="off",
):
    """Call the local OpenAI-compatible Adapter.

    For structured cognition Eyle sends the current JSON Schema. The Adapter
    owns provider connection and mechanical wire conformance; Eyle owns ECC
    semantics after the candidate returns.
    """
    if on_request is not None:
        on_request()
    url = _endpoint_openai(base_url, "chat/completions")
    payload = {
        "model": model,
        "messages": prompt_messages(prompt_sistema, prompt_usuario),
        "temperature": temperature,
        "stream": bool(on_chunk),
        "reasoning_mode": str(reasoning_mode or "off"),
    }
    if isinstance(max_completion_tokens, int) and max_completion_tokens > 0:
        payload["max_completion_tokens"] = int(max_completion_tokens)
    if perfil is not None:
        # Eyle->Adapter has one stable wire protocol. Provider-specific structured
        # transport selection belongs entirely to the Adapter.
        fmt = _structured_response_format(perfil)
        if fmt is not None:
            payload["response_format"] = fmt
    dados = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json", "X-Eyle-Transport-Protocol": ADAPTER_TRANSPORT_PROTOCOL})
    try:
        with _abrir_url(req, timeout, read_timeout or timeout) as resp:
            response_headers = getattr(resp, "headers", None)
            if on_chunk is not None:
                partes = []
                ultimo = {}
                for linha_bruta in resp:
                    linha = linha_bruta.decode("utf-8", errors="replace").strip()
                    if not linha:
                        continue
                    if linha.startswith("data:"):
                        linha = linha[5:].strip()
                    if linha == "[DONE]":
                        break
                    try:
                        corpo = json.loads(linha)
                    except json.JSONDecodeError:
                        corpo = linha
                    ultimo = corpo if isinstance(corpo, dict) else ultimo
                    try:
                        delta = _texto_openai_backend(corpo, streaming=True)
                    except ResponseEnvelopeError as exc:
                        raise ErroLLM(
                            f"OpenAI-compatible backend returned an invalid streaming envelope: {exc}",
                            transient=False, error_code="BACKEND_RESPONSE_INVALID",
                        ) from exc
                    if delta:
                        partes.append(delta)
                    on_chunk(delta, ultimo, False)
                on_chunk("", ultimo, True)
                return "".join(partes)

            bruto = resp.read().decode("utf-8", errors="replace")
            try:
                corpo = json.loads(bruto)
            except json.JSONDecodeError:
                corpo = bruto
    except urllib.error.HTTPError as exc:
        body = _ler_corpo_http_error(exc)
        adapter_error, adapter_usage = _adapter_error_contract(exc, body)
        if adapter_usage:
            _LLM_RESPONSE_LOCAL.metadata = adapter_usage
        if adapter_error is not None:
            raise adapter_error from exc
        if perfil is not None and getattr(exc, "code", None) in (400, 404, 422):
            raise ErroLLM(
                _mensagem_http_error(base_url, exc, body),
                transient=False,
                status_code=getattr(exc, "code", None),
                error_code="LLM_STRUCTURED_OUTPUT_UNAVAILABLE",
            ) from exc
        # Preserve canonical HTTP classification only for unknown upstream 5xx.
        raise _erro_http(base_url, exc, body) from exc

    try:
        normalized = normalize_openai_chat_response(corpo)
        text = _registrar_metadata_backend(normalized).usable_text()
    except ResponseEnvelopeError as exc:
        raise ErroLLM(
            f"OpenAI-compatible backend returned an invalid Chat Completions envelope: {exc}",
            transient=False, error_code="BACKEND_RESPONSE_INVALID",
        ) from exc
    source = "content" if normalized.content.strip() else "empty"
    meta = _ultima_metadata_backend()
    meta.update(_adapter_response_metadata(response_headers if 'response_headers' in locals() else None))
    observed_protocol = meta.get("adapter_protocol")
    if observed_protocol not in (None, "", ADAPTER_TRANSPORT_PROTOCOL):
        raise ErroLLM(
            f"Adapter protocol incompatível: {observed_protocol}",
            transient=False, error_code="ADAPTER_PROTOCOL_INCOMPATIBLE",
        )
    meta.update({
        "completion_ceiling_requested": int(max_completion_tokens) if isinstance(max_completion_tokens, int) and max_completion_tokens > 0 else None,
        "reasoning_mode_requested": str(reasoning_mode or "off"),
        "structured_profile": perfil,
        "structured_mode": ("adapter_wire_json_schema" if perfil is not None else None),
        "structured_transport": ("openai_adapter_wire_json_schema" if perfil is not None else "text"),
        "structured_source": source,
    })
    _LLM_RESPONSE_LOCAL.metadata = meta
    return text



def _reservar_requisicao_llm(
    config, execution: ExecutionContext | None, prompt_sistema, prompt_usuario,
    *, profile=None,
):
    """Preflight and account one real backend request.

    The provider receives the complete prompt on every request. This function
    records estimated/provider token usage for telemetry and cache accounting;
    it does not enforce a task-wide token fuse. Context-window safety remains
    based on the full prompt plus reserved output and safety margin.
    """
    if execution is None:
        return {"estimated_prompt_tokens": 0, "estimated_effective_tokens": 0}
    cfg_llm = (config or {}).get("llm", {})
    cfg_context = (config or {}).get("context_engine", {})
    chars_per_token = max(1, int(cfg_context.get("chars_per_token_fallback", 3) or 3))
    system_tokens = estimar_tokens(prompt_sistema, chars_per_token)
    user_tokens = estimar_tokens(str(prompt_usuario), chars_per_token)
    stable_tokens = estimar_tokens(prompt_usuario.stable_text, chars_per_token) if isinstance(prompt_usuario, CanonicalPrompt) else 0
    prompt_tokens = system_tokens + user_tokens
    multiplier = execution.prompt_token_calibration if execution is not None else 1.0
    calibrated_prompt_tokens = int(math.ceil(prompt_tokens * min(4.0, max(0.75, float(multiplier)))))
    margin = max(0, int(cfg_context.get("safety_margin_tokens", 256) or 0))
    raw_window = cfg_llm.get("context_window_tokens")
    window = int(raw_window) if isinstance(raw_window, int) and not isinstance(raw_window, bool) and raw_window > 0 else None
    context_output_remaining = None if window is None else window - calibrated_prompt_tokens - margin
    if context_output_remaining is not None and context_output_remaining <= 0:
        raise ErroLLM(
            "O prompt não deixa espaço físico para resposta dentro da janela de contexto configurada.",
            transient=False, error_code="PROMPT_CONTEXT_BUDGET_EXCEEDED",
        )

    system_hash = hashlib.sha256(str(prompt_sistema or "").encode("utf-8")).hexdigest()
    seen_hashes = execution.system_prompt_hashes
    repeated_system = system_hash in seen_hashes
    if not repeated_system:
        seen_hashes.append(system_hash)
        del seen_hashes[:-8]
    # Cache is provider-specific. Core never converts cached tokens into a
    # vendor pricing-equivalent weight; physical and cached/uncached counts are
    # reported separately.
    effective_estimate = prompt_tokens

    current_prompt_effective = int(execution.prompt_tokens_effective or 0)
    current_prompt_physical = max(int(execution.prompt_tokens_budgeted_physical or 0), int(execution.prompt_tokens_actual or 0))
    current_prompt_estimated_raw = int(execution.prompt_tokens_estimated_raw or 0)
    reserved_prompt_tokens = calibrated_prompt_tokens
    protected = 0
    execution.prompt_tokens_budgeted_physical = current_prompt_physical + reserved_prompt_tokens
    execution.prompt_tokens_estimated_raw = current_prompt_estimated_raw + prompt_tokens
    execution.prompt_tokens_effective = current_prompt_effective + effective_estimate
    return {
        "estimated_prompt_tokens": prompt_tokens,
        "budgeted_prompt_tokens": reserved_prompt_tokens,
        "context_output_remaining": context_output_remaining,
        "prompt_token_calibration": round(float(multiplier), 4),
        "estimated_effective_tokens": effective_estimate,
        "estimated_system_tokens": system_tokens,
        "estimated_user_tokens": user_tokens,
        "estimated_stable_prefix_tokens": stable_tokens,
        "protected_tokens": protected,
        "repeated_system_prompt": repeated_system,
        "finalized": False,
    }


def _release_unused_reservation(execution: ExecutionContext | None, reservation) -> None:
    if execution is None or not isinstance(reservation, dict) or reservation.get("finalized"):
        return
    execution.prompt_tokens_budgeted_physical = max(0, int(execution.prompt_tokens_budgeted_physical or 0) - int(reservation.get("budgeted_prompt_tokens") or 0))
    execution.prompt_tokens_estimated_raw = max(0, int(execution.prompt_tokens_estimated_raw or 0) - int(reservation.get("estimated_prompt_tokens") or 0))
    execution.prompt_tokens_effective = max(0, int(execution.prompt_tokens_effective or 0) - int(reservation.get("estimated_effective_tokens") or 0))
    reservation["finalized"] = True
    reservation["request_sent"] = False


def _finalizar_requisicao_llm(config, execution: ExecutionContext | None, reservation, metadata):
    if execution is None or not isinstance(reservation, dict):
        return
    if reservation.get("finalized"):
        return
    estimated_effective = int(reservation.get("estimated_effective_tokens", 0) or 0)
    estimated_raw = int(reservation.get("estimated_prompt_tokens", 0) or 0)
    actual = (metadata or {}).get("prompt_tokens")
    cached = (metadata or {}).get("cached_prompt_tokens")
    if isinstance(actual, (int, float)):
        actual = max(0, int(actual))
        # Replace this call's conservative reservation with provider truth.
        # Reservations remain authoritative only when provider usage is absent.
        reserved_physical = int(reservation.get("budgeted_prompt_tokens", 0) or 0)
        execution.prompt_tokens_budgeted_physical = max(
            0, int(execution.prompt_tokens_budgeted_physical or 0) - reserved_physical + actual,
        )
        execution.prompt_tokens_actual += actual
        if isinstance(cached, (int, float)):
            cached = min(actual, max(0, int(cached)))
            uncached = max(0, actual - cached)
            effective_actual = actual
            execution.prompt_tokens_cached += cached
            execution.prompt_tokens_uncached += uncached
        elif estimated_raw > 0:
            # Without a provider cache breakdown, keep token accounting physical.
            effective_actual = actual
            execution.prompt_tokens_uncached += actual
        else:
            effective_actual = actual
            execution.prompt_tokens_uncached += actual
        execution.prompt_tokens_effective = max(0, int(execution.prompt_tokens_effective or 0) + effective_actual - estimated_effective)
    metadata = metadata if isinstance(metadata, dict) else {}
    total_tokens = metadata.get("total_tokens")
    usage_source = "provider_total_tokens"
    if not isinstance(total_tokens, (int, float)):
        prompt = metadata.get("prompt_tokens")
        completion = metadata.get("completion_tokens")
        if isinstance(prompt, (int, float)) and isinstance(completion, (int, float)):
            total_tokens = int(prompt) + int(completion)
            usage_source = "provider_prompt_plus_completion"
    if isinstance(total_tokens, (int, float)):
        total_tokens = max(0, int(total_tokens))
        execution.provider_total_tokens_actual += total_tokens
        reservation["provider_total_tokens"] = total_tokens
        reservation["provider_usage_source"] = usage_source
    else:
        execution.provider_usage_unknown = True
        reservation["provider_usage_source"] = "unknown"
    reservation["finalized"] = True


def _registrar_tokens_gerados(config, execution: ExecutionContext | None, resposta, metadata_respostas=None):
    if execution is None:
        return
    metadata_respostas = list(metadata_respostas or [])
    reais = [
        int(item.get("completion_tokens"))
        for item in metadata_respostas
        if isinstance(item, dict) and isinstance(item.get("completion_tokens"), (int, float))
    ]
    reasoning = sum(
        int(item.get("reasoning_tokens"))
        for item in metadata_respostas
        if isinstance(item, dict) and isinstance(item.get("reasoning_tokens"), (int, float))
    )
    execution.reasoning_tokens_actual += reasoning
    chars_por_token = max(
        1, int((config or {}).get("context_engine", {}).get("chars_per_token_fallback", 3)),
    )
    estimativa = sum(reais) if reais else (
        len(str(resposta or "")) + chars_por_token - 1
    ) // chars_por_token
    total = int(execution.completion_tokens_actual or 0) + estimativa
    execution.completion_tokens_actual = total


def _provider_token_limit(config, execution: ExecutionContext | None = None) -> int:
    if execution is not None and int(getattr(execution, "provider_token_limit", 0) or 0) > 0:
        return int(execution.provider_token_limit)
    raw = ((config or {}).get("llm") or {}).get("provider_token_budget_per_message", 150000)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 150000


def _ensure_provider_token_budget(config, execution: ExecutionContext | None) -> int:
    """Guard one user-message execution using provider-reported billed usage.

    The authoritative counter is ``usage.total_tokens`` returned by the Adapter
    after aggregating every upstream attempt it performed. Prompt/completion
    estimates are used only for preflight; they never replace provider billing
    truth in the execution ledger.
    """
    limit = _provider_token_limit(config, execution)
    if execution is None:
        return limit
    if bool(execution.provider_usage_unknown):
        raise ErroLLM(
            "O provider pode ter contabilizado uma chamada sem devolver usage; novas chamadas foram bloqueadas para não ultrapassar o orçamento às cegas.",
            transient=False, error_code="PROVIDER_USAGE_UNKNOWN_BUDGET_STOP",
        )
    used = int(execution.provider_total_tokens_actual or 0)
    remaining = max(0, limit - used)
    if remaining <= 0:
        raise ErroLLM(
            f"O orçamento de {limit} tokens reportados pelo provider para esta mensagem foi atingido.",
            transient=False, error_code="PROVIDER_TOKEN_BUDGET_REACHED",
        )
    return remaining


def _enforce_provider_token_budget_after_usage(config, execution: ExecutionContext | None) -> None:
    if execution is None:
        return
    if execution.provider_usage_unknown:
        return
    limit = _provider_token_limit(config, execution)
    used = int(execution.provider_total_tokens_actual or 0)
    if used > limit:
        raise ErroLLM(
            f"O provider reportou {used} tokens totais para esta mensagem, acima do orçamento de {limit}. Nenhuma nova chamada será permitida.",
            transient=False, error_code="PROVIDER_TOKEN_BUDGET_EXCEEDED",
        )


def _timeouts_da_chamada(cfg_llm, perfil):
    connect_timeout = float(cfg_llm.get("connect_timeout_seconds", 5))
    perfil_chave = f"{perfil}_timeout_seconds" if perfil else None
    configured_read = cfg_llm.get(perfil_chave) if perfil_chave and perfil_chave in cfg_llm else cfg_llm.get("read_timeout_seconds")
    read_timeout = float(configured_read) if configured_read is not None else 1800.0
    return max(0.1, connect_timeout), max(0.1, read_timeout)




def _metricas_stream(metadata, estimativa_tokens, segundos):
    metadata = metadata if isinstance(metadata, dict) else {}
    tokens = None
    tps = None

    uso = metadata.get("usage")
    if isinstance(uso, dict):
        valor = uso.get("completion_tokens")
        if isinstance(valor, (int, float)):
            tokens = int(valor)

    eval_count = metadata.get("eval_count")
    eval_duration = metadata.get("eval_duration")
    if isinstance(eval_count, (int, float)):
        tokens = int(eval_count)
        if isinstance(eval_duration, (int, float)) and eval_duration > 0:
            tps = float(eval_count) / (float(eval_duration) / 1_000_000_000.0)

    timings = metadata.get("timings")
    if isinstance(timings, dict):
        for chave in ("predicted_per_second", "tokens_per_second"):
            valor = timings.get(chave)
            if isinstance(valor, (int, float)) and valor >= 0:
                tps = float(valor)
                break
        if tokens is None:
            valor = timings.get("predicted_n")
            if isinstance(valor, (int, float)):
                tokens = int(valor)

    if tokens is None:
        tokens = max(0, int(estimativa_tokens or 0))
    if tps is None and segundos > 0 and tokens > 0:
        tps = tokens / segundos
    return tokens, tps


def _criar_callback_stream(execution: ExecutionContext | None, perfil, visivel, chars_por_token):
    partes = []
    inicio = [None]
    ultima_metadata = [{}]

    def callback(delta, metadata=None, done=False):
        agora = time.monotonic()
        if inicio[0] is None:
            inicio[0] = agora
        if isinstance(delta, str) and delta:
            partes.append(delta)
        if isinstance(metadata, dict) and metadata:
            ultima_metadata[0] = metadata
        texto = "".join(partes)
        estimativa = (len(texto) + chars_por_token - 1) // chars_por_token
        segundos = max(0.001, agora - inicio[0])
        tokens, tps = _metricas_stream(ultima_metadata[0], estimativa, segundos)
        mensagem = "Escrevendo a resposta" if visivel else "LLM gerando tokens"
        campos = {
            "profile": perfil or "default",
            "estimated_tokens": tokens,
            "tokens_per_second": round(tps, 2) if tps is not None else None,
        }
        if visivel:
            campos["partial_text"] = texto[-16000:]
        job_progress.publicar(
            execution, "generating" if not done else "validating",
            mensagem if not done else "Geracao concluida; validando a resposta",
            force=bool(done), min_interval=0.18, **campos,
        )

    return callback


def _chamar_llm_impl(
    prompt_sistema, prompt_usuario, config, execution: ExecutionContext | None = None, perfil=None, stream_visible=False,
):
    """Chama o backend com limites, isolamento e retry transitório."""
    cfg_llm = config.get("llm", {})
    base_url = cfg_llm.get("base_url", "http://127.0.0.1:8080")
    configured_model = cfg_llm.get("model", "deepseek-v4-flash")
    model = configured_model
    temperature = cfg_llm.get("temperature", 0.2)
    transport_policy = provider_policy(config)
    # Prove the local Adapter transport contract before any paid
    # generation. Success is cached mechanically per Adapter base URL.
    _ensure_adapter_ready(config)
    _ensure_provider_token_budget(config, execution)
    connect_timeout, read_timeout = _timeouts_da_chamada(cfg_llm, perfil)

    model = str(model or "").strip()
    if not model:
        raise ErroLLM(
            "Nenhum modelo foi configurado para o Adapter.",
            transient=False, error_code="MODEL_REQUIRED",
        )
    # The bundled Adapter uses one explicitly configured DeepSeek model; Core
    # forwards its stable model field but never performs discovery/probing.

    structured_mode = "json_schema" if perfil is not None else None
    # The provider-facing representation contract is attached once by the Adapter
    # from Eyle's supplied JSON Schema. Core keeps only Eyle semantics.

    if execution is not None:
        latest_call = execution.latest_call()
        if isinstance(latest_call, dict):
            prompt_meta = latest_call.setdefault("prompt", {})
            chars_per_token = max(
                1, int((config or {}).get("context_engine", {}).get("chars_per_token_fallback", 3) or 3),
            )
            prompt_meta["system_prompt_characters"] = len(str(prompt_sistema or ""))
            prompt_meta["system_prompt_estimated_tokens"] = estimar_tokens(prompt_sistema, chars_per_token)

    job_progress.publicar(
        execution, "llm_wait", "Aguardando o Adapter LLM",
        profile=perfil or "default",
    )
    latest_call = execution.latest_call() if execution is not None else None
    if isinstance(latest_call, dict):
        prompt_meta = latest_call.setdefault("prompt", {})
        prompt_meta["output_ceiling"] = "execution_provider_token_budget"
        prompt_meta["provider_token_budget_per_message"] = _provider_token_limit(config, execution)
        prompt_meta["provider_tokens_before_call"] = int(execution.provider_total_tokens_actual or 0) if execution is not None else 0

    tentativas = max(1, int(cfg_llm.get("retry_max_attempts", 3)))
    base_delay = max(0.0, float(cfg_llm.get("retry_base_delay_seconds", 0.5)))
    max_delay = max(base_delay, float(cfg_llm.get("retry_max_delay_seconds", 2.0)))
    jitter = max(0.0, float(cfg_llm.get("retry_jitter_seconds", 0.2)))
    cooldown = max(0.0, float(cfg_llm.get("cooldown_seconds", 2.0)))
    chave_backend = (str(base_url).rstrip("/"), str(model), "adapter_openai_chat")
    semaforo = _semaforo_backend(
        chave_backend, cfg_llm.get("max_concurrent_requests", 1),
    )
    if not semaforo.acquire(timeout=max(0.1, read_timeout)):
        raise ErroLLM(
            "A fila interna de chamadas LLM excedeu o prazo disponivel.",
            transient=True, error_code="LLM_RATE_LIMIT_WAIT_TIMEOUT",
        )

    limite_processos = max(1, int(cfg_llm.get("max_concurrent_requests", 1)))
    lease_seconds = max(
        30.0,
        (connect_timeout + read_timeout + max_delay + cooldown) * tentativas + 30.0,
    )
    try:
        slot_processo = limiter.acquire(
            chave_backend, limit=limite_processos,
            timeout=max(0.1, read_timeout), lease_seconds=lease_seconds,
        )
    except Exception:
        semaforo.release()
        raise
    if slot_processo is None:
        semaforo.release()
        raise ErroLLM(
            "A fila entre processos de chamadas LLM excedeu o prazo disponivel.",
            transient=True, error_code="LLM_PROCESS_RATE_LIMIT_WAIT_TIMEOUT",
        )

    # Structured profiles are always non-streaming so only the final content
    # field can cross the executable-response boundary.
    streaming_ativado = bool(
        job_progress.job_id_de(execution) is not None
        and cfg_llm.get("stream_responses", True)
        and perfil is None
    )
    chars_por_token = max(
        1, int((config or {}).get("context_engine", {}).get("chars_per_token_fallback", 3)),
    )

    metadata_chamadas = []
    try:
        ultimo_erro = None
        for tentativa in range(1, tentativas + 1):
            _esperar_cooldown(chave_backend)
            connect_atual, read_atual = _timeouts_da_chamada(cfg_llm, perfil)
            job_progress.publicar(
                execution, "llm_request",
                "Solicitando geracao ao Adapter" if tentativa == 1 else "Tentando o Adapter novamente",
                profile=perfil or "default", attempt=tentativa, max_attempts=tentativas,
                partial_text="" if stream_visible else None,
            )
            on_chunk = (
                _criar_callback_stream(
                    execution, perfil, bool(stream_visible), chars_por_token,
                )
                if streaming_ativado else None
            )
            attempt_state = {"attempt": None, "started_at": None}
            try:
                def chamar_backend(callback):
                    _LLM_RESPONSE_LOCAL.metadata = {}
                    inicio_backend = time.monotonic()
                    reservation = _reservar_requisicao_llm(
                        config, execution, prompt_sistema, prompt_usuario, profile=perfil,
                    )
                    attempt_state["reservation"] = reservation
                    remaining_provider = _ensure_provider_token_budget(config, execution)
                    estimated_prompt = int(reservation.get("budgeted_prompt_tokens") or 0)
                    provider_output_remaining = remaining_provider - estimated_prompt
                    context_output_remaining = reservation.get("context_output_remaining")
                    caps = [provider_output_remaining]
                    if isinstance(context_output_remaining, int):
                        caps.append(context_output_remaining)
                    hard_remaining = min(caps) if caps else provider_output_remaining
                    if hard_remaining <= 0:
                        _release_unused_reservation(execution, reservation)
                        raise ErroLLM(
                            "O saldo de tokens desta mensagem não comporta outra chamada completa ao provider.",
                            transient=False, error_code="PROVIDER_TOKEN_BUDGET_REACHED",
                        )
                    attempt_state["started_at"] = time.monotonic()
                    attempt_state["attempt"] = _registrar_inicio_tentativa_runtime(
                        execution, profile=perfil or "default",
                    )
                    reservation["request_sent"] = True
                    resposta_backend = _chamar_openai_compatible(
                        base_url, model, prompt_sistema, prompt_usuario, temperature,
                        connect_atual, read_timeout=read_atual, on_chunk=callback,
                        on_request=None, perfil=perfil, max_completion_tokens=hard_remaining,
                        reasoning_mode=str(cfg_llm.get("reasoning_mode") or "off"),
                    )
                    metadata_backend = _ultima_metadata_backend()
                    _finalizar_requisicao_llm(config, execution, reservation, metadata_backend)
                    metadata_backend["latency_ms"] = round((time.monotonic() - inicio_backend) * 1000, 2)
                    metadata_backend["prompt_tokens_estimated"] = reservation.get("estimated_prompt_tokens", 0)
                    metadata_backend["provider_budget_before_call"] = remaining_provider
                    metadata_backend["client_completion_ceiling"] = hard_remaining
                    return resposta_backend, metadata_backend


                resposta, metadata_resposta = chamar_backend(on_chunk)
                metadata_resposta.update({
                    "configured_model": str(configured_model),
                    "resolved_model": str(metadata_resposta.get("provider_model") or model),
                    "provider": "adapter_openai_compatible",
                    "profile": perfil or "default",
                    "structured_mode": ("adapter_wire_json_schema" if perfil is not None else None),
                    "canonical_contract_mode": ("wire_json+local_canonical" if perfil is not None else None),
                    "canonical_transport_request_mode": ("wire_json_schema" if perfil is not None else None),
                    "provider_cache_mode": transport_policy["cache_mode"],
                })
                if _finish_reason_truncado(metadata_resposta):
                    truncation = _classify_output_truncation()
                    metadata_resposta.update({
                        "truncated": True,
                        "truncation_cause": truncation["cause"],
                    })
                    _registrar_metadata_runtime(execution, metadata_resposta, attempt=attempt_state["attempt"])
                    metadata_chamadas.append(dict(metadata_resposta))
                    _registrar_tokens_gerados(config, execution, resposta, metadata_chamadas)
                    _enforce_provider_token_budget_after_usage(config, execution)
                    raise ErroLLM(
                        truncation["message"], transient=False, error_code=truncation["error_code"],
                    )
                _registrar_metadata_runtime(execution, metadata_resposta, attempt=attempt_state["attempt"])
                metadata_chamadas.append(dict(metadata_resposta))
                if not isinstance(resposta, str) or not resposta.strip():
                    if perfil is not None:
                        # A successful billed structured generation with an empty
                        # assistant payload is a malformed cognition envelope, not
                        # a transport retry signal. Let local parsing turn it into
                        # one fresh current Eyle decision on the same execution.
                        resposta = ""
                        ultimo_erro = None
                        break
                    raise ErroLLM(
                        "O backend respondeu sem conteúdo utilizável.",
                        transient=True, error_code="EMPTY_MODEL_RESPONSE",
                    )
                ultimo_erro = None
                break
            except urllib.error.HTTPError as erro_http:
                if perfil is not None and getattr(erro_http, "code", None) in (400, 404, 422):
                    ultimo_erro = ErroLLM(
                        _mensagem_http_error(base_url, erro_http, _ler_corpo_http_error(erro_http)),
                        transient=False, status_code=getattr(erro_http, "code", None),
                        error_code="LLM_STRUCTURED_OUTPUT_UNAVAILABLE",
                    )
                else:
                    ultimo_erro = _erro_http(
                        base_url, erro_http, _ler_corpo_http_error(erro_http),
                    )
            except urllib.error.URLError as erro_rede:
                motivo = getattr(erro_rede, "reason", None)
                eh_timeout = isinstance(motivo, (socket.timeout, TimeoutError)) or (
                    "timed out" in str(motivo or erro_rede).lower()
                )
                repetir_timeout = bool(cfg_llm.get("retry_read_timeouts", False))
                ultimo_erro = ErroLLM(
                    (
                        f"O Adapter/backend excedeu o timeout de leitura de {read_atual:.1f}s "
                        f"em {base_url}."
                        if eh_timeout else
                        f"Nao foi possivel conectar/ler em {base_url}. Detalhe: {erro_rede}"
                    ),
                    transient=(repetir_timeout if eh_timeout else True),
                    error_code=("READ_TIMEOUT" if eh_timeout else "TRANSPORT_ERROR"),
                )
            except (socket.timeout, TimeoutError) as erro_timeout:
                ultimo_erro = ErroLLM(
                    f"O Adapter/backend excedeu o timeout de leitura de {read_atual:.1f}s "
                    f"em {base_url}. Detalhe: {erro_timeout}",
                    transient=bool(cfg_llm.get("retry_read_timeouts", False)),
                    error_code="READ_TIMEOUT",
                )
            except ConnectionError as erro_rede:
                ultimo_erro = ErroLLM(
                    f"Nao foi possivel conectar/ler em {base_url}. Detalhe: {erro_rede}",
                    transient=True, error_code="TRANSPORT_ERROR",
                )
            except ErroLLM as erro_llm:
                ultimo_erro = erro_llm
            except Exception as erro_inesperado:
                ultimo_erro = ErroLLM(
                    f"Falha ao chamar o Adapter LLM: {erro_inesperado}",
                    transient=False, error_code="UNEXPECTED_LLM_ERROR",
                )

            failed_metadata = _ultima_metadata_backend()
            if execution is not None and failed_metadata.get("billing_may_have_occurred") and not failed_metadata.get("provider_usage_from_error"):
                execution.provider_usage_unknown = True
            if (failed_metadata.get("retry_cost_risk") or failed_metadata.get("billing_may_have_occurred")
                    or int(failed_metadata.get("prompt_tokens") or 0) > 0
                    or int(failed_metadata.get("completion_tokens") or 0) > 0):
                ultimo_erro.transient = False
            reservation = attempt_state.get("reservation")
            if failed_metadata.get("provider_usage_from_error") and isinstance(reservation, dict):
                _finalizar_requisicao_llm(config, execution, reservation, failed_metadata)
                if execution is not None:
                    execution.completion_tokens_actual += max(0, int(failed_metadata.get("completion_tokens") or 0))
                    execution.reasoning_tokens_actual += max(0, int(failed_metadata.get("reasoning_tokens") or 0))
                _registrar_metadata_runtime(execution, failed_metadata, attempt=attempt_state.get("attempt"))

            started_at = attempt_state.get("started_at")
            elapsed_ms = (time.monotonic() - started_at) * 1000 if isinstance(started_at, (int, float)) else None
            _registrar_falha_tentativa_runtime(
                attempt_state.get("attempt"), ultimo_erro.error_code, str(ultimo_erro), elapsed_ms=elapsed_ms,
            )

            if not ultimo_erro.transient or tentativa >= tentativas:
                raise ultimo_erro

            if ultimo_erro.status_code in (429, 503):
                _ativar_cooldown(
                    chave_backend,
                    ultimo_erro.retry_after if ultimo_erro.retry_after is not None else cooldown,
                )
            atraso = ultimo_erro.retry_after
            if atraso is None:
                atraso = min(max_delay, base_delay * (2 ** (tentativa - 1)))
                if jitter:
                    atraso += random.uniform(0, jitter)
            job_progress.publicar(
                execution, "retry", "A LLM falhou; preparando nova tentativa",
                profile=perfil or "default", attempt=tentativa, max_attempts=tentativas,
                error=str(ultimo_erro)[:500],
            )
            _diagnostico(
                "LLM_RETRY", attempt=tentativa, max_attempts=tentativas,
                delay_seconds=round(atraso, 3), status_code=ultimo_erro.status_code,
                error_code=ultimo_erro.error_code,
            )
            if atraso > 0:
                time.sleep(atraso)
        else:
            raise ultimo_erro or ErroLLM("Falha desconhecida na LLM")
    finally:
        limiter.release(slot_processo)
        semaforo.release()

    _registrar_tokens_gerados(config, execution, resposta, metadata_chamadas)
    _enforce_provider_token_budget_after_usage(config, execution)
    parsed_response = resposta
    if perfil is not None:
        try:
            parsed_response = parse_profile_response(resposta, perfil)
            last = _latest_attempt(execution)
            if isinstance(last, dict):
                last["structured_parse_status"] = "valid"
                last["structured_profile"] = perfil
                last["structured_top_level_keys"] = sorted(parsed_response.keys())
        except StructuredResponseError as error:
            observed = observed_top_level(resposta)
            observed_keys = sorted(observed.keys()) if isinstance(observed, dict) else []
            required_keys = list(mandatory_top_level_keys(perfil))
            missing_keys = [key for key in required_keys if key not in observed_keys]
            last = _latest_attempt(execution)
            if isinstance(last, dict):
                last["structured_parse_status"] = "invalid"
                last["structured_parse_error"] = error.code
                last["structured_parse_detail"] = error.detail
                last["structured_profile"] = perfil
                last["structured_top_level_keys"] = observed_keys
                last["structured_missing_keys"] = missing_keys

            raise ErroLLM(
                f"Structured response for {perfil} is invalid: {error.detail}",
                transient=False,
                error_code=f"STRUCTURED_RESPONSE_INVALID:{perfil}:{error.code}",
                structured_error=error,
                structured_observed=observed,
            ) from error
    campos_finais = {"profile": perfil or "default"}
    if stream_visible and not streaming_ativado:
        campos_finais["partial_text"] = str(resposta or "")[-16000:]
    job_progress.publicar(
        execution, "validating", "Resposta gerada; executando validacoes",
        **campos_finais,
    )

    return parsed_response


def _chamar_llm(
    prompt_sistema, prompt_usuario, config, execution: ExecutionContext | None = None, perfil=None, stream_visible=False,
):
    """Fronteira observavel de toda chamada LLM, do AgentSession."""
    inicio = time.monotonic()
    if execution is None:
        execution = current_execution() or ExecutionContext.from_config(config)
    if execution.latest_call() is None:
        execution.begin_call(mode=perfil or "default", turn=0, prompt={})
    status = "ok"
    metadata = {"profile": perfil or "default", "structured": perfil is not None}
    try:
        resposta = _chamar_llm_impl(
            prompt_sistema, prompt_usuario, config, execution,
            perfil=perfil,
            stream_visible=stream_visible,
        )
        metadata["estimated_output_chars"] = len(str(resposta or ""))
        last_response = _latest_attempt(execution)
        if isinstance(last_response, dict):
            elapsed_ms = round((time.monotonic() - inicio) * 1000, 2)
            last_response["orchestration_latency_ms"] = elapsed_ms
            if not isinstance(last_response.get("latency_ms"), (int, float)):
                last_response["latency_ms"] = elapsed_ms
            metadata.update({key: value for key, value in last_response.items() if value is not None})
        return resposta
    except ErroLLM as erro:
        status = "error"
        metadata.update({
            "error_code": erro.error_code,
            "status_code": erro.status_code,
            "transient": erro.transient,
        })
        raise
    except Exception as erro:
        status = "exception"
        metadata.update({"exception": type(erro).__name__, "detail": str(erro)[:500]})
        raise
    finally:
        telemetry.record(
            "llm", perfil or "default", status,
            (time.monotonic() - inicio) * 1000,
            execution_id=execution.execution_id, job_id=execution.source_job_id,
            metadata=metadata,
        )



PROMPT_NAVIGATION = """You are Eyle, the running agent. Choose one ECC cognition.

AUTHORITY
Main owns meaning, references, relevance, Task semantics and sufficiency. Runtime owns physical IDs, schemas, budgets, permissions, persistence, Coverage and Frontier. Never delegate semantic selection to Runtime.

SELF
You/Eyle/this agent and your code, internals, core, runtime or memory mean this running Eyle unless context establishes another referent. source=eyle is self source; source=workspace is the user's selected project.
workspace = the user-selected/open project; source=eyle names the running Eyle source, never the workspace.

ECC NAVIGATION
Choose exactly one existing ECC movement:
- explorar: obtain facts through an Explore Surface.
- construir: request one Runtime-controlled lasting change through a Build Surface.
- concluir: answer now when evidence is sufficient and requested physical changes are done.
Navigation is a protocol surface, not a fourth ECC action. For ordinary conversation answerable from current request/recent conversation, concluir directly.

TASK
A Task is an ordinary kind=task Memory node. Main alone may create, revise, bind, replace or unbind the active Task. Runtime never discovers a Task. task_binding is an optional persistence sidecar. Do not create a Task for trivial conversation merely because Task exists.

MEMORY
memory_delta stores reusable learning; [] means none. Memory is continuous learning, not a planner or hidden working set. Explicit Memory activation remains separate from active Task projection.
Associative recall cues are Main-authored retrieval hints only, not Evidence. Runtime never invents/ranks semantic associations.
memory_view is a materialized view of explicitly activated Memory, never the boundary of Memory and never universal truth; Main judges meaning. Do not guess missing or ambiguous Memory.
For a durable task, Main may create it in the same cognition:
memory_delta:[{op:"remember",arguments:{key:"current_task",scope:"world",retention:"persistent",kind:"task",content:"stable task meaning"}}]
and bind exactly that node with task_binding:{action:"bind",ref:"@current_task"}. Existing mem-* Task ids may be bound directly. Do not persist transient tool-next-step reasoning as Task content.

CONVERSATION
Recent conversation uses native user/assistant roles. The final user message is always current_request. Resolve recent references before asking.
history_messages_omitted=0 means no older materialized conversation was omitted; do not substitute an unrelated fact. If omitted>0 and older context matters, explicit recall remains available.
Participate directly, not as a help-desk dispatcher; ask only when clarification is actually needed.

EFFICIENCY
Choose the nature of the next cognition. Detailed capability schemas are intentionally absent here; the selected surface will receive only its own physical operation catalog.
"""

PROMPT_EXPLORE = """You are Eyle, executing an Explore Surface selected explicitly by Main.

AUTHORITY
Main owns meaning, relevance, investigation direction and sufficiency. Runtime owns physical execution, exact IDs, Coverage, Frontier, budgets and persistence.

EXPLORE SURFACE
This surface may only request observe/execute operations exposed in explore_operations, or return control to ECC Navigation with return_to_ecc=true.
Batch only independent operations. Runtime never chooses the next tool for you.
Do not emit construir or concluir here; return_to_ecc when Main wants to choose another ECC movement.

TASK AND MEMORY
active_task, when present, is the exact Task Main bound to this execution. It is not Runtime retrieval or ranking.
memory_view is a materialized view of explicitly activated Memory, never the boundary of Memory and never universal truth.
memory_delta remains optional reusable learning, not a scratchpad or plan. task_binding may explicitly bind/unbind a Task. A same-call Task can be created with memory_delta remember(kind:"task", key:"...") and bound with @key.
Use latest observations, Evidence coordinates, Coverage and Frontiers as physical facts. Frontier is available continuation, never an instruction to consume it.

BODY / SOURCE
workspace = the user-selected/open project, even if it is a copy, fork, old revision, or repository containing Eyle code. eyle = the source tree of the Eyle instance currently running. Capabilities are Eyle's replaceable body. Never infer one from the other or fall back from workspace to eyle.

EFFICIENCY
Use only the facts and operations necessary for the current exploration. Preserve reachability instead of rereading already covered material.
Do not confuse an inventory with an analysis. When source evidence matters, inspect representative implementation before concluding; prefer targeted reads over structural dumps.
"""

PROMPT_BUILD = """You are Eyle, executing a Build Surface selected explicitly by Main.

AUTHORITY
Main owns semantic choice of change. Runtime owns mutation mechanics, confirmations, transactions, persistence and post-write physical facts.

BUILD SURFACE
This surface may request exactly one mutate operation exposed in build_operations, or return control to ECC Navigation with return_to_ecc=true.
Do not explore or conclude from this surface. After a mutation attempt, control returns to ECC Navigation so Main can decide what follows.

TASK AND MEMORY
active_task, when present, is the exact Task Main bound to this execution. Runtime did not select it.
memory_delta stores reusable learning only. task_binding may explicitly bind/unbind a Task. A same-call Task can be created with memory_delta remember(kind:"task", key:"...") and bound with @key.
A requested create/change/fix/remove/apply action is not complete by explanation alone; physical side effects remain Runtime-controlled.
"""

# Kept as a source-level name for integrations that import the semantic Eyle
# instruction; Rev4 calls use the explicit surface prompts below.
PROMPT_ECC = PROMPT_NAVIGATION


def warmup_provider_cache(prompt: CanonicalPrompt, config: dict[str, Any]) -> dict[str, Any]:
    """Prime an optional provider prefix cache with a normal Eyle request.

    There are no vendor branches here. The operator enables this only when the
    configured provider/adapter benefits from prewarming; correctness never
    depends on cache support and the returned cognition is discarded.
    """
    policy = provider_policy(config)
    if not policy.get("cache_warmup") or policy.get("cache_mode") == "none":
        return {"status": "disabled", "cache_mode": policy.get("cache_mode", "auto")}
    started = time.monotonic()
    try:
        decision = executar_navigation(prompt, config)
    except Exception as exc:
        return {
            "status": "failed",
            "cache_mode": policy.get("cache_mode", "auto"),
            "error_code": getattr(exc, "error_code", type(exc).__name__),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    return {
        "status": "ok",
        "cache_mode": policy.get("cache_mode", "auto"),
        "stable_prefix_hash": prompt.stable_hash,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "decision_type": decision.get("type") if isinstance(decision, dict) else None,
    }

def executar_navigation(prompt_usuario, config, execution: ExecutionContext | None = None):
    """Run Rev4 ECC Navigation: choose explorar, construir or concluir."""
    return _chamar_llm(PROMPT_NAVIGATION, prompt_usuario, config, execution, perfil="navigation")


def executar_explore(prompt_usuario, config, execution: ExecutionContext | None = None):
    """Run the Explore Execution Surface selected by Navigation."""
    return _chamar_llm(PROMPT_EXPLORE, prompt_usuario, config, execution, perfil="explore")


def executar_build(prompt_usuario, config, execution: ExecutionContext | None = None):
    """Run the Build Execution Surface selected by Navigation."""
    return _chamar_llm(PROMPT_BUILD, prompt_usuario, config, execution, perfil="build")


def executar_ecc(prompt_usuario, config, execution: ExecutionContext | None = None):
    """Current public cognition entry is Rev4 ECC Navigation."""
    return executar_navigation(prompt_usuario, config, execution)
