#!/usr/bin/env python3
"""Adaptador LLM da Eyle 2.7.5.

Transporta o único protocolo AgentSession para o backend configurado.
"""
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

from eyle.runtime.execution_context import ExecutionContext, current_execution  # noqa: E402
from eyle.runtime.token_budget import estimate_tokens as estimar_tokens  # noqa: E402
from eyle.runtime import telemetry  # noqa: E402
from eyle.runtime import limiter  # noqa: E402
from eyle.runtime import progress as job_progress  # noqa: E402
from llm.response_adapter import (  # noqa: E402
    NormalizedModelResponse, ResponseEnvelopeError,
    normalize_ollama_chat_response, normalize_openai_chat_response,
)
from llm.structured import (  # noqa: E402
    StructuredResponseError, contract_instruction, json_schema_response_format,
    mandatory_top_level_keys, observed_top_level, parse_profile_response,
    schema_for_profile,
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


# Deteccao basica, somente em memoria, do servidor OpenAI-compativel.
# Evita gravar estado novo no projeto e reaprende a cada reinicio da Eyle.
_MODELOS_OPENAI = {}
_SEMAFOROS_LLM = {}
_SEMAFOROS_LOCK = threading.Lock()
_COOLDOWN_ATE = {}
_COOLDOWN_LOCK = threading.Lock()
_LLM_RESPONSE_LOCAL = threading.local()
# Structured schemas and validation live in llm.structured.  This transport
# layer only chooses the empirically verified mechanism for the active connection.

def _schema_para_perfil(perfil):
    try:
        return schema_for_profile(perfil)
    except StructuredResponseError as exc:
        raise ErroLLM(
            f"Perfil estruturado desconhecido: {perfil}",
            transient=False, error_code=exc.code,
        ) from exc



def _diagnostico(codigo, **campos):
    """Log curto e estruturado; nunca interfere no resultado da chamada."""
    try:
        payload = {"code": codigo, **campos}
        print("[llm] " + json.dumps(payload, ensure_ascii=False, default=str), file=sys.stderr)
    except Exception:
        pass


def diagnosticar_backend(config, timeout=None):
    """Testa somente a API do backend, sem gerar tokens nem alterar estado persistente.

    O diagnostico e usado no startup para diferenciar "Flask/Worker online" de
    "servidor da LLM ausente". Nunca levanta para o chamador: devolve um objeto
    pequeno, seguro e pronto para log.
    """
    cfg_llm = (config or {}).get("llm", {})
    base_url = str(cfg_llm.get("base_url") or "http://localhost:11434").rstrip("/")
    openai_compatible = bool(cfg_llm.get("openai_compatible", False))
    limite = timeout
    if limite is None:
        limite = cfg_llm.get("model_discovery_timeout_seconds", 3)
    try:
        limite = max(0.1, min(float(limite), 10.0))
    except (TypeError, ValueError):
        limite = 3.0

    if openai_compatible:
        endpoint = _endpoint_openai(base_url, "models")
    else:
        endpoint = base_url + "/api/tags"

    inicio = time.monotonic()
    req = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
    try:
        with _abrir_url(req, limite, limite) as resp:
            corpo = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        detalhe = _mensagem_http_error(base_url, erro, _ler_corpo_http_error(erro))
        return {
            "ok": False,
            "reachable": True,
            "base_url": base_url,
            "endpoint": endpoint,
            "error_code": "BACKEND_HTTP_ERROR",
            "status_code": getattr(erro, "code", None),
            "detail": detalhe,
            "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as erro:
        return {
            "ok": False,
            "reachable": False,
            "base_url": base_url,
            "endpoint": endpoint,
            "error_code": "BACKEND_UNREACHABLE",
            "detail": f"Nao foi possivel acessar {endpoint}: {erro}",
            "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }
    except Exception as erro:
        return {
            "ok": False,
            "base_url": base_url,
            "endpoint": endpoint,
            "error_code": "BACKEND_DIAGNOSTIC_ERROR",
            "detail": f"Falha ao validar o backend: {type(erro).__name__}: {erro}",
            "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
        }

    modelos = []
    itens = corpo.get("data", []) if openai_compatible else corpo.get("models", [])
    if isinstance(itens, list):
        for item in itens:
            if isinstance(item, dict):
                valor = item.get("id") if openai_compatible else (item.get("name") or item.get("model"))
                if isinstance(valor, str) and valor.strip():
                    modelos.append(valor.strip())
    return {
        "ok": True,
        "reachable": True,
        "base_url": base_url,
        "endpoint": endpoint,
        "models": modelos[:20],
        "model_count": len(modelos),
        "latency_ms": round((time.monotonic() - inicio) * 1000, 1),
    }


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
        return ErroLLM(
            "O adaptador não conseguiu conectar ao provider.",
            transient=True, status_code=getattr(erro, "code", None),
            error_code="TRANSPORT_ERROR",
        ), metadata
    return None, metadata


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


def _detectar_modelos_openai(base_url, timeout, negative_ttl=60):
    """Resolve the explicit ``model: auto`` contract through ``/v1/models``.

    Discovery is never used to repair an explicit configured model name. A
    failed or empty discovery is therefore an explicit configuration/runtime
    error instead of a silent substitution path.
    """
    chave = str(base_url or "").rstrip("/")
    if chave in _MODELOS_OPENAI:
        cache = _MODELOS_OPENAI[chave]
        if isinstance(cache, tuple):
            return list(cache)
        if isinstance(cache, dict):
            if time.monotonic() < float(cache.get("expira", 0)):
                return list(cache.get("modelos") or [])
            _MODELOS_OPENAI.pop(chave, None)

    req = urllib.request.Request(_endpoint_openai(base_url, "models"))
    try:
        limite = min(max(float(timeout), 0.1), 5.0)
        with _abrir_url(req, limite, limite) as resp:
            corpo = json.loads(resp.read().decode("utf-8"))
    except Exception as erro:
        _MODELOS_OPENAI[chave] = {
            "modelos": (),
            "expira": time.monotonic() + max(1.0, float(negative_ttl or 60)),
            "erro": f"{type(erro).__name__}: {erro}",
        }
        _diagnostico(
            "MODEL_DISCOVERY_FAILED", base_url=chave,
            error=f"{type(erro).__name__}: {erro}",
        )
        telemetry.record(
            "internal", "model_discovery", "failed",
            metadata={
                "base_url": chave,
                "exception": type(erro).__name__,
                "detail": str(erro)[:500],
            },
        )
        return []

    modelos = []
    for item in corpo.get("data", []) if isinstance(corpo, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            modelos.append(item["id"].strip())
    if modelos:
        _MODELOS_OPENAI[chave] = tuple(modelos)
    else:
        _MODELOS_OPENAI[chave] = {
            "modelos": (),
            "expira": time.monotonic() + max(1.0, float(negative_ttl or 60)),
            "erro": "endpoint sem modelos",
        }
        telemetry.record(
            "internal", "model_discovery", "empty",
            metadata={"base_url": chave},
        )
    return modelos


def _resolver_modelo_openai(base_url, model, timeout, negative_ttl=60):
    """Use an explicit model verbatim; resolve only the explicit ``auto`` mode."""
    configurado = str(model or "").strip()
    if not configurado:
        raise ErroLLM(
            "Nenhum modelo foi configurado para o backend OpenAI-compatible.",
            transient=False, error_code="MODEL_REQUIRED",
        )
    if configurado.lower() != "auto":
        return configurado
    modelos = _detectar_modelos_openai(base_url, timeout, negative_ttl=negative_ttl)
    if not modelos:
        raise ErroLLM(
            "model='auto' exige que /v1/models retorne ao menos um modelo.",
            transient=False, error_code="MODEL_DISCOVERY_REQUIRED",
        )
    return modelos[0]

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
        "Verifique se o modelo configurado existe nesse servidor e se "
        "'openai_compatible' em config.json bate com o tipo de backend rodando ali."
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


def _texto_ollama_backend(valor, *, streaming=False):
    return _registrar_metadata_backend(
        normalize_ollama_chat_response(valor, streaming=streaming)
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
    execution: ExecutionContext | None, *, profile: str, max_tokens_requested: int | None,
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
        "max_tokens_requested": max_tokens_requested,
    })


def _registrar_metadata_runtime(execution: ExecutionContext | None, metadata, *, attempt=None):
    if execution is None:
        return None
    call = execution.latest_call()
    if not isinstance(call, dict):
        call = execution.begin_call(mode=str((metadata or {}).get("profile") or "default"), turn=0, prompt={})
    clean = {key: value for key, value in dict(metadata or {}).items() if value is not None}
    clean["logical_call_id"] = call.get("logical_call_id")
    requested = clean.get("max_tokens_requested")
    actual = clean.get("completion_tokens")
    if isinstance(requested, (int, float)) and isinstance(actual, (int, float)):
        clean["completion_tokens_ceiling_unused"] = max(0, int(requested) - max(0, int(actual)))
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


def _chamar_ollama(
    base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
    max_tokens=None, read_timeout=None, on_chunk=None,
    schema_estruturado=None, perfil=None,
):
    url = base_url.rstrip("/") + "/api/chat"
    options = {"temperature": temperature}
    if max_tokens:
        options["num_predict"] = max_tokens
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario},
        ],
        "stream": bool(on_chunk),
        "options": options,
    }
    if perfil is not None:
        payload["format"] = schema_estruturado or _schema_para_perfil(perfil)
    dados = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json"})
    with _abrir_url(req, timeout, read_timeout or timeout) as resp:
        if on_chunk is None:
            bruto = resp.read().decode("utf-8", errors="replace")
            try:
                corpo = json.loads(bruto)
            except json.JSONDecodeError:
                corpo = bruto
            try:
                text = _texto_ollama_backend(corpo)
            except ResponseEnvelopeError as exc:
                raise ErroLLM(
                    f"Ollama returned an invalid /api/chat envelope: {exc}",
                    transient=False, error_code="BACKEND_RESPONSE_INVALID",
                ) from exc
            meta = _ultima_metadata_backend()
            meta.update({
                "structured_profile": perfil,
                "structured_mode": "json_schema" if perfil is not None else None,
                "structured_transport": (
                    "ollama_json_schema" if perfil is not None else "text"
                ),
                "structured_source": "content",
            })
            _LLM_RESPONSE_LOCAL.metadata = meta
            return text

        partes = []
        ultimo = {}
        for linha_bruta in resp:
            linha = linha_bruta.decode("utf-8", errors="replace").strip()
            if not linha:
                continue
            try:
                corpo = json.loads(linha)
            except json.JSONDecodeError:
                corpo = linha
            ultimo = corpo if isinstance(corpo, dict) else ultimo
            delta = _texto_ollama_backend(corpo, streaming=True)
            if delta:
                partes.append(delta)
            on_chunk(delta, ultimo, bool(ultimo.get("done")))
        on_chunk("", ultimo, True)
        return "".join(partes)


