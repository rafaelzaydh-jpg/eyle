#!/usr/bin/env python3
"""Adaptador LLM da Eyle 2.7.4.

Transporta o único protocolo AgentSession para o backend configurado.
"""
import hashlib
import json
import random
import re
import socket
import sys
import threading
import time
import urllib.request
import urllib.error
from contextlib import contextmanager

from eyle.core.token_budget import estimate_tokens as estimar_tokens  # noqa: E402
from eyle.runtime import telemetry  # noqa: E402
from eyle.runtime import limiter  # noqa: E402
from eyle.runtime import progress as job_progress  # noqa: E402
from llm.response_adapter import NormalizedModelResponse, normalize_model_response  # noqa: E402


class ErroLLM(RuntimeError):
    """Falha de transporte/backend; nunca representa uma resposta do modelo."""

    def __init__(self, mensagem, *, transient=False, status_code=None,
                 retry_after=None, error_code=None):
        super().__init__(mensagem)
        self.transient = bool(transient)
        self.status_code = status_code
        self.retry_after = retry_after
        self.error_code = error_code


# Deteccao basica, somente em memoria, do servidor OpenAI-compativel.
# Evita gravar estado novo no projeto e reaprende a cada reinicio da Eyle.
_CAPACIDADES_OPENAI = {}
_MODELOS_OPENAI = {}
_SEMAFOROS_LLM = {}
_SEMAFOROS_LOCK = threading.Lock()
_COOLDOWN_ATE = {}
_COOLDOWN_LOCK = threading.Lock()
_LLM_RESPONSE_LOCAL = threading.local()
_RE_BLOCO_RACIOCINIO = re.compile(
    r"<(?:think|analysis|reasoning)>.*?</(?:think|analysis|reasoning)>",
    re.IGNORECASE | re.DOTALL,
)

