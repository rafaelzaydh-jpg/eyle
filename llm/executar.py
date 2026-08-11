#!/usr/bin/env python3
"""Adaptador LLM da Eyle 2.7.4.

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

from eyle.core.execution_context import ExecutionContext, current_execution  # noqa: E402
from eyle.core.token_budget import estimate_tokens as estimar_tokens  # noqa: E402
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


def _latest_attempt(execution: ExecutionContext | None):
    if execution is None:
        return None
    call = execution.latest_call()
    attempts = call.get("attempts") if isinstance(call, dict) else None
    return attempts[-1] if isinstance(attempts, list) and attempts else None


def _registrar_metadata_runtime(execution: ExecutionContext | None, metadata):
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
    return execution.add_attempt(call, clean)


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
        code = (
            "LLM_STRUCTURED_OUTPUT_UNAVAILABLE"
            if perfil is not None and getattr(exc, "code", None) in (400, 404, 422)
            else "HTTP_ERROR"
        )
        raise ErroLLM(
            _mensagem_http_error(base_url, exc, body),
            transient=False,
            status_code=getattr(exc, "code", None),
            error_code=code,
        ) from exc

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


def _reservar_orcamento_llm(execution: ExecutionContext | None):
    if execution is None:
        return
    if len(execution.llm_calls) > int(execution.max_llm_calls):
        raise ErroLLM("O limite global de chamadas LLM da tarefa foi atingido.", transient=False, error_code="MAX_LLM_CALLS_EXCEEDED")


def _completion_budget_remaining(execution: ExecutionContext | None):
    if execution is None:
        return None
    return max(0, int(execution.max_completion_tokens) - int(execution.completion_tokens_actual or 0))


def _preflight_completion_budget(config, execution: ExecutionContext | None, max_tokens, *, pending_completion_tokens=0):
    """Fit the next output ceiling inside the remaining physical budget.

    ``max_tokens`` is a ceiling, not a prepaid allocation. The Runtime protects
    the next mandatory downstream semantic stage, then clamps the current call
    to whatever can still physically fit. It fails only when no positive output
    budget remains for the current call.
    """
    requested = max(0, int(max_tokens or 0))
    pending = max(0, int(pending_completion_tokens or 0))
    if execution is None:
        return {
            "remaining": None, "requested": requested, "effective": requested,
            "pending": pending, "downstream_reserve": 0, "clamped": False,
        }
    remaining = _completion_budget_remaining(execution)
    cfg_llm = (config or {}).get("llm") or {}
    try:
        downstream = max(0, int(cfg_llm.get("downstream_completion_reserve_tokens", 0) or 0))
    except (TypeError, ValueError):
        downstream = 0
    available = max(0, int(remaining or 0) - pending - downstream)
    effective = min(requested, available) if requested > 0 else 0
    if requested > 0 and effective <= 0:
        raise ErroLLM(
            "Não resta orçamento positivo de saída para a próxima chamada LLM após preservar a etapa obrigatória seguinte.",
            transient=False, error_code="MAX_COMPLETION_BUDGET_INSUFFICIENT",
        )
    return {
        "remaining": remaining, "requested": requested, "effective": effective,
        "pending": pending, "downstream_reserve": downstream,
        "available_for_call": available, "clamped": bool(requested > 0 and effective < requested),
    }


def _prompt_cache_weight(config):
    context = (config or {}).get("context_engine") or {}
    try:
        value = float(context.get("cached_prompt_weight", 0.2))
    except (TypeError, ValueError):
        value = 0.2
    return min(1.0, max(0.0, value))


def _reservar_requisicao_llm(config, execution: ExecutionContext | None, prompt_sistema, prompt_usuario, max_tokens):
    """Preflight and account one real backend request.

    The provider still receives the complete prompt on every request, but the
    task-wide budget does not charge an identical system prefix at full weight
    forever. Real provider cache metadata replaces this estimate after the
    response arrives. Context-window safety remains based on the full prompt.
    """
    if execution is None:
        return {"estimated_prompt_tokens": 0, "estimated_effective_tokens": 0}
    completion_fit = _preflight_completion_budget(config, execution, max_tokens)
    if max_tokens is not None:
        max_tokens = int(completion_fit.get("effective") or 0)
    cfg_llm = (config or {}).get("llm", {})
    cfg_context = (config or {}).get("context_engine", {})
    chars_per_token = max(1, int(cfg_context.get("chars_per_token_fallback", 3) or 3))
    system_tokens = estimar_tokens(prompt_sistema, chars_per_token)
    user_tokens = estimar_tokens(prompt_usuario, chars_per_token)
    prompt_tokens = system_tokens + user_tokens
    multiplier = execution.prompt_token_calibration if execution is not None else 1.0
    calibrated_prompt_tokens = int(math.ceil(prompt_tokens * max(1.0, float(multiplier))))
    response_reserved = max(0, int(max_tokens or 0))
    margin = max(0, int(cfg_context.get("safety_margin_tokens", 256) or 0))
    window = min(32768, max(1, int(cfg_llm.get("context_window_tokens", 32768) or 32768)))
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
    max_prompt = int(execution.max_prompt_tokens or 0)
    if max_prompt > 0 and current_prompt_physical + reserved_prompt_tokens > max_prompt:
        raise ErroLLM(
            "O limite físico acumulado de tokens de entrada da mensagem seria excedido.",
            transient=False, error_code="MAX_PROMPT_TOKENS_EXCEEDED",
        )
    current_completion = int(execution.completion_tokens_actual or 0)
    max_total = int(execution.max_total_tokens or 0)
    if max_total > 0 and current_prompt_physical + current_completion + reserved_prompt_tokens + response_reserved > max_total:
        raise ErroLLM(
            "O limite físico total de tokens da mensagem seria excedido pela próxima chamada.",
            transient=False, error_code="MAX_TOTAL_TOKENS_EXCEEDED",
        )

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
    max_tokens = int(execution.max_completion_tokens or 0)
    execution.completion_tokens_actual = total
    if max_tokens > 0 and total > max_tokens:
        raise ErroLLM(
            "O limite global de tokens de saída da tarefa foi excedido.",
            transient=False, error_code="MAX_COMPLETION_TOKENS_EXCEEDED",
        )
    max_total = int(execution.max_total_tokens or 0)
    # Cache discounts remain diagnostic only. The hard 98k message budget is
    # physical: full prompt attempts plus generated output.
    physical_total = int(execution.prompt_tokens_estimated_raw or 0) + total
    if max_total > 0 and physical_total > max_total:
        raise ErroLLM(
            "O limite físico total de tokens da mensagem foi excedido.",
            transient=False, error_code="MAX_TOTAL_TOKENS_EXCEEDED",
        )


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

    job_progress.publicar(
        execution, "llm_wait", "Aguardando a LLM local",
        profile=perfil or "default",
    )
    completion_fit = _preflight_completion_budget(config, execution, max_tokens)
    if max_tokens is not None:
        max_tokens = int(completion_fit.get("effective") or 0)
    latest_call = execution.latest_call() if execution is not None else None
    if isinstance(latest_call, dict):
        prompt_meta = latest_call.setdefault("prompt", {})
        prompt_meta.setdefault("output_tokens_requested", int(completion_fit.get("requested") or 0))
        prompt_meta["output_tokens_reserved"] = int(max_tokens or 0)
        prompt_meta["completion_budget_remaining_before_call"] = completion_fit.get("remaining")
        prompt_meta["downstream_completion_reserve_tokens"] = int(completion_fit.get("downstream_reserve") or 0)
        prompt_meta["completion_ceiling_clamped"] = bool(
            prompt_meta.get("completion_ceiling_clamped") or completion_fit.get("clamped")
        )
    _reservar_orcamento_llm(execution)

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
        job_progress.job_id_de(config) is not None
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
                    config, perfil, bool(stream_visible), chars_por_token,
                )
                if streaming_ativado else None
            )
            try:
                def chamar_backend(token_limit, callback):
                    _LLM_RESPONSE_LOCAL.metadata = {}
                    inicio_backend = time.monotonic()
                    reservations = []

                    def before_request():
                        reservations.append(_reservar_requisicao_llm(
                            config, execution, prompt_sistema, prompt_usuario, token_limit,
                        ))

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
                    metadata_resposta.update({"truncated": True})
                    _registrar_metadata_runtime(execution, metadata_resposta)
                    metadata_chamadas.append(dict(metadata_resposta))
                    _registrar_tokens_gerados(config, execution, resposta, metadata_chamadas)
                    raise ErroLLM(
                        "A resposta do modelo foi interrompida pelo limite físico de saída.",
                        transient=False, error_code="MODEL_OUTPUT_TRUNCATED",
                    )
                _registrar_metadata_runtime(execution, metadata_resposta)
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
                repetir_timeout = bool(cfg_llm.get("retry_read_timeouts", True))
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
                    transient=bool(cfg_llm.get("retry_read_timeouts", True)),
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
            parsed_response = parse_profile_response(resposta, perfil or "agent")
            last = _latest_attempt(execution)
            if isinstance(last, dict):
                last["structured_parse_status"] = "valid"
                last["structured_profile"] = perfil or "agent"
                last["structured_top_level_keys"] = sorted(parsed_response.keys())
        except StructuredResponseError as error:
            observed = observed_top_level(resposta)
            observed_keys = sorted(observed.keys()) if isinstance(observed, dict) else []
            required_keys = list(mandatory_top_level_keys(perfil or "agent"))
            missing_keys = [key for key in required_keys if key not in observed_keys]
            last = _latest_attempt(execution)
            if isinstance(last, dict):
                last["structured_parse_status"] = "invalid"
                last["structured_parse_error"] = error.code
                last["structured_parse_detail"] = error.detail
                last["structured_profile"] = perfil or "agent"
                last["structured_top_level_keys"] = observed_keys
                last["structured_missing_keys"] = missing_keys

            raise ErroLLM(
                f"Structured response for {perfil or 'agent'} is invalid: {error.detail}",
                transient=False,
                error_code=f"STRUCTURED_RESPONSE_INVALID:{perfil or 'agent'}:{error.code}",
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
            task_id=execution.task_id, job_id=execution.source_job_id,
            metadata=metadata,
        )



PROMPT_AGENTE = """You are Eyle. JSON only.

