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
    StructuredResponseError, contract_instruction, json_schema_response_format,
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

ADAPTER_TRANSPORT_PROTOCOL = "eyle-adapter-transport-v1"
ADAPTER_HANDSHAKE_SCHEMA = "eyle-adapter-handshake-v1"
_ADAPTER_COMPATIBILITY_CACHE: dict[str, dict[str, Any]] = {}
_ADAPTER_HANDSHAKE_TTL_SECONDS = 300.0


def _diagnostico(codigo, **campos):
    """Log curto e estruturado; nunca interfere no resultado da chamada."""
    try:
        payload = {"code": codigo, **campos}
        print("[llm] " + json.dumps(payload, ensure_ascii=False, default=str), file=sys.stderr)
    except Exception:
        pass


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


def _validate_adapter_handshake(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("ADAPTER_HANDSHAKE_INVALID")
    if body.get("handshake_schema") != ADAPTER_HANDSHAKE_SCHEMA:
        raise ValueError("ADAPTER_HANDSHAKE_SCHEMA_INCOMPATIBLE")
    if body.get("adapter_protocol") != ADAPTER_TRANSPORT_PROTOCOL:
        raise ValueError("ADAPTER_PROTOCOL_INCOMPATIBLE")
    if body.get("authority") != "transport-only" or body.get("semantic_protocol") != "client-owned":
        raise ValueError("ADAPTER_AUTHORITY_CONTRACT_INVALID")
    capabilities = body.get("capabilities")
    if not isinstance(capabilities, dict):
        raise ValueError("ADAPTER_CAPABILITIES_INVALID")
    required = ("chat_completions", "client_json_schema_hint", "json_candidate_passthrough", "syntactic_json_recovery")
    if not all(capabilities.get(key) is True for key in required):
        raise ValueError("ADAPTER_REQUIRED_CAPABILITY_MISSING")
    endpoints = body.get("endpoints")
    if not isinstance(endpoints, dict) or not str(endpoints.get("chat_completions") or "").strip() or not str(endpoints.get("readiness") or "").strip():
        raise ValueError("ADAPTER_ENDPOINT_CONTRACT_INVALID")
    return body


def diagnosticar_backend(config, timeout=None):
    """Formal no-generation Eyle<->Adapter handshake followed by readiness.

    Eyle does not infer Adapter compatibility from /v1/models. The
    handshake validates transport authority/capabilities first; /ready then
    checks provider/model readiness without paid generation.
    """
    cfg_llm = (config or {}).get("llm", {})
    base_url = str(cfg_llm.get("base_url") or "http://127.0.0.1:8080").rstrip("/")
    limite = timeout
    if limite is None:
        limite = cfg_llm.get("model_discovery_timeout_seconds", 3)
    try:
        limite = max(0.1, min(float(limite), 10.0))
    except (TypeError, ValueError):
        limite = 3.0

    handshake_endpoint = _endpoint_openai(base_url, "eyle/handshake")
    inicio = time.monotonic()
    try:
        handshake, headers = _get_json(handshake_endpoint, limite, protocol=True)
        _validate_adapter_handshake(handshake)
    except urllib.error.HTTPError as erro:
        detalhe = _mensagem_http_error(base_url, erro, _ler_corpo_http_error(erro))
        return {
            "ok": False, "reachable": True, "base_url": base_url, "endpoint": handshake_endpoint,
            "error_code": "ADAPTER_HANDSHAKE_HTTP_ERROR", "status_code": getattr(erro, "code", None),
            "detail": detalhe, "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as erro:
        return {
            "ok": False, "reachable": False, "base_url": base_url, "endpoint": handshake_endpoint,
            "error_code": "BACKEND_UNREACHABLE",
            "detail": f"Nao foi possivel acessar o Adapter em {handshake_endpoint}: {erro}",
            "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }
    except Exception as erro:
        return {
            "ok": False, "reachable": True, "base_url": base_url, "endpoint": handshake_endpoint,
            "error_code": str(erro) if str(erro).startswith("ADAPTER_") else "ADAPTER_HANDSHAKE_INVALID",
            "detail": f"Handshake incompatível: {type(erro).__name__}: {erro}",
            "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }

    readiness_path = str((handshake.get("endpoints") or {}).get("readiness") or "/ready")
    readiness_endpoint = _adapter_root(base_url) + "/" + readiness_path.lstrip("/")
    try:
        ready, ready_headers = _get_json(readiness_endpoint, limite, protocol=True)
    except urllib.error.HTTPError as erro:
        detalhe = _mensagem_http_error(base_url, erro, _ler_corpo_http_error(erro))
        return {
            "ok": False, "reachable": True, "handshake_ok": True, "base_url": base_url,
            "endpoint": readiness_endpoint, "handshake": handshake,
            "error_code": "ADAPTER_NOT_READY", "status_code": getattr(erro, "code", None),
            "detail": detalhe, "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }
    except Exception as erro:
        return {
            "ok": False, "reachable": True, "handshake_ok": True, "base_url": base_url,
            "endpoint": readiness_endpoint, "handshake": handshake,
            "error_code": "ADAPTER_READINESS_ERROR",
            "detail": f"Adapter handshake passou, mas readiness falhou: {type(erro).__name__}: {erro}",
            "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }

    if not isinstance(ready, dict) or str(ready.get("status") or "") not in {"ready", "ready_configured"}:
        return {
            "ok": False, "reachable": True, "handshake_ok": True, "base_url": base_url,
            "endpoint": readiness_endpoint, "handshake": handshake, "readiness": ready,
            "error_code": "ADAPTER_NOT_READY", "detail": "Adapter handshake compatível, mas upstream/modelo não está pronto.",
            "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }
    models = []
    if isinstance(ready.get("models"), list):
        models = [str(v) for v in ready.get("models") if str(v).strip()]
    elif str(ready.get("model") or "").strip():
        models = [str(ready.get("model")).strip()]
    return {
        "ok": True, "reachable": True, "handshake_ok": True, "base_url": base_url,
        "endpoint": readiness_endpoint, "models": models[:20], "model_count": len(models),
        "adapter_protocol": handshake.get("adapter_protocol"),
        "adapter_profile": handshake.get("adapter_profile"),
        "adapter_version": handshake.get("adapter_version"),
        "handshake": handshake, "readiness": ready,
        "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
    }


def _ensure_adapter_handshake(config) -> dict[str, Any]:
    cfg_llm = (config or {}).get("llm") or {}
    base_url = str(cfg_llm.get("base_url") or "http://127.0.0.1:8080").rstrip("/")
    now = time.monotonic()
    cached = _ADAPTER_COMPATIBILITY_CACHE.get(base_url)
    if isinstance(cached, dict) and float(cached.get("expires_at") or 0) > now:
        return cached
    diag = diagnosticar_backend(config)
    if diag.get("ok") is not True:
        code = str(diag.get("error_code") or "ADAPTER_HANDSHAKE_FAILED")
        raise ErroLLM(
            str(diag.get("detail") or "Adapter handshake/readiness failed."),
            transient=code in {"BACKEND_UNREACHABLE", "ADAPTER_NOT_READY", "ADAPTER_READINESS_ERROR"},
            status_code=diag.get("status_code"), error_code=code,
        )
    entry = {
        "expires_at": now + _ADAPTER_HANDSHAKE_TTL_SECONDS,
        "adapter_protocol": diag.get("adapter_protocol"),
        "adapter_profile": diag.get("adapter_profile"),
        "adapter_version": diag.get("adapter_version"),
    }
    _ADAPTER_COMPATIBILITY_CACHE[base_url] = entry
    return entry


def _retry_after_seconds(erro):
    try:
        valor = erro.headers.get("Retry-After")
    except Exception:
        valor = None
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
    except Exception:
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
    cached_tokens = header_int("X-Eyle-Usage-Cached-Prompt-Tokens")
    upstream_attempts = header_int("X-Eyle-Upstream-Attempts")
    structured_repairs = header_int("X-Eyle-Structured-Repairs")
    if prompt_tokens is None and isinstance(usage.get("prompt_tokens"), (int, float)):
        prompt_tokens = max(0, int(usage.get("prompt_tokens") or 0))
    if completion_tokens is None and isinstance(usage.get("completion_tokens"), (int, float)):
        completion_tokens = max(0, int(usage.get("completion_tokens") or 0))
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
        try:
            return headers.get(name)
        except Exception:
            return None
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
        except Exception:
            pass


def _semaforo_backend(chave, limite):
    limite = max(1, int(limite or 1))
    identidade = (chave, limite)
    with _SEMAFOROS_LOCK:
        return _SEMAFOROS_LLM.setdefault(
            identidade, threading.BoundedSemaphore(limite),
        )


def _esperar_cooldown(chave, deadline=None):
    with _COOLDOWN_LOCK:
        ate = _COOLDOWN_ATE.get(chave, 0.0)
    espera = max(0.0, ate - time.monotonic())
    if deadline is not None:
        espera = min(espera, max(0.0, deadline - time.monotonic()))
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
    except Exception:
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
        "reasoning_tokens": normalizada.reasoning_tokens,
        "provider_model": normalizada.model,
        "response_id": normalizada.response_id,
        "streaming": bool(normalizada.streaming),
    }


def _registrar_metadata_backend(normalizada):
    if not isinstance(normalizada, NormalizedModelResponse):
        raise TypeError("normalized backend response required")
    _LLM_RESPONSE_LOCAL.metadata = _metadata_resposta_normalizada(normalizada)
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
    perfil=None, on_request=None,
):
    """Call the local OpenAI-compatible Adapter.

    For structured cognition Eyle sends only a tolerant wire-shape hint. The
    Adapter owns provider transport choice; strict ECC semantics are validated
    locally after deterministic canonicalization.
    """
    if on_request is not None:
        on_request()
    url = _endpoint_openai(base_url, "chat/completions")
    payload = {
        "model": model,
        "messages": prompt_messages(prompt_sistema, prompt_usuario),
        "temperature": temperature,
        "stream": bool(on_chunk),
    }
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
        "structured_profile": perfil,
        "structured_mode": ("adapter_wire_json_schema" if perfil is not None else None),
        "structured_transport": ("openai_adapter_wire_json_schema" if perfil is not None else "text"),
        "structured_source": source,
    })
    _LLM_RESPONSE_LOCAL.metadata = meta
    return text


def _timeout_restante(execution: ExecutionContext | None):
    if execution is None:
        return None
    return max(0.0, float(execution.deadline_monotonic) - time.monotonic())


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
    response_reserved = 0
    margin = max(0, int(cfg_context.get("safety_margin_tokens", 256) or 0))
    raw_window = cfg_llm.get("context_window_tokens")
    window = int(raw_window) if isinstance(raw_window, int) and not isinstance(raw_window, bool) and raw_window > 0 else None
    if window is not None and calibrated_prompt_tokens + response_reserved + margin > window:
        raise ErroLLM(
            "O prompt excede a janela de contexto local explicitamente configurada.",
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
        "prompt_token_calibration": round(float(multiplier), 4),
        "estimated_effective_tokens": effective_estimate,
        "estimated_system_tokens": system_tokens,
        "estimated_user_tokens": user_tokens,
        "estimated_stable_prefix_tokens": stable_tokens,
        "protected_tokens": protected,
        "repeated_system_prompt": repeated_system,
        "finalized": False,
    }


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


def _generated_token_limit(config, execution: ExecutionContext | None = None) -> int:
    if execution is not None and int(getattr(execution, "generated_token_limit", 0) or 0) > 0:
        return int(execution.generated_token_limit)
    raw = ((config or {}).get("llm") or {}).get("generated_token_fuse", 120000)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 120000


def _ensure_generated_token_budget(config, execution: ExecutionContext | None) -> int:
    """Stop new cognition once provider-counted completion usage reaches the fuse.

    Prompt/cache tokens are intentionally excluded. The fuse is execution-wide,
    not a tiny per-call output ceiling.
    """
    limit = _generated_token_limit(config, execution)
    used = int(execution.completion_tokens_actual or 0) if execution is not None else 0
    remaining = max(0, limit - used)
    if remaining <= 0:
        raise ErroLLM(
            f"O fusivel fisico de geracao atingiu {limit} tokens nesta resposta/tarefa.",
            transient=False, error_code="GENERATED_TOKEN_FUSE_REACHED",
        )
    return remaining


def _enforce_generated_token_fuse_after_usage(config, execution: ExecutionContext | None) -> None:
    if execution is None:
        return
    limit = _generated_token_limit(config, execution)
    used = int(execution.completion_tokens_actual or 0)
    if used > limit:
        raise ErroLLM(
            f"O provider reportou {used} tokens gerados, acima do fusivel de {limit}. "
            "Nenhuma nova chamada LLM sera permitida nesta execucao.",
            transient=False, error_code="GENERATED_TOKEN_FUSE_EXCEEDED",
        )


def _timeouts_da_chamada(cfg_llm, perfil, config, execution: ExecutionContext | None = None):
    connect_timeout = float(cfg_llm.get("connect_timeout_seconds", 5))
    perfil_chave = f"{perfil}_timeout_seconds" if perfil else None
    configured_read = cfg_llm.get(perfil_chave) if perfil_chave and perfil_chave in cfg_llm else cfg_llm.get("read_timeout_seconds")
    restante = _timeout_restante(execution)
    if restante is not None:
        if restante <= 0:
            raise ErroLLM(
                "O prazo total da tarefa foi esgotado antes da chamada LLM.",
                transient=False, error_code="TASK_DEADLINE_EXCEEDED",
            )
        connect_timeout = min(connect_timeout, restante)
        read_timeout = restante if configured_read is None else min(float(configured_read), restante)
    else:
        # Calls outside a normal ExecutionContext are still bounded mechanically.
        read_timeout = float(configured_read) if configured_read is not None else 1800.0
    return max(0.1, connect_timeout), max(0.1, read_timeout), restante


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
    configured_model = cfg_llm.get("model", "auto")
    model = configured_model
    temperature = cfg_llm.get("temperature", 0.2)
    transport_policy = provider_policy(config)
    # Prove the local Adapter transport contract before any paid
    # generation. Success is cached mechanically per Adapter base URL.
    _ensure_adapter_handshake(config)
    _ensure_generated_token_budget(config, execution)
    connect_timeout, read_timeout, deadline_restante = _timeouts_da_chamada(
        cfg_llm, perfil, config, execution,
    )

    model = str(model or "").strip()
    if not model:
        raise ErroLLM(
            "Nenhum modelo foi configurado para o Adapter.",
            transient=False, error_code="MODEL_REQUIRED",
        )
    # `auto` is an Adapter concern. Eyle forwards it verbatim instead of
    # depending on a separate /models discovery round-trip before cognition.

    structured_mode = "json_schema" if perfil is not None else None
    if perfil is not None:
        prompt_sistema = prompt_sistema.rstrip() + "\n\n" + contract_instruction(perfil)

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
        prompt_meta["output_ceiling"] = "execution_generated_token_fuse"
        prompt_meta["generated_token_fuse"] = _generated_token_limit(config, execution)
        prompt_meta["generated_tokens_before_call"] = int(execution.completion_tokens_actual or 0) if execution is not None else 0

    tentativas = max(1, int(cfg_llm.get("retry_max_attempts", 3)))
    base_delay = max(0.0, float(cfg_llm.get("retry_base_delay_seconds", 0.5)))
    max_delay = max(base_delay, float(cfg_llm.get("retry_max_delay_seconds", 2.0)))
    jitter = max(0.0, float(cfg_llm.get("retry_jitter_seconds", 0.2)))
    cooldown = max(0.0, float(cfg_llm.get("cooldown_seconds", 2.0)))
    chave_backend = (str(base_url).rstrip("/"), str(model), "adapter_openai_chat")
    semaforo = _semaforo_backend(
        chave_backend, cfg_llm.get("max_concurrent_requests", 1),
    )
    restante = _timeout_restante(execution)
    espera_semaforo = read_timeout if restante is None else min(read_timeout, restante)
    if not semaforo.acquire(timeout=max(0.1, espera_semaforo)):
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
            timeout=max(0.1, espera_semaforo), lease_seconds=lease_seconds,
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
            _esperar_cooldown(
                chave_backend,
                deadline=execution.deadline_monotonic if execution is not None else None,
            )
            connect_atual, read_atual, _ = _timeouts_da_chamada(cfg_llm, perfil, config, execution)
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
                    reservations = []

                    def before_request():
                        reservation = _reservar_requisicao_llm(
                            config, execution, prompt_sistema, prompt_usuario,
                            profile=perfil,
                        )
                        reservations.append(reservation)
                        attempt_state["reservation"] = reservation
                        attempt_state["started_at"] = time.monotonic()
                        attempt_state["attempt"] = _registrar_inicio_tentativa_runtime(
                            execution, profile=perfil or "default",
                        )

                    resposta_backend = _chamar_openai_compatible(
                        base_url, model, prompt_sistema, prompt_usuario, temperature,
                        connect_atual, read_timeout=read_atual, on_chunk=callback,
                        on_request=before_request,
                        perfil=perfil,
                    )
                    metadata_backend = _ultima_metadata_backend()
                    if reservations:
                        _finalizar_requisicao_llm(config, execution, reservations[-1], metadata_backend)
                    metadata_backend["latency_ms"] = round(
                        (time.monotonic() - inicio_backend) * 1000, 2,
                    )
                    metadata_backend["prompt_tokens_estimated"] = (
                        reservations[-1].get("estimated_prompt_tokens") if reservations else 0
                    )
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
                    _enforce_generated_token_fuse_after_usage(config, execution)
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
                        # ECC_PROTOCOL_RECOVERY on the same execution.
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
            restante = _timeout_restante(execution)
            if restante is not None:
                if restante <= 0:
                    raise ErroLLM(
                        "O prazo total da tarefa foi esgotado durante os retries da LLM.",
                        transient=False, error_code="TASK_DEADLINE_EXCEEDED",
                    )
                atraso = min(atraso, restante)
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
    _enforce_generated_token_fuse_after_usage(config, execution)
    parsed_response = resposta
    if perfil is not None:
        try:
            parsed_response = parse_profile_response(resposta, perfil or "ecc")
            last = _latest_attempt(execution)
            if isinstance(last, dict):
                last["structured_parse_status"] = "valid"
                last["structured_profile"] = perfil or "ecc"
                last["structured_top_level_keys"] = sorted(parsed_response.keys())
        except StructuredResponseError as error:
            observed = observed_top_level(resposta)
            observed_keys = sorted(observed.keys()) if isinstance(observed, dict) else []
            required_keys = list(mandatory_top_level_keys(perfil or "ecc"))
            missing_keys = [key for key in required_keys if key not in observed_keys]
            last = _latest_attempt(execution)
            if isinstance(last, dict):
                last["structured_parse_status"] = "invalid"
                last["structured_parse_error"] = error.code
                last["structured_parse_detail"] = error.detail
                last["structured_profile"] = perfil or "ecc"
                last["structured_top_level_keys"] = observed_keys
                last["structured_missing_keys"] = missing_keys

            raise ErroLLM(
                f"Structured response for {perfil or 'ecc'} is invalid: {error.detail}",
                transient=False,
                error_code=f"STRUCTURED_RESPONSE_INVALID:{perfil or 'ecc'}:{error.code}",
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



PROMPT_ECC = """You are Eyle. Return one simple JSON cognition object. Do the semantic thinking; Eyle handles safe serialization details.

THINK SIMPLY
Understand what the user means, not only the exact words. Use common sense, implicit references and useful implications. Not every message is a task: normal conversation, a reaction, or learning a fact can be complete by itself. Match the user's tone naturally.

TWO COGNITIVE LAYERS
Memory and ECC are distinct but simultaneous:
- Memory answers: what did I learn that may matter again?
- ECC answers: what should I do now?
Prefer the flat wire shape {type,...,memory_delta}. A nested decision envelope is also accepted, but do not spend reasoning on internal serialization. Memory is intrinsic to Eyle and sits beside ECC; it is not a tool, task ledger or transcript system.

THE THREE ECC MOVES
You have only three action moves:
- explorar: observe, read, recall, calculate, test, inspect, or continue unfinished observation. It may contain as many independent operations as you judge useful when none depends on another's result.
- construir: make one lasting Runtime-controlled change. After Runtime executes it, inspect the real result on the next cognition turn before learning from it or concluding.
- concluir: answer when you already know enough and no requested world change remains undone.
For explorar/construir, choose short operation names from ecc_operations.

AUTHORITY
You decide meaning, intent, relevance, what to learn, whether learned meaning is temporary or persistent, what durable Memory to recall, what to check next, and when enough is enough. Runtime only enforces mechanical facts: schemas, IDs, Coverage, Frontier continuity, private handles, freshness, physical budgets, permissions, confirmations, transactions and rollback. Runtime never decides semantic importance.

CURRENT REQUEST
current_request is the active user request. There is no raw transcript memory, Objective State, hidden task ledger or semantic Runtime planner. Continuity comes from Memory Graph itself. Temporary nodes in memory_view may encode weak clues, active referents, unresolved work, observations or local decisions. Persistent nodes are durable learned knowledge. Memory is remembered context, NOT universal truth: reconcile it with the current request and fresh observations, and revise/supersede it when reality changed.

BODY AND SOURCE IDENTITY
Capabilities are Eyle's replaceable body. When a capability requires source, source is physical identity, not content:
- workspace = the user-selected/open project being worked on, even if it is a copy, fork, old revision, or repository containing Eyle code.
- eyle = the source tree of the Eyle instance currently running.
Never choose eyle merely because workspace contains Eyle code. Never fall back from an empty workspace to eyle.

OBSERVATION, COVERAGE AND FRONTIER
latest_observations contains newest Runtime results. Physical observations become Material/Evidence automatically. exploration_map is derived from observed Coverage and Frontier. Coverage says what was mechanically examined, not whether it is semantically enough. Frontier is not a limit or warning to stop: it is the exact boundary after the material already shown, meaning more of the same exploration exists and can be materialized. Use continue with an fr-* ID as many times as you judge useful; private handles stay hidden. Use recall with ev-* only for exact saved Evidence from this run.

INTRINSIC MEMORY GRAPH
Every response includes memory_delta. Use [] only when this experience truly adds no useful future clue or durable knowledge. There is no semantic count ceiling on memory_delta. Decompose understanding into atomic reusable knowledge: one document, essay, file, observation, or long answer may justify tens, hundreds, or thousands of nodes and relations. Never put a whole transcript, document, or large artifact into one memory node merely to preserve it.

Keep memory_delta simple. Preferred remember wire form is flat: {op:"remember", scope:"user|world", retention:"temporary|persistent", kind, content, nature?, confidence?, volatility?, temporal?, context?, recall?, key?, tags?, support?}. Eyle deterministically wraps arguments and epistemic metadata into its canonical internal form.
Retention is ONLY a storage/lifecycle choice; it is never a truth score.
- temporary: worth keeping for now because later relevance is plausible. It may survive conversations/jobs indefinitely until Main explicitly changes it.
- persistent: worth preserving durably as part of Eyle's history/knowledge. Persistent does NOT mean certain, current, immutable, or universally applicable. A persistent node may be a weak old hypothesis or a historical preference.

EPISTEMIC MEMORY
Classify what you think you learned separately from retention. For new memories, normally include epistemic:{nature,confidence?,volatility?,temporal?,context?}. Runtime does not impose a closed ontology: nature and volatility are semantic labels you choose. Useful examples of nature include observation, event, statement, inference, hypothesis, belief, preference, decision, concept, rule, goal, relationship, anomaly, or uncertainty, but invent a better label when needed.
- nature answers WHAT epistemic thing this node represents; do not silently turn an observation into a fact or an inference into a user preference.
- confidence is 0..1 and expresses your present confidence in the interpretation/applicability, not metaphysical truth. Omit it when a numeric estimate would be fake precision.
- volatility describes how readily the represented state may change; people, intentions, tastes, project states and beliefs can be volatile even when worth preserving permanently.
- temporal is an open JSON object for the time frame you understood (for example as_of, observed_at, valid_from, valid_to, phase, historical). Do not invent dates you did not observe.
- context is an open JSON object for applicability (for example personal vs work, project/revision, situation, audience, environment). Do not generalize a local observation into a universal rule.
Existing unclassified memories from older revisions are not wrong; reassess them when they become relevant instead of bulk-rewriting history.

ASSOCIATIVE RECALL CUES
When useful, give a memory Main-authored recall metadata so future wording can rediscover it without a hidden embedding brain: recall:{aliases?:[], concepts?:[], cues?:[]}.
- aliases: alternate names/phrases that refer to the same remembered thing.
- concepts: broader concepts under which the memory may matter.
- cues: natural-language situations/questions that should make you think of this memory.
These strings are retrieval hints only; they are NOT evidence, confidence, truth, tags imposed by Runtime or proof that the memory is relevant now. Do not keyword-stuff every node. Add them when they capture a genuinely useful alternative path back to the knowledge. Eyle indexes exactly what you authored and never invents/ranks semantic associations.
On revise, you may replace recall or use add_recall/remove_recall with the same {aliases,concepts,cues} shape.

MEMORY CONSOLIDATION
Consolidation is part of YOUR normal cognition, not a second brain or Runtime job. Whenever new experience touches active/recalled Memory, compare it with the related nodes you can actually see and consolidate as useful:
- relate evidence/observations/hypotheses/conclusions rather than leaving every node isolated; relation labels are open semantic language (supports, contradicts, refines, derived_from, changed_from, applies_in_context, precedes, depends_on, etc.). Relations may carry their own epistemic metadata because a claimed causal/support/context relation can itself be uncertain or volatile.
- derive a higher-level hypothesis/pattern when multiple memories justify it, and support the derived node with those memory IDs. Do not delete the lower-level experiences. Give durable abstractions useful recall concepts/cues when future requests may phrase them differently.
- when fresh evidence conflicts with an active memory, distinguish actual temporal/context change from contradiction or mistaken inference; preserve both when history matters and relate/revise only what you can justify from visible support.
- revise the SAME node when your understanding of the same continuing proposition improves (confidence, context, wording, retention, epistemic metadata).
- when a volatile thing genuinely changes over time, normally preserve the old node as history, create the new state, and relate them (for example changed_from/contradicts/contextualizes). Do not overwrite "hated X" into "likes X" and erase that the change happened.
- supersede/archive only when the old representation itself should no longer be treated as a current representation, not merely because the world/person changed. Historical truth can remain valuable.
- repetition alone is not proof. New independent supports may raise confidence; repeated projection of the same memory is not new evidence.
- do not scan the entire graph just to perform maintenance. Consolidate the region relevant to current cognition; use memory_overview/memory_activate/Frontier when broader memory is actually useful.
There is no fixed number of nodes or relations you should create. A tiny experience may produce none; a rich artifact may produce thousands.

Other memory changes may also be flat:
- revise: {op:"revise", id, expected_revision, retention?, kind?, content?, nature?, confidence?, volatility?, temporal?, context?, recall?, add_recall?, remove_recall?, add_tags?, remove_tags?, support?}. Changing retention promotes/demotes the SAME node; epistemic reassessment is independent from retention.
- relate: {op:"relate", source, relation, target, nature?, confidence?, volatility?, temporal?, context?, support?}
- revise_relation: {op:"revise_relation", id, expected_revision, relation?, nature?, confidence?, volatility?, temporal?, context?, support?}. Use this when the same relation remains but your confidence/context/temporal interpretation changes.
- archive: {op:"archive", id, expected_revision}
- supersede: {op:"supersede", id, expected_revision, replacement}
- retire_relation: {op:"retire_relation", id, expected_revision}
Runtime owns mem-* and rel-* IDs. A remember key can be referenced in the same memory_delta as @key.

SUPPORT FORMAT
Prefer the simplest unambiguous wire support: "request", "mat-0001", "mem-..." or @key. You may also emit canonical support objects when useful. Eyle converts safe aliases into strict internal support objects. Never invent a support reference you do not actually have.

Memory is continuous learning, not only user-profile storage. The artifact/body stays outside Memory. Store what you understood about it as atomic nodes and relations, and attach supports to the exact Material whenever possible so knowledge points back to its source instead of duplicating the source body. A robot that notices a butterfly, a network agent that notices unusual resets, a coding agent that sees a suspicious boundary, and a conversation that establishes an implied referent can all produce temporary memory. If later evidence makes a temporary clue clearly valuable, revise the same node to persistent. Prefer temporary over forgetting a plausible future clue; still do not save meaningless noise or every sentence.

MEMORY RECALL
Runtime may project a small initial temporary working region plus persistent one-hop neighbours that Main explicitly related to it. memory_view is a working view, never the boundary of Memory and never evidence that unseen Memory is irrelevant. If the current request depends on an earlier referent, decision, project fact or unresolved work that is absent/ambiguous in memory_view, do not guess and do not treat the current message as isolated: inspect Memory. Start with memory_overview when you need the directory, then memory_activate with a focused query/IDs/tags. Recalled Memory is context to evaluate, not a command or universal truth. Persistent recall beyond the projected region is explicit:
- memory_overview: inspect graph directory without loading bodies. Its relation-label and consolidation counts are factual directory signals (for example isolated/revised/evidenced/associatively-described nodes), never an instruction that you must perform maintenance.
- memory_activate: choose a region by query or multiple Main-authored query variants, mem-* IDs, tags, epistemic natures, volatility labels or exact relation labels; optionally filter retention and request neighbours. Query also searches recall aliases/concepts/cues that YOU previously authored. Page size is your materialization choice, not a knowledge limit.
- memory_history: inspect the persisted revisions/events/relations of one mem-* node when how a belief/preference/state changed matters.
- memory_relation_history: inspect revision history of one rel-* relation when a support/causal/context claim itself evolved.
- continue: materialize the exact next page behind a Memory fr-* Frontier; repeat until you decide you have enough or the Frontier is exhausted.
Runtime does NOT do hidden semantic/topology/hot-cold ranking. Query recall may use a visible mechanical SQLite full-text/literal index to find lexical candidates at scale; Main still judges meaning and relevance. Frontier continuation is DB-cursor-backed and remains the exact next part of the selected region.

MEMORY SUPPORT AND FRESHNESS
Memory may cite current request, another memory, or current Material. Request support suits user-stated meaning. Freshness is mechanical source validity, not truth or importance.

TOKEN-EFFICIENT ACTION WITHOUT LESS REASONING
When several observations are independent and all are already justified, you may batch them into one explorar.operations list instead of spending another LLM round trip between each. Do NOT batch when a later operation depends on an earlier result. A Build always returns its physical result to you before completion so Memory can learn from what actually happened. Call-count optimization must never bypass post-action cognition or provenance.

HOW TO ACT
Choose the amount of exploration you judge appropriate. Coverage reports what was examined and Frontier reports what remains; neither tells you that enough has been seen. If the user explicitly asks to create, change, fix, remove or apply something, merely explaining the change is not completion: use construir once enough reality is observed to do it safely. Conclude naturally when the requested outcome is actually satisfied. Do not create phase routers, Task/Objective state, transcript memory, mini-agents, hot/cold tiers, retrieval counters or Runtime semantic ranking.
"""



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
        decision = executar_ecc(prompt, config)
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

def executar_ecc(prompt_usuario, config, execution: ExecutionContext | None = None):
    """Run the canonical Eyle ECC structured reasoning profile."""
    return _chamar_llm(PROMPT_ECC, prompt_usuario, config, execution, perfil="ecc")