# Schema pequeno e estavel do protocolo interno do Agente. Em llama-server
# moderno, enviar um schema explicito ativa a gramatica de JSON; o simples
# {"type":"json_object"} pode ser aceito pelo HTTP sem realmente impedir
# texto livre em algumas builds/modelos.
_SCHEMA_DECISAO_AGENTE = {
    "type": "object",
    "properties": {
        "tool": {"type": "string"},
        "arguments": {"type": "object"},
        "tool_call": {"type": "object", "additionalProperties": True},
        "tool_calls": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "actions": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "patches": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "decision": {"type": "object", "additionalProperties": True},
        "final": {"anyOf": [{"type": "string"}, {"type": "object", "additionalProperties": True}]},
        "needs_user": {"type": "string"}
    },
    "additionalProperties": True
}


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
    """Consulta /v1/models com cache positivo e negativo temporario.

    llama-server normalmente expoe um unico modelo/alias. A deteccao evita
    que um nome antigo em config.json derrube a comunicacao depois de trocar
    o GGUF carregado. Servidores sem esse endpoint continuam usando o nome
    configurado, sem mudar o comportamento anterior.
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
                "fallback_strategy": "configured_model",
            },
        )
        return []

    modelos = []
    for item in corpo.get("data", []) if isinstance(corpo, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            modelos.append(item["id"].strip())
    # Resposta valida, inclusive lista vazia, tambem recebe TTL negativo para
    # impedir nova consulta em cada decisao do agente.
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
            metadata={"base_url": chave, "fallback_strategy": "configured_model"},
        )
    return modelos


def _resolver_modelo_openai(base_url, model, timeout, negative_ttl=60):
    """Prefere o modelo configurado; corrige automaticamente o caso comum
    de llama-server com um unico modelo carregado e config desatualizada."""
    configurado = str(model or "").strip()
    modelos = _detectar_modelos_openai(base_url, timeout, negative_ttl=negative_ttl)
    if not modelos:
        return configurado
    if configurado in modelos:
        return configurado
    if configurado.lower() == "auto" or len(modelos) == 1:
        return modelos[0]
    return configurado


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


def _erro_pode_ser_incompatibilidade(erro, corpo_erro):
    """400/404/422 sao os codigos usuais para campo/role nao suportado."""
    return getattr(erro, "code", None) in (400, 404, 422)


def _limpar_resposta_estruturada(texto):
    """Remove raciocinio visivel comum antes do JSON do agente.

    Modelos thinking podem devolver <think>...</think> mesmo quando o servidor
    nao separa reasoning_content. O conteudo de chat normal nao e alterado.
    """
    limpo = _RE_BLOCO_RACIOCINIO.sub("", texto or "").strip()
    if limpo.startswith("```json") and limpo.endswith("```"):
        limpo = limpo[7:-3].strip()
    elif limpo.startswith("```") and limpo.endswith("```"):
        limpo = limpo[3:-3].strip()
    return limpo


def _metadata_resposta_normalizada(normalizada):
    return {
        "finish_reason": normalizada.finish_reason,
        "prompt_tokens": normalizada.prompt_tokens,
        "cached_prompt_tokens": normalizada.cached_prompt_tokens,
        "completion_tokens": normalizada.completion_tokens,
        "reasoning_tokens": normalizada.reasoning_tokens,
        "provider_model": normalizada.model,
        "response_id": normalizada.response_id,
        "partial_json": bool(normalizada.partial_json),
        "streaming": bool(normalizada.streaming),
    }


def _registrar_metadata_backend(normalizada):
    if not isinstance(normalizada, NormalizedModelResponse):
        normalizada = normalize_model_response(normalizada)
    _LLM_RESPONSE_LOCAL.metadata = _metadata_resposta_normalizada(normalizada)
    return normalizada


def _ultima_metadata_backend():
    return dict(getattr(_LLM_RESPONSE_LOCAL, "metadata", {}) or {})


def _texto_normalizado_backend(valor, *, permitir_raciocinio=False, streaming=False):
    normalizada = _registrar_metadata_backend(
        normalize_model_response(valor, streaming=streaming)
    )
    return normalizada.usable_text(allow_reasoning=permitir_raciocinio)


def _finish_reason_truncado(metadata):
    reason = str((metadata or {}).get("finish_reason") or "").strip().lower()
    return reason in {"length", "max_tokens", "max_output_tokens", "token_limit"}


def _registrar_metadata_runtime(config, metadata):
    runtime = (config or {}).get("_runtime_agent_budget")
    if not isinstance(runtime, dict):
        return
    clean = {key: value for key, value in dict(metadata or {}).items() if value is not None}
    runtime["last_llm_response"] = clean
    history = runtime.setdefault("llm_responses", [])
    history.append(clean)
    del history[:-50]


def _chamar_ollama(
    base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
    forcar_json=False, max_tokens=None, read_timeout=None, on_chunk=None,
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
    if forcar_json:
        payload["format"] = "json"
    dados = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json"})
    with _abrir_url(req, timeout, read_timeout or timeout) as resp:
        if on_chunk is None:
            bruto = resp.read().decode("utf-8", errors="replace")
            try:
                corpo = json.loads(bruto)
            except json.JSONDecodeError:
                corpo = bruto
            return _texto_normalizado_backend(
                corpo, permitir_raciocinio=bool(forcar_json),
            )

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
            delta = _texto_normalizado_backend(corpo, streaming=True)
            if delta:
                partes.append(delta)
            on_chunk(delta, ultimo, bool(ultimo.get("done")))
        on_chunk("", ultimo, True)
        return "".join(partes)


def _chamar_openai_compatible(
    base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
    forcar_json=False, max_tokens=None, usar_system_role=True,
    desativar_raciocinio=False, recuperar_reasoning_content=False,
    read_timeout=None, on_chunk=None,
):
    url = _endpoint_openai(base_url, "chat/completions")
    if usar_system_role:
        messages = [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario},
        ]
    else:
        messages = [{
            "role": "user",
            "content": (
                "SYSTEM INSTRUCTIONS:\n" + prompt_sistema +
                "\n\nUSER MESSAGE:\n" + prompt_usuario
            ),
        }]

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": bool(on_chunk),
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if forcar_json:
        payload["response_format"] = {
            "type": "json_object",
            "schema": _SCHEMA_DECISAO_AGENTE,
        }
        if desativar_raciocinio:
            payload["reasoning_effort"] = "none"
            payload["chat_template_kwargs"] = {"enable_thinking": False}
    dados = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json"})
    with _abrir_url(req, timeout, read_timeout or timeout) as resp:
        if on_chunk is not None:
            partes = []
            ultimo = {}
            terminou = False
            for linha_bruta in resp:
                linha = linha_bruta.decode("utf-8", errors="replace").strip()
                if not linha:
                    continue
                if linha.startswith("data:"):
                    linha = linha[5:].strip()
                if linha == "[DONE]":
                    terminou = True
                    break
                try:
                    corpo = json.loads(linha)
                except json.JSONDecodeError:
                    corpo = linha
                ultimo = corpo if isinstance(corpo, dict) else ultimo
                # O adapter reconhece content e reasoning_content. Como este
                # caminho so e usado para resposta textual visivel, raciocinio
                # permanece privado e nunca entra no callback publico.
                delta = _texto_normalizado_backend(corpo, streaming=True)
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
    return _texto_normalizado_backend(
        corpo,
        permitir_raciocinio=bool(forcar_json or recuperar_reasoning_content),
    )


def _chamar_openai_com_fallback(
    base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
    forcar_json=False, max_tokens=None, read_timeout=None, on_chunk=None,
    on_request=None,
):
    """Detect common OpenAI-compatible incompatibilities with accounting."""
    chave = (str(base_url).rstrip("/"), str(model))
    capacidades = _CAPACIDADES_OPENAI.setdefault(
        chave, {"json_mode": None, "system_role": None, "reasoning_controls": None},
    )
    capacidades.setdefault("reasoning_controls", None)
    usar_json_nativo = bool(forcar_json and capacidades["json_mode"] is not False)
    usar_system = capacidades["system_role"] is not False
    usar_controles_raciocinio = bool(
        forcar_json and capacidades["reasoning_controls"] is not False
    )

    def request(**kwargs):
        if on_request is not None:
            on_request()
        return _chamar_openai_compatible(
            base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
            max_tokens=max_tokens, read_timeout=read_timeout, on_chunk=on_chunk,
            **kwargs,
        )

    try:
        resposta = request(
            forcar_json=usar_json_nativo,
            usar_system_role=usar_system,
            desativar_raciocinio=usar_controles_raciocinio,
            recuperar_reasoning_content=forcar_json,
        )
        if usar_json_nativo:
            capacidades["json_mode"] = True
        if usar_system:
            capacidades["system_role"] = True
        if usar_controles_raciocinio:
            capacidades["reasoning_controls"] = True
        return resposta
    except urllib.error.HTTPError as primeiro_erro:
        erro_inicial = primeiro_erro
        primeiro_corpo = _ler_corpo_http_error(primeiro_erro)
        if not _erro_pode_ser_incompatibilidade(primeiro_erro, primeiro_corpo):
            raise _erro_http(base_url, primeiro_erro, primeiro_corpo) from primeiro_erro

    if usar_json_nativo and usar_controles_raciocinio:
        try:
            resposta = request(
                forcar_json=True, usar_system_role=usar_system,
                desativar_raciocinio=False,
                recuperar_reasoning_content=forcar_json,
            )
            capacidades["reasoning_controls"] = False
            capacidades["json_mode"] = True
            if usar_system:
                capacidades["system_role"] = True
            return resposta
        except urllib.error.HTTPError as erro_sem_controles:
            corpo_sem_controles = _ler_corpo_http_error(erro_sem_controles)
            if not _erro_pode_ser_incompatibilidade(
                erro_sem_controles, corpo_sem_controles,
            ):
                raise _erro_http(
                    base_url, erro_sem_controles, corpo_sem_controles,
                ) from erro_sem_controles
            capacidades["reasoning_controls"] = False

    if usar_json_nativo:
        try:
            resposta = request(
                forcar_json=False, usar_system_role=usar_system,
                recuperar_reasoning_content=forcar_json,
            )
            capacidades["json_mode"] = False
            if usar_system:
                capacidades["system_role"] = True
            return resposta
        except urllib.error.HTTPError as segundo_erro:
            erro_apos_json = segundo_erro
            segundo_corpo = _ler_corpo_http_error(segundo_erro)
            if not _erro_pode_ser_incompatibilidade(segundo_erro, segundo_corpo):
                raise _erro_http(base_url, segundo_erro, segundo_corpo) from segundo_erro
    else:
        erro_apos_json = erro_inicial
        segundo_corpo = primeiro_corpo

    if usar_system:
        try:
            resposta = request(
                forcar_json=False, usar_system_role=False,
                recuperar_reasoning_content=forcar_json,
            )
            capacidades["system_role"] = False
            if usar_json_nativo:
                capacidades["json_mode"] = False
            return resposta
        except urllib.error.HTTPError as terceiro_erro:
            terceiro_corpo = _ler_corpo_http_error(terceiro_erro)
            raise _erro_http(base_url, terceiro_erro, terceiro_corpo) from terceiro_erro

    raise _erro_http(base_url, erro_apos_json, segundo_corpo) from erro_apos_json


def _timeout_restante(config):
    runtime = (config or {}).get("_runtime_agent_budget") or {}
    deadline = runtime.get("deadline_monotonic")
    if deadline is None:
        return None
    return max(0.0, float(deadline) - time.monotonic())


def _reservar_orcamento_llm(config):
    """Conta a chamada no ponto comum a todas as chamadas LLM, antes do envio ao backend."""
    runtime = (config or {}).get("_runtime_agent_budget")
    if not isinstance(runtime, dict):
        return
    max_calls = int(runtime.get("max_llm_calls", 0) or 0)
    atual = int(runtime.get("llm_calls", 0) or 0)
    if max_calls > 0 and atual >= max_calls:
        raise ErroLLM(
            "O limite global de chamadas LLM da tarefa foi atingido.",
            transient=False, error_code="MAX_LLM_CALLS_EXCEEDED",
        )
    runtime["llm_calls"] = atual + 1


def _prompt_cache_weight(config):
    context = (config or {}).get("context_engine") or {}
    try:
        value = float(context.get("cached_prompt_weight", 0.2))
    except (TypeError, ValueError):
        value = 0.2
    return min(1.0, max(0.0, value))


def _reservar_requisicao_llm(config, prompt_sistema, prompt_usuario, max_tokens):
    """Preflight and account one real backend request.

    The provider still receives the complete prompt on every request, but the
    task-wide budget does not charge an identical system prefix at full weight
    forever. Real provider cache metadata replaces this estimate after the
    response arrives. Context-window safety remains based on the full prompt.
    """
    runtime = (config or {}).get("_runtime_agent_budget")
    if not isinstance(runtime, dict):
        return {"estimated_prompt_tokens": 0, "estimated_effective_tokens": 0}
    cfg_llm = (config or {}).get("llm", {})
    cfg_context = (config or {}).get("context_engine", {})
    chars_per_token = max(1, int(cfg_context.get("chars_per_token_fallback", 3) or 3))
    system_tokens = estimar_tokens(prompt_sistema, chars_per_token)
    user_tokens = estimar_tokens(prompt_usuario, chars_per_token)
    prompt_tokens = system_tokens + user_tokens
    response_reserved = max(0, int(max_tokens or 0))
    margin = max(0, int(cfg_context.get("safety_margin_tokens", 256) or 0))
    window = max(1, int(cfg_llm.get("context_window_tokens", 32768) or 32768))
    if prompt_tokens + response_reserved + margin > window:
        raise ErroLLM(
            "O prompt e a saída reservada excedem a janela de contexto do modelo.",
            transient=False, error_code="PROMPT_CONTEXT_BUDGET_EXCEEDED",
        )

    system_hash = hashlib.sha256(str(prompt_sistema or "").encode("utf-8")).hexdigest()
    seen_hashes = runtime.setdefault("system_prompt_hashes", [])
    repeated_system = system_hash in seen_hashes
    if not repeated_system:
        seen_hashes.append(system_hash)
        del seen_hashes[:-8]
    cache_weight = _prompt_cache_weight(config)
    effective_estimate = user_tokens + int(round(system_tokens * (cache_weight if repeated_system else 1.0)))

    current_prompt = int(runtime.get("prompt_tokens_effective", 0) or 0)
    max_prompt = int(runtime.get("max_prompt_tokens", 0) or 0)
    if max_prompt > 0 and current_prompt + effective_estimate > max_prompt:
        raise ErroLLM(
            "O limite global efetivo de tokens de entrada da tarefa seria excedido.",
            transient=False, error_code="MAX_PROMPT_TOKENS_EXCEEDED",
        )
    current_completion = int(runtime.get("generated_tokens", 0) or 0)
    max_total = int(runtime.get("max_total_tokens", 0) or 0)
    if max_total > 0 and current_prompt + current_completion + effective_estimate + response_reserved > max_total:
        raise ErroLLM(
            "O limite global efetivo de tokens da tarefa seria excedido pela próxima chamada.",
            transient=False, error_code="MAX_TOTAL_TOKENS_EXCEEDED",
        )

    runtime["llm_requests"] = int(runtime.get("llm_requests", 0) or 0) + 1
    runtime["prompt_tokens_reserved"] = int(runtime.get("prompt_tokens_reserved", 0) or 0) + prompt_tokens
    runtime["prompt_tokens_estimated_raw"] = int(runtime.get("prompt_tokens_estimated_raw", 0) or 0) + prompt_tokens
    runtime["prompt_tokens_effective"] = current_prompt + effective_estimate
    return {
        "estimated_prompt_tokens": prompt_tokens,
        "estimated_effective_tokens": effective_estimate,
        "estimated_system_tokens": system_tokens,
        "estimated_user_tokens": user_tokens,
        "repeated_system_prompt": repeated_system,
        "finalized": False,
    }


def _finalizar_requisicao_llm(config, reservation, metadata):
    runtime = (config or {}).get("_runtime_agent_budget")
    if not isinstance(runtime, dict) or not isinstance(reservation, dict):
        return
    if reservation.get("finalized"):
        return
    estimated_effective = int(reservation.get("estimated_effective_tokens", 0) or 0)
    estimated_raw = int(reservation.get("estimated_prompt_tokens", 0) or 0)
    actual = (metadata or {}).get("prompt_tokens")
    cached = (metadata or {}).get("cached_prompt_tokens")
    if isinstance(actual, (int, float)):
        actual = max(0, int(actual))
        runtime["prompt_tokens_actual"] = int(runtime.get("prompt_tokens_actual", 0) or 0) + actual
        if isinstance(cached, (int, float)):
            cached = min(actual, max(0, int(cached)))
            uncached = max(0, actual - cached)
            effective_actual = uncached + int(round(cached * _prompt_cache_weight(config)))
            runtime["prompt_tokens_cached"] = int(runtime.get("prompt_tokens_cached", 0) or 0) + cached
            runtime["prompt_tokens_uncached"] = int(runtime.get("prompt_tokens_uncached", 0) or 0) + uncached
        elif estimated_raw > 0:
            # Preserve the repeated-prefix discount when the provider reports
            # only the total prompt count and no cache breakdown.
            ratio = estimated_effective / estimated_raw
            effective_actual = int(round(actual * ratio))
            runtime["prompt_tokens_uncached"] = int(runtime.get("prompt_tokens_uncached", 0) or 0) + actual
        else:
            effective_actual = actual
            runtime["prompt_tokens_uncached"] = int(runtime.get("prompt_tokens_uncached", 0) or 0) + actual
        runtime["prompt_tokens_effective"] = max(
            0, int(runtime.get("prompt_tokens_effective", 0) or 0) + effective_actual - estimated_effective,
        )
    reservation["finalized"] = True


def _registrar_tokens_gerados(config, resposta, metadata_respostas=None):
    runtime = (config or {}).get("_runtime_agent_budget")
    if not isinstance(runtime, dict):
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
    runtime["reasoning_tokens_actual"] = int(runtime.get("reasoning_tokens_actual", 0) or 0) + reasoning
    chars_por_token = max(
        1, int((config or {}).get("context_engine", {}).get("chars_per_token_fallback", 3)),
    )
    estimativa = sum(reais) if reais else (
        len(str(resposta or "")) + chars_por_token - 1
    ) // chars_por_token
    total = int(runtime.get("generated_tokens", 0) or 0) + estimativa
    max_tokens = int(runtime.get("max_completion_tokens", runtime.get("max_generated_tokens", 0)) or 0)
    runtime["generated_tokens"] = total
    runtime["completion_tokens_actual"] = total
    if max_tokens > 0 and total > max_tokens:
        raise ErroLLM(
            "O limite global de tokens de saída da tarefa foi excedido.",
            transient=False, error_code="MAX_COMPLETION_TOKENS_EXCEEDED",
        )
    max_total = int(runtime.get("max_total_tokens", 0) or 0)
    # Most OpenAI-compatible providers include reasoning in completion_tokens.
    # It is exposed separately for observability, but not double-counted here.
    effective_total = int(runtime.get("prompt_tokens_effective", 0) or 0) + total
    runtime["total_tokens_effective"] = effective_total
    runtime["provider_reported_tokens"] = (
        int(runtime.get("prompt_tokens_actual", 0) or 0)
        + int(runtime.get("completion_tokens_actual", 0) or 0)
    )
    if max_total > 0 and effective_total > max_total:
        raise ErroLLM(
            "O limite global de tokens da tarefa foi excedido.",
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


def _timeouts_da_chamada(cfg_llm, perfil, config):
    legado = float(cfg_llm.get("timeout_seconds", 180))
    connect_timeout = float(cfg_llm.get("connect_timeout_seconds", min(legado, 10)))
    perfil_chave = f"{perfil}_timeout_seconds" if perfil else None
    read_timeout = float(
        cfg_llm.get(perfil_chave, cfg_llm.get("read_timeout_seconds", legado))
        if perfil_chave else cfg_llm.get("read_timeout_seconds", legado)
    )
    restante = _timeout_restante(config)
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


def _criar_callback_stream(config, perfil, visivel, chars_por_token):
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
            config, "generating" if not done else "validating",
            mensagem if not done else "Geracao concluida; validando a resposta",
            force=bool(done), min_interval=0.18, **campos,
        )

    return callback


def _chamar_llm_impl(
    prompt_sistema, prompt_usuario, config, forcar_json=False, perfil=None,
    stream_visible=False,
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
        cfg_llm, perfil, config,
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

    job_progress.publicar(
        config, "llm_wait", "Aguardando a LLM local",
        profile=perfil or "default",
    )
    _reservar_orcamento_llm(config)

    chave_tentativas = (
        "agent_retry_max_attempts" if perfil == "agent" else "retry_max_attempts"
    )
    tentativas = max(
        1,
        int(cfg_llm.get(chave_tentativas, cfg_llm.get("retry_max_attempts", 3))),
    )
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
    restante = _timeout_restante(config)
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

    # Decisoes estruturadas podem vir apenas em reasoning_content. O parser de
    # streaming omite esse campo de proposito para nao publicar raciocinio
    # interno; por isso chamadas JSON precisam usar a resposta nao-streaming,
    # que ja possui a recuperacao segura desse campo.
    streaming_ativado = bool(
        job_progress.job_id_de(config) is not None
        and cfg_llm.get("stream_responses", True)
        and not forcar_json
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
                deadline=(config.get("_runtime_agent_budget") or {}).get("deadline_monotonic"),
            )
            connect_atual, read_atual, _ = _timeouts_da_chamada(cfg_llm, perfil, config)
            job_progress.publicar(
                config, "llm_request",
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
                            config, prompt_sistema, prompt_usuario, token_limit,
                        ))

                    if openai_compatible:
                        resposta_backend = _chamar_openai_com_fallback(
                            base_url, model, prompt_sistema, prompt_usuario, temperature,
                            connect_atual, forcar_json=forcar_json, max_tokens=token_limit,
                            read_timeout=read_atual, on_chunk=callback,
                            on_request=before_request,
                        )
                    else:
                        before_request()
                        resposta_backend = _chamar_ollama(
                            base_url, model, prompt_sistema, prompt_usuario, temperature,
                            connect_atual, forcar_json=forcar_json, max_tokens=token_limit,
                            read_timeout=read_atual, on_chunk=callback,
                        )
                    metadata_backend = _ultima_metadata_backend()
                    if reservations:
                        _finalizar_requisicao_llm(config, reservations[-1], metadata_backend)
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
                    "provider": str(cfg_llm.get("provider") or ("openai_compatible" if openai_compatible else "ollama")),
                    "profile": perfil or "default",
                    "max_tokens_requested": max_tokens,
                })
                if _finish_reason_truncado(metadata_resposta):
                    retry_limit = int(cfg_llm.get("truncation_retry_max_tokens", 2048) or 2048)
                    multiplier = max(1.1, float(cfg_llm.get("truncation_retry_multiplier", 2.0) or 2.0))
                    current = int(max_tokens or cfg_llm.get("max_tokens") or 700)
                    expanded = max(current + 256, int(current * multiplier))
                    expanded = min(expanded, retry_limit)
                    metadata_resposta.update({
                        "truncated": True,
                        "truncation_retry_planned": bool(expanded > current and not streaming_ativado),
                    })
                    _registrar_metadata_runtime(config, metadata_resposta)
                    metadata_chamadas.append(dict(metadata_resposta))
                    if expanded <= current or streaming_ativado:
                        raise ErroLLM(
                            "A resposta do modelo foi interrompida pelo limite de tokens.",
                            transient=False, error_code="MODEL_OUTPUT_TRUNCATED",
                        )
                    _diagnostico(
                        "MODEL_OUTPUT_TRUNCATED_RETRY", profile=perfil or "default",
                        configured_model=str(configured_model), resolved_model=str(model),
                        previous_max_tokens=current, retry_max_tokens=expanded,
                    )
                    _reservar_orcamento_llm(config)
                    resposta, metadata_resposta = chamar_backend(expanded, None)
                    metadata_resposta.update({
                        "configured_model": str(configured_model),
                        "resolved_model": str(metadata_resposta.get("provider_model") or model),
                        "provider": str(cfg_llm.get("provider") or ("openai_compatible" if openai_compatible else "ollama")),
                        "profile": perfil or "default",
                        "max_tokens_requested": expanded,
                        "truncation_retry": True,
                        "truncated": _finish_reason_truncado(metadata_resposta),
                    })
                    _registrar_metadata_runtime(config, metadata_resposta)
                    metadata_chamadas.append(dict(metadata_resposta))
                    if _finish_reason_truncado(metadata_resposta):
                        raise ErroLLM(
                            "A resposta do modelo continuou truncada após a repetição com orçamento maior.",
                            transient=False, error_code="MODEL_OUTPUT_TRUNCATED",
                        )
                else:
                    _registrar_metadata_runtime(config, metadata_resposta)
                    metadata_chamadas.append(dict(metadata_resposta))
                if forcar_json:
                    resposta = _limpar_resposta_estruturada(resposta)
                if not isinstance(resposta, str) or not resposta.strip():
                    raise ErroLLM(
                        "O backend respondeu sem conteúdo utilizável.",
                        transient=True, error_code="EMPTY_MODEL_RESPONSE",
                    )
                ultimo_erro = None
                break
            except urllib.error.HTTPError as erro_http:
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
            restante = _timeout_restante(config)
            if restante is not None:
                if restante <= 0:
                    raise ErroLLM(
                        "O prazo total da tarefa foi esgotado durante os retries da LLM.",
                        transient=False, error_code="TASK_DEADLINE_EXCEEDED",
                    )
                atraso = min(atraso, restante)
            job_progress.publicar(
                config, "retry", "A LLM falhou; preparando nova tentativa",
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

    _registrar_tokens_gerados(config, resposta, metadata_chamadas)
    campos_finais = {"profile": perfil or "default"}
    if stream_visible and not streaming_ativado:
        campos_finais["partial_text"] = str(resposta or "")[-16000:]
    job_progress.publicar(
        config, "validating", "Resposta gerada; executando validacoes",
        **campos_finais,
    )

    return resposta


def _chamar_llm(
    prompt_sistema, prompt_usuario, config, forcar_json=False, perfil=None,
    stream_visible=False,
):
    """Fronteira observavel de toda chamada LLM, do AgentSession."""
    inicio = time.monotonic()
    runtime = (config or {}).get("_runtime_agent_budget") or {}
    status = "ok"
    metadata = {"profile": perfil or "default", "structured": bool(forcar_json)}
    try:
        resposta = _chamar_llm_impl(
            prompt_sistema, prompt_usuario, config,
            forcar_json=forcar_json, perfil=perfil,
            stream_visible=stream_visible,
        )
        metadata["estimated_output_chars"] = len(str(resposta or ""))
        last_response = runtime.get("last_llm_response")
        if isinstance(last_response, dict):
            elapsed_ms = round((time.monotonic() - inicio) * 1000, 2)
            last_response["orchestration_latency_ms"] = elapsed_ms
            if not isinstance(last_response.get("latency_ms"), (int, float)):
                last_response["latency_ms"] = elapsed_ms
            history = runtime.get("llm_responses") or []
            if history and isinstance(history[-1], dict):
                history[-1]["orchestration_latency_ms"] = elapsed_ms
                if not isinstance(history[-1].get("latency_ms"), (int, float)):
                    history[-1]["latency_ms"] = elapsed_ms
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
            task_id=runtime.get("task_id"), job_id=runtime.get("source_job_id"),
            metadata=metadata,
        )



PROMPT_AGENTE = """You are Eyle, a coding agent. Return one JSON object only.