def _chamar_openai_compatible(
    base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
    max_tokens=None, read_timeout=None, on_chunk=None,
    schema_estruturado=None, perfil=None, on_request=None,
):
    """Call one OpenAI-compatible Chat Completions endpoint.

    Structured profiles require strict JSON Schema transport.
    """
    if on_request is not None:
        on_request()
    url = _endpoint_openai(base_url, "chat/completions")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario},
        ],
        "temperature": temperature,
        "stream": bool(on_chunk),
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if perfil is not None:
        fmt = _structured_response_format(perfil)
        if fmt is not None:
            payload["response_format"] = fmt
    dados = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json"})
    try:
        with _abrir_url(req, timeout, read_timeout or timeout) as resp:
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
    meta.update({
        "structured_profile": perfil,
        "structured_mode": "json_schema" if perfil is not None else None,
        "structured_transport": (
            "openai_json_schema" if perfil is not None else "text"
        ),
        "structured_source": source,
    })
    _LLM_RESPONSE_LOCAL.metadata = meta
    return text


def _timeout_restante(execution: ExecutionContext | None):
    if execution is None:
        return None
    return max(0.0, float(execution.deadline_monotonic) - time.monotonic())


def _prompt_cache_weight(config):
    context = (config or {}).get("context_engine") or {}
    try:
        value = float(context.get("cached_prompt_weight", 0.2))
    except (TypeError, ValueError):
        value = 0.2
    return min(1.0, max(0.0, value))