Return exactly: tool_calls, patches, needs_user, final, investigation_updates. Exactly one action payload is non-null. final={answer,limitations,evidence_ids}; needs_user={question,missing_information}; investigation_updates={id,goal,status,evidence_ids,reason}[], status=open|established|dismissed.

You are the semantic authority. request is canonical; conversation_background is context. Decide goals, capabilities, relevance, semantic debt, Evidence admission and stopping. Runtime owns physical/structural contracts, never intent/relevance/sufficiency. needs_user only when an active concrete task cannot continue without one specific fact/choice; never use it for greetings; otherwise return final. A resumed clarification becomes part of the canonical request.

Investigation is YOUR semantic working memory, never a Runtime requirement. Keep it empty unless debt must survive a turn. Preserve targets. established requires Evidence; dismissed requires reason. Establish the exact requested property: names, references, imports, tests and signatures prove only what they observe. For candidate checks, either polarity is a valid result. Stop when Evidence discriminates it. After Claim feedback, address semantic_gaps[].required_property for the same target/candidate before exploring another. After one material non-redundant failed attempt, conclude with the limitation.

Capabilities may exhaust an objective property and deterministically compress/group it into SourceRecords, coverage, frontiers and handle:* continuations. src-* is observed/citable material, NOT admitted Evidence. Select material src-* in Investigation/final evidence_ids to promote; never promote breadcrumbs merely because observed. YOU decide relevance, frontier materiality and semantic sufficiency. projection_complete=false means more objective results are behind handles, not incomplete coverage. expand_observation accepts exact handle:* only.