Valid decisions:
- {"final":"answer"}
- {"final":{"answer":"answer","claims":[{"kind":"bug|risk|recommendation|fact","sentence":1,"evidence_ids":["ev-..."]}],"limitations":[]}}
- {"tool":"name","arguments":{},"plan":[]}
- {"tool_calls":[{"tool":"name","arguments":{}}],"plan":[]}
- {"patches":[{"operation":"replace|create|delete|update","path":"file","content":"complete file"}],"plan":[]}
- {"needs_user":"blocking question"}

Follow runtime_phase/action_policy. Use only available_tools; tool_taxonomy defines shared category/effect tags. Project-specific facts, confirmed bugs and contextual risks require real evidence; hypotheses, opinions, tradeoffs and recommendations may be reasoned when clearly framed. If evidence is missing, investigate or state uncertainty. In structured finals, reference claims by the 1-based sentence number; headings do not count. Batch independent observations; do not repeat covered evidence or infer beyond a tool's purpose/caveats. Dry-run patches never write; actual writes require runtime confirmation. When patch_required, prefer one transaction; when reads_allowed is false, do not read. Never claim a change was applied before confirmation. If a patch is rejected, correct it once from the error. Answer in the user's language and tone.
"""


def executar_agente(prompt_usuario, config):
    """Run the only active Eyle reasoning profile."""
    cfg_agente = config.get("agent", {})
    forcar_json = cfg_agente.get("usar_json_mode_se_suportado", True)
    return _chamar_llm(
        PROMPT_AGENTE, prompt_usuario, config,
        forcar_json=forcar_json, perfil="agent",
    )