def _reservar_requisicao_llm(
    config, execution: ExecutionContext | None, prompt_sistema, prompt_usuario, max_tokens,
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
    user_tokens = estimar_tokens(prompt_usuario, chars_per_token)
    prompt_tokens = system_tokens + user_tokens
    multiplier = execution.prompt_token_calibration if execution is not None else 1.0
    calibrated_prompt_tokens = int(math.ceil(prompt_tokens * min(4.0, max(0.75, float(multiplier)))))
    response_reserved = max(0, int(max_tokens or 0))
    margin = max(0, int(cfg_context.get("safety_margin_tokens", 256) or 0))
    window = max(1, int(cfg_llm.get("context_window_tokens", 38000) or 38000))
    if calibrated_prompt_tokens + response_reserved + margin > window:
        raise ErroLLM(
            "O prompt e a saída reservada excedem a janela de contexto do modelo.",
            transient=False, error_code="PROMPT_CONTEXT_BUDGET_EXCEEDED",
        )

    system_hash = hashlib.sha256(str(prompt_sistema or "").encode("utf-8")).hexdigest()
    seen_hashes = execution.system_prompt_hashes
    repeated_system = system_hash in seen_hashes
    if not repeated_system:
        seen_hashes.append(system_hash)
        del seen_hashes[:-8]
    cache_weight = _prompt_cache_weight(config)
    effective_estimate = user_tokens + int(round(system_tokens * (cache_weight if repeated_system else 1.0)))

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
            effective_actual = uncached + int(round(cached * _prompt_cache_weight(config)))
            execution.prompt_tokens_cached += cached
            execution.prompt_tokens_uncached += uncached
        elif estimated_raw > 0:
            # Preserve the repeated-prefix discount when the provider reports
            # only the total prompt count and no cache breakdown.
            ratio = estimated_effective / estimated_raw
            effective_actual = int(round(actual * ratio))
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


def _max_tokens_da_chamada(cfg_llm, perfil):
    """Resolve o teto de saida por perfil sem mudar o contrato do backend.

    Decisoes do Agente usam JSON curto e nao devem herdar automaticamente o
    teto grande de respostas finais/chat. Um teto separado impede modelos de
    raciocinio locais de gastar centenas de tokens internos em cada passo.
    """
    valor_global = cfg_llm.get("max_tokens", 700)
    chave = f"{perfil}_max_tokens" if perfil else None
    valor = cfg_llm.get(chave, valor_global) if chave else valor_global
    if valor is None:
        return None
    try:
        valor = int(valor)
    except (TypeError, ValueError):
        return valor_global
    return valor if valor > 0 else None


def _timeouts_da_chamada(cfg_llm, perfil, config, execution: ExecutionContext | None = None):
    connect_timeout = float(cfg_llm.get("connect_timeout_seconds", 5))
    perfil_chave = f"{perfil}_timeout_seconds" if perfil else None
    read_timeout = float(
        cfg_llm.get(perfil_chave, cfg_llm.get("read_timeout_seconds", 120))
        if perfil_chave else cfg_llm.get("read_timeout_seconds", 120)
    )
    restante = _timeout_restante(execution)
    if restante is not None:
        if restante <= 0:
            raise ErroLLM(
                "O prazo total da tarefa foi esgotado antes da chamada LLM.",
                transient=False, error_code="TASK_DEADLINE_EXCEEDED",
            )
        connect_timeout = min(connect_timeout, restante)
        read_timeout = min(read_timeout, restante)
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
    base_url = cfg_llm.get("base_url", "http://localhost:11434")
    configured_model = cfg_llm.get("model", "qwen2.5:7b-instruct-q4_0")
    model = configured_model
    temperature = cfg_llm.get("temperature", 0.2)
    openai_compatible = cfg_llm.get("openai_compatible", False)
    max_tokens = _max_tokens_da_chamada(cfg_llm, perfil)
    connect_timeout, read_timeout, deadline_restante = _timeouts_da_chamada(
        cfg_llm, perfil, config, execution,
    )

    if openai_compatible:
        descoberta_timeout = min(
            connect_timeout,
            float(cfg_llm.get("model_discovery_timeout_seconds", 3)),
        )
        model = _resolver_modelo_openai(
            base_url, model, descoberta_timeout,
            negative_ttl=cfg_llm.get("model_discovery_negative_ttl_seconds", 60),
        )

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
        execution, "llm_wait", "Aguardando a LLM local",
        profile=perfil or "default",
    )
    latest_call = execution.latest_call() if execution is not None else None
    if isinstance(latest_call, dict):
        prompt_meta = latest_call.setdefault("prompt", {})
        prompt_meta.setdefault("output_tokens_requested", int(max_tokens or 0))
        prompt_meta["output_tokens_reserved"] = int(max_tokens or 0)

    tentativas = max(1, int(cfg_llm.get("retry_max_attempts", 3)))
    base_delay = max(0.0, float(cfg_llm.get("retry_base_delay_seconds", 0.5)))
    max_delay = max(base_delay, float(cfg_llm.get("retry_max_delay_seconds", 2.0)))
    jitter = max(0.0, float(cfg_llm.get("retry_jitter_seconds", 0.2)))
    cooldown = max(0.0, float(cfg_llm.get("cooldown_seconds", 2.0)))
    chave_backend = (
        str(base_url).rstrip("/"), str(model), bool(openai_compatible),
    )
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
                "Solicitando geracao a LLM local" if tentativa == 1 else "Tentando a LLM novamente",
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
                def chamar_backend(token_limit, callback):
                    _LLM_RESPONSE_LOCAL.metadata = {}
                    inicio_backend = time.monotonic()
                    reservations = []

                    def before_request():
                        reservation = _reservar_requisicao_llm(
                            config, execution, prompt_sistema, prompt_usuario, token_limit,
                            profile=perfil,
                        )
                        reservations.append(reservation)
                        attempt_state["reservation"] = reservation
                        attempt_state["started_at"] = time.monotonic()
                        attempt_state["attempt"] = _registrar_inicio_tentativa_runtime(
                            execution, profile=perfil or "default", max_tokens_requested=token_limit,
                        )

                    if openai_compatible:
                        resposta_backend = _chamar_openai_compatible(
                            base_url, model, prompt_sistema, prompt_usuario, temperature,
                            connect_atual, max_tokens=token_limit,
                            read_timeout=read_atual, on_chunk=callback,
                            on_request=before_request,
                            schema_estruturado=(_schema_para_perfil(perfil) if perfil is not None else None), perfil=perfil,
                        )
                    else:
                        before_request()
                        resposta_backend = _chamar_ollama(
                            base_url, model, prompt_sistema, prompt_usuario, temperature,
                            connect_atual, max_tokens=token_limit,
                            read_timeout=read_atual, on_chunk=callback,
                            schema_estruturado=(_schema_para_perfil(perfil) if perfil is not None else None), perfil=perfil,
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

                resposta, metadata_resposta = chamar_backend(max_tokens, on_chunk)
                metadata_resposta.update({
                    "configured_model": str(configured_model),
                    "resolved_model": str(metadata_resposta.get("provider_model") or model),
                    "provider": ("openai_compatible" if openai_compatible else "ollama"),
                    "profile": perfil or "default",
                    "max_tokens_requested": max_tokens,
                    "structured_mode": "json_schema" if perfil is not None else None,
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
                    raise ErroLLM(
                        truncation["message"], transient=False, error_code=truncation["error_code"],
                    )
                _registrar_metadata_runtime(execution, metadata_resposta, attempt=attempt_state["attempt"])
                metadata_chamadas.append(dict(metadata_resposta))
                if not isinstance(resposta, str) or not resposta.strip():
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
                        f"A LLM local excedeu o timeout de leitura de {read_atual:.1f}s "
                        f"em {base_url}."
                        if eh_timeout else
                        f"Nao foi possivel conectar/ler em {base_url}. Detalhe: {erro_rede}"
                    ),
                    transient=(repetir_timeout if eh_timeout else True),
                    error_code=("READ_TIMEOUT" if eh_timeout else "TRANSPORT_ERROR"),
                )
            except (socket.timeout, TimeoutError) as erro_timeout:
                ultimo_erro = ErroLLM(
                    f"A LLM local excedeu o timeout de leitura de {read_atual:.1f}s "
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
                    f"Falha ao chamar a LLM local: {erro_inesperado}",
                    transient=False, error_code="UNEXPECTED_LLM_ERROR",
                )

            failed_metadata = _ultima_metadata_backend()
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



PROMPT_ECC = """You are Eyle. Return only one valid ECC JSON object.

THINK SIMPLY
Understand what the user means, not only the exact words they used. Use common sense. Notice useful hints and reasonable implications. Do not wait for the user to spell out every obvious next step. Not every message is a task: normal conversation, a reaction, or learning a fact can be complete by itself.

THE THREE MOVES
You have only three moves:
- explorar: look, read, check, calculate, test, or otherwise observe without making a lasting change.
- construir: make a lasting change through a Runtime-controlled operation.
- concluir: answer the user when you have enough to give a good answer.
For explorar or construir, choose a short operation name from the matching ecc_operations list. Never put explorar. or construir. inside operation.

WHO DECIDES WHAT
You decide meaning, intent, relevance, what to remember, what to check next, and when you know enough. Runtime does not understand meaning. Runtime only checks rules, stores state, runs capabilities, records what was observed, checks source freshness, handles IDs, limits, permissions, confirmations, transactions, rollback, and other mechanical facts. Never hand semantic choices to Runtime.

OBJECTIVE
current_request is exactly what the user said for this run. Do not rewrite it. objective_state is optional and means: "what am I still trying to achieve?"
Every decision includes objective={disposition,state}:
- unchanged + state:null: keep the current objective_state.
- updated + state:{summary,status,children,constraints}: replace it.
- cleared + state:null: remove it.
Use Objective only when it helps you keep track of something that continues across moves. If you can answer now, do not invent one. Conversation can have no Objective. A request with several goals may use children. Objective says WHAT is still wanted, never HOW to do it. Do not put tool order, phases, checklists, or a hidden plan in Objective. Runtime stores Objective but never reads its meaning or uses it to block concluir.

WHAT YOU SAW
Evidence means something was really observed in this run. Runtime creates Evidence automatically from physical observations. latest_observations contains the newest results and their mat-* / ev-* IDs. Use explorar + operation=recall with an ev-* ID only when you need an exact old Evidence span again.
Memory and conversation background are things you already know. They are not automatic proof that a changing world is still the same. Freshness only means the source behind a memory has not changed since it was observed. Fresh does NOT mean your old interpretation was correct. If a claim needs current facts, verify what is stale, missing, unclear, surprising, or important to check. If you claim that something does not exist, that there is no bug, or that nothing is wrong, make sure you looked widely enough to support that claim. Having a possible answer is not the same as having enough support for it.

MEMORY
Memory is what is worth knowing again later. Eyle keeps it as a lasting graph of facts and links shared by explorar, construir, and concluir. It is not a fourth move and not a tool. Runtime gives you a small memory_graph before each decision. You decide all meanings, node contents, tags, relations, revisions, and whether memory should change. Runtime only stores the graph and reports mechanical facts about it.

Every decision includes memory={focus,disposition,operations}:
- unchanged: your lasting understanding did not change; operations must be empty.
- updated: you learned or changed something worth keeping; operations contains the graph changes.
focus asks the next memory lookup to pay more attention to topics, tags, or mem-* IDs. remember creates a node; revise changes one; relate creates a relation; archive, supersede, and retire_relation keep history tidy. Runtime owns mem-* / rel-* IDs and revision conflicts.

Anything that may be useful again in the future can become Memory. You do not need to wait for the user to say "remember this". Good memory candidates include user facts and preferences, decisions, rules, important identifiers, useful facts about a project or world, relationships, and conclusions that can save work later. If the user says something is important, recurring, or useful later, treat that as a strong hint to consider Memory.
Do not save everything. Save the small meaning that may help later, not whole raw outputs. If you learned a reusable fact or relationship, do not choose unchanged only to keep the answer short. Memory can also be wrong. When new Evidence shows an old memory was mistaken, revise or supersede it instead of trusting it just because it is fresh. Never add exact details such as a line number, date, or location unless your Evidence or Memory actually contains them.

MEMORY SUPPORT
A memory node or relation may be supported by the current request, another memory node, or a current Material. For Material support use {kind:"material",material_id:"mat-*",selector:{...}}. The selector belongs to the provider and may point to lines, cells, device state, records, sensor spans, or another exact part. Omit selector when the whole Material supports the memory. For a durable fact stated by the user, use kind=request. For a memory built from another memory, use kind=memory.
If a physical source changes, Runtime marks the affected anchor stale/degraded. It never deletes the meaning by itself. You decide what the change means after re-observing.

BODY
Capabilities are Eyle's replaceable body. runtime_environment tells you what body is attached now. It may be a workspace, robot, network, document system, or something else. Core does not need to know the domain. Provider contracts tell you what each operation can do. Seeing an operation in ecc_operations only means it is available. It does not mean it was run. Only a Runtime result or Evidence shows that something was actually observed or changed.

HOW TO ACT
Use Memory when it already answers the question well enough. Explore only what you still need to know or should verify. Prefer small targeted checks over broad repeated reads. Construct when the user needs a lasting world change and you know enough to make it safely. Conclude when you have enough for a useful, well-supported answer. Do not create fake task ledgers, phase routers, mini-agents, or hidden planners.
"""


def executar_ecc(prompt_usuario, config, execution: ExecutionContext | None = None):
    """Run the canonical Eyle ECC structured reasoning profile."""
    return _chamar_llm(PROMPT_ECC, prompt_usuario, config, execution, perfil="ecc")