symbol_relations reports structural facts, never liveness semantics. For root/path questions prefer query=reachability and omit roots for objective Python entrypoints. Reachability exhausts the resolved graph automatically: never tune max_depth/max_edges or walk a proven path node-by-node. objective_complete=false is not absence: resolve only a material expandable frontier or conclude with the remaining boundary. Request text references only when they discriminate the property; otherwise leave include_text_references false.

run_command is unrestricted only inside its job sandbox and never writes the real workspace. Use tools only when useful; batch at most 4 independent calls; never repeat retained observations unless reality changed.

Real workspace writes use one transaction: replace/create={operation,path,content}; delete={operation,path}; update={operation,path,line_start,line_end,new_code}. Dry-run never writes; confirmed apply does. Never claim an unconfirmed write.

Final: lead with the result and match language. Be concise without erasing requested distinctions established by reality. Requested quantity never licenses invention: if fewer items are established, return them and state the limitation; non-exhaustive requests need not enumerate extras. Hide Evidence/Claim mechanics unless asked.
"""

PROMPT_CLAIM_VERIFIER = """You are Eyle Claim Verifier. Return JSON only. You are the independent semantic auditor. Never request tools, edits, plans or create Investigation.

There is one task only: verify_claims. Audit the complete provisional answer against the canonical request and supplied grounding coordinates. Every response MUST include material_satisfaction={status,grounding_refs,reason}, answer_consistency={status,grounding_refs,reason}, claims and semantic_gaps.

Grounding coordinates are typed and literal. Copy supplied refs exactly; never construct or infer coordinates. `request` denotes the full request; request anchors are literal coordinates such as request:r1, not semantic requirements or counts. Other refs include answer:a1, evidence:ev-0001, runtime:r1 and investigation:inv-1.
Grounding is not synonymous with EvidenceLedger. Omissions or instruction failures may be grounded by request + answer anchors. Physical impossibility may be grounded by runtime facts. Source-code assertions normally require source Evidence.

First judge material delivery against the request's actual semantic obligations, not bullet/anchor cardinality. Truth and grounding outrank requested quantity: never treat missing real facts as a reason to invent them. material_satisfaction.status is satisfied|gap|blocked. Use blocked only when the requested action cannot physically be completed in the current execution and the answer accurately reports that limitation; blocked MUST cite at least one runtime:* grounding. A truthful grounded blocked outcome is a valid final delivery, not a reason to demand impossible repeated execution. Then judge answer consistency. Then atomize every materially necessary factual/architectural/bug/risk/recommendation Claim. Finally audit declared Investigation closure.

For each Claim return exactly: answer_ref,target_id,statement,grounding_refs,verdict,reason. answer_ref copies a supplied answer:* ref. target_id copies a supplied investigation:* ref when applicable, otherwise null. verdict=supported|contradicted|insufficient. grounding_refs must be non-empty. Cite the coordinates that genuinely discriminate the proposition; do not cite the answer itself as circular proof of an external fact. If the available coordinates do not establish the proposition, use insufficient.

Investigation is semantic debt declared by the Main LLM, not by Runtime. Empty Investigation is valid. If a materially required property was not established, emit scope_gap. If an existing target was falsely closed, tie the gap/insufficient Claim to its supplied investigation:* target_id. Do not accept an easier proxy: definitions, references, imports, compilation, tests, signatures, successful commands, names or markers prove only what they directly observe unless they genuinely discriminate the requested property.

semantic_gaps items contain exactly type,target_id,grounding_refs,required_property,reason. type=material_omission|conflicting_evidence|scope_gap. grounding_refs must be non-empty. required_property states the unresolved material property precisely enough that the Main LLM can decide how to establish or narrow it; do not prescribe tools. material_omission may cite request and answer anchors without source Evidence. conflicting_evidence cites the conflicting coordinates. scope_gap may cite request and/or Investigation when the missing proof was never gathered.

answer_consistency={status,grounding_refs,reason}, status=consistent|conflict. Ground it in answer anchors. Return no unsupported administrative prose.
"""


def executar_agente(prompt_usuario, config, execution: ExecutionContext | None = None):
    """Run the canonical structured Eyle agent reasoning profile."""
    return _chamar_llm(
        PROMPT_AGENTE, prompt_usuario, config, execution,
        perfil="agent",
    )


def executar_verificador_claims(prompt_usuario, config, execution: ExecutionContext | None = None):
    """Run a bounded semantic review pass with no tool authority."""
    return _chamar_llm(
        PROMPT_CLAIM_VERIFIER, prompt_usuario, config, execution,
        perfil="claim_verifier",
    )
