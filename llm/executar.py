#!/usr/bin/env python3
"""Adaptador LLM da Eyle 2.7.4.

Transporta prompts do agente unico, chat, finalizers e recovery para o backend.
Expoe apenas os perfis internos da unica agente Eyle e o modo de chat.
"""
import json
import os
import random
import re
import socket
import sys
import threading
import time
import urllib.request
import urllib.error
from contextlib import contextmanager

# garante que 'cache' (mesma pasta) e encontrado tanto quando este arquivo
# e importado como llm.executar quanto quando rodado direto (python llm/executar.py)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
import cache as _cache

BASE_DIR = os.path.dirname(_THIS_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
from engine.persistencia import salvar_texto_atomico  # noqa: E402
from engine.config_schema import carregar_config_validada  # noqa: E402
from engine import telemetry  # noqa: E402
from engine import process_limiter  # noqa: E402
from engine import progress as job_progress  # noqa: E402
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
        "final": {
            "anyOf": [
                {"type": "string"},
                {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "verification": {"type": "string"},
                        "limitations": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "claims": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": [
                                            "fact", "risk", "inference", "hypothesis",
                                            "decision", "recommendation",
                                        ],
                                    },
                                    "text": {"type": "string"},
                                    "evidence_ids": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "basis": {"type": "string"},
                                },
                                "required": ["type", "text", "evidence_ids"],
                                "additionalProperties": False,
                            },
                        },
                        "claim_annotations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "claim": {"type": "string"},
                                    "claim_index": {"type": "integer"},
                                    "type": {
                                        "type": "string",
                                        "enum": [
                                            "fact", "inference", "hypothesis",
                                            "decision", "recommendation",
                                        ],
                                    },
                                    "evidence_ids": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "basis": {"type": "string"},
                                },
                                "required": ["type"],
                                "additionalProperties": True,
                            },
                        },
                        # Legacy keys remain accepted during migration.
                        "resposta": {"type": "string"},
                        "verificacao": {"type": "string"},
                        "limitacoes": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "additionalProperties": True,
                },
            ]
        },
        "needs_user": {"type": "string"},
        "ready_to_finalize": {"type": "boolean", "const": True},
        "important_fact": {"type": "string"},
        # Legacy key remains accepted during migration.
        "fato_importante": {"type": "string"},
        "goal_update": {"type": "object"},
    },
    "oneOf": [
        {"required": ["tool", "arguments"]},
        {"required": ["final"]},
        {"required": ["needs_user"]},
        {"required": ["ready_to_finalize"]},
    ],
    "additionalProperties": True,
}


def _diagnostico(codigo, **campos):
    """Log curto e estruturado; nunca interfere no resultado da chamada."""
    try:
        payload = {"code": codigo, **campos}
        print("[llm] " + json.dumps(payload, ensure_ascii=False, default=str), file=sys.stderr)
    except Exception:
        pass


def diagnosticar_backend(config, timeout=None):
    """Testa somente a API do backend, sem gerar tokens nem alterar cache.

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


def _fingerprint_backend(cfg_llm, forcar_json=False):
    """Identidade canonica de tudo que pode mudar a resposta do backend.

    Antes a chave carregava somente modelo/temperatura. Dois servidores ou
    providers diferentes usando o mesmo nome de modelo podiam compartilhar
    resposta indevidamente.
    """
    identidade = {
        "provider": str(cfg_llm.get("provider") or "ollama").strip().lower(),
        "base_url": str(
            cfg_llm.get("base_url") or "http://localhost:11434"
        ).rstrip("/"),
        "openai_compatible": bool(cfg_llm.get("openai_compatible", False)),
        "model": str(cfg_llm.get("model") or "qwen2.5:7b-instruct-q4_0"),
        "temperature": cfg_llm.get("temperature", 0.2),
        "max_tokens": cfg_llm.get("max_tokens", 700),
        "json_mode": bool(forcar_json),
    }
    canonico = json.dumps(
        identidade, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return canonico












def _conteudo_delta_openai(valor):
    """Compatibilidade interna; a normalizacao canonica vive no adapter."""
    return normalize_model_response({"content": valor}).content


def _metadata_resposta_normalizada(normalizada):
    return {
        "finish_reason": normalizada.finish_reason,
        "prompt_tokens": normalizada.prompt_tokens,
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
):
    """Detecta duas incompatibilidades comuns sem perfil por familia de modelo.

    1. response_format rejeitado -> repete pedindo JSON apenas no prompt.
    2. role=system rejeitado -> repete com system incorporado ao user.

    O resultado fica em memoria por servidor+modelo para as proximas chamadas.
    """
    chave = (str(base_url).rstrip("/"), str(model))
    capacidades = _CAPACIDADES_OPENAI.setdefault(
        chave, {
            "json_mode": None,
            "system_role": None,
            "reasoning_controls": None,
        },
    )
    # Compatibilidade com estado em memoria criado por uma versao anterior.
    capacidades.setdefault("reasoning_controls", None)
    usar_json_nativo = bool(forcar_json and capacidades["json_mode"] is not False)
    usar_system = capacidades["system_role"] is not False
    usar_controles_raciocinio = bool(
        forcar_json and capacidades["reasoning_controls"] is not False
    )

    try:
        resposta = _chamar_openai_compatible(
            base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
            forcar_json=usar_json_nativo, max_tokens=max_tokens,
            usar_system_role=usar_system,
            desativar_raciocinio=usar_controles_raciocinio,
            recuperar_reasoning_content=forcar_json,
            read_timeout=read_timeout, on_chunk=on_chunk,
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

    # Fallback 0: builds mais antigas podem aceitar schema JSON, mas rejeitar
    # apenas reasoning_effort/chat_template_kwargs. Retira so esses controles
    # antes de abrir mao da gramatica estruturada.
    if usar_json_nativo and usar_controles_raciocinio:
        try:
            resposta = _chamar_openai_compatible(
                base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
                forcar_json=True, max_tokens=max_tokens,
                usar_system_role=usar_system, desativar_raciocinio=False,
                recuperar_reasoning_content=forcar_json,
                read_timeout=read_timeout, on_chunk=on_chunk,
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

    # Fallback 1: response_format e opcional; o PROMPT_AGENTE ja exige JSON.
    if usar_json_nativo:
        try:
            resposta = _chamar_openai_compatible(
                base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
                forcar_json=False, max_tokens=max_tokens,
                usar_system_role=usar_system,
                recuperar_reasoning_content=forcar_json,
                read_timeout=read_timeout, on_chunk=on_chunk,
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

    # Fallback 2: alguns templates antigos nao aceitam uma mensagem system.
    if usar_system:
        try:
            resposta = _chamar_openai_compatible(
                base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
                forcar_json=False, max_tokens=max_tokens, usar_system_role=False,
                recuperar_reasoning_content=forcar_json,
                read_timeout=read_timeout, on_chunk=on_chunk,
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
    """Conta a chamada no ponto comum a TODOS os pipelines, apos cache miss."""
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
    chars_por_token = max(
        1, int((config or {}).get("context_engine", {}).get("chars_per_token_fallback", 3)),
    )
    estimativa = sum(reais) if reais else (
        len(str(resposta or "")) + chars_por_token - 1
    ) // chars_por_token
    total = int(runtime.get("generated_tokens", 0) or 0) + estimativa
    max_tokens = int(runtime.get("max_generated_tokens", 0) or 0)
    runtime["generated_tokens"] = total
    if max_tokens > 0 and total > max_tokens:
        raise ErroLLM(
            "O limite global aproximado de tokens gerados pela tarefa foi excedido.",
            transient=False, error_code="MAX_GENERATED_TOKENS_EXCEEDED",
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
    """Chama o backend com cache seguro, limites separados e retry transitorio."""
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

    cache_cfg = cfg_llm.get("cache", {})
    cache_ativado = bool(cache_cfg.get("ativado", True)) and not forcar_json
    cfg_fingerprint = dict(cfg_llm)
    cfg_fingerprint["model"] = model
    backend_fingerprint = _fingerprint_backend(
        cfg_fingerprint, forcar_json=forcar_json,
    )

    if cache_ativado:
        cacheada = _cache.obter(
            BASE_DIR, backend_fingerprint, prompt_sistema, prompt_usuario,
            max_entradas=cache_cfg.get("max_entradas", 4096),
            max_age_days=cache_cfg.get("max_age_days", 30),
            hit_flush_interval=cache_cfg.get("hit_flush_interval", 20),
            memoria_max_entradas=cache_cfg.get("memoria_max_entradas", 2048),
            max_age_hours=cache_cfg.get("max_age_hours", 24),
        )
        if cacheada is not None:
            # Compatibilidade com implementacoes externas/mocks de cache e
            # arquivos antigos ainda nao saneados pela nova camada.
            if cacheada.lstrip().lower().startswith("[erro]"):
                raise ErroLLM(cacheada[len("[erro]"):].strip())
            if not _cache.resposta_cacheavel(cacheada):
                _cache.invalidar(
                    BASE_DIR, backend_fingerprint, prompt_sistema, prompt_usuario,
                )
                _diagnostico(
                    "POISONED_CACHE_ENTRY_REMOVED",
                    backend=backend_fingerprint[:16],
                )
            else:
                campos_cache = {"profile": perfil or "default", "cached": True}
                if stream_visible:
                    campos_cache["partial_text"] = cacheada[-16000:]
                job_progress.publicar(
                    config, "validating", "Resposta recuperada do cache",
                    **campos_cache,
                )
                return cacheada

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
        slot_processo = process_limiter.acquire(
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
                    if openai_compatible:
                        resposta_backend = _chamar_openai_com_fallback(
                            base_url, model, prompt_sistema, prompt_usuario, temperature,
                            connect_atual, forcar_json=forcar_json, max_tokens=token_limit,
                            read_timeout=read_atual, on_chunk=callback,
                        )
                    else:
                        resposta_backend = _chamar_ollama(
                            base_url, model, prompt_sistema, prompt_usuario, temperature,
                            connect_atual, forcar_json=forcar_json, max_tokens=token_limit,
                            read_timeout=read_atual, on_chunk=callback,
                        )
                    metadata_backend = _ultima_metadata_backend()
                    metadata_backend["latency_ms"] = round(
                        (time.monotonic() - inicio_backend) * 1000, 2,
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
        process_limiter.release(slot_processo)
        semaforo.release()

    _registrar_tokens_gerados(config, resposta, metadata_chamadas)
    campos_finais = {"profile": perfil or "default"}
    if stream_visible and not streaming_ativado:
        campos_finais["partial_text"] = str(resposta or "")[-16000:]
    job_progress.publicar(
        config, "validating", "Resposta gerada; executando validacoes",
        **campos_finais,
    )

    # So publica no cache depois de todos os gates locais terem aceitado a
    # resposta. Assim uma resposta que estoura o orçamento da tarefa nao vira
    # um atalho permanente para sessoes futuras.
    if cache_ativado:
        _cache.definir(
            BASE_DIR, backend_fingerprint, prompt_sistema, prompt_usuario, resposta,
            max_entradas=cache_cfg.get("max_entradas", 4096),
            max_age_days=cache_cfg.get("max_age_days", 30),
            memoria_max_entradas=cache_cfg.get("memoria_max_entradas", 2048),
            max_age_hours=cache_cfg.get("max_age_hours", 24),
        )

    return resposta


def _chamar_llm(
    prompt_sistema, prompt_usuario, config, forcar_json=False, perfil=None,
    stream_visible=False,
):
    """Fronteira observavel de toda chamada LLM, inclusive pipelines legados."""
    inicio = time.monotonic()
    runtime = (config or {}).get("_runtime_agent_budget") or {}
    chamadas_antes = int(runtime.get("llm_calls", 0) or 0)
    status = "ok"
    metadata = {"profile": perfil or "default", "structured": bool(forcar_json)}
    try:
        resposta = _chamar_llm_impl(
            prompt_sistema, prompt_usuario, config,
            forcar_json=forcar_json, perfil=perfil,
            stream_visible=stream_visible,
        )
        chamadas_depois = int(runtime.get("llm_calls", 0) or 0)
        if chamadas_depois == chamadas_antes:
            status = "cache_hit"
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


PROMPT_CHAT = """Voce e a Eyle, uma unica agente autonoma local especializada exclusivamente em codigo e engenharia de software.

Sua identidade nao depende de editar arquivos: voce tambem pode conversar sobre codigo, explicar implementacoes, analisar arquitetura, investigar comportamento, planejar mudancas e revisar decisoes tecnicas.

Regras:
1. Responda de forma direta, natural e util, preservando a identidade de agente de codigo.
2. Se a pergunta mencionar um projeto e o codigo nao estiver disponivel nesta mensagem, diga isso sem inventar detalhes e oriente o usuario a pedir a leitura do workspace.
3. Nao transforme uma analise em lista de melhorias quando o usuario nao pediu recomendacoes.
4. Nao tente editar, criar ou executar algo quando o pedido for apenas conversa, explicacao ou analise.
5. Fora do dominio de codigo, explique brevemente que a Eyle foi criada para trabalhar exclusivamente com software.
6. Nao termine oferecendo ajuda generica; pergunte algo apenas quando uma decisao real impedir o proximo passo."""


PROMPT_AUDIT_SCOUT = """You are the Eyle PROJECT AUDIT SCOUT. You plan evidence collection; you never answer the user.

You receive a deterministic candidate catalog built from the real project inventory.
Rules:
1. Select only paths present in CANDIDATE CATALOG. Never invent a path.
2. Prefer a small, high-signal set covering entrypoint, orchestration/core logic, state/persistence, grounding/recovery/validation, related tests, and main configuration.
3. During gap review, select only additional files that can test a concrete risk or close a missing coverage area.
4. Do not make claims about project health. Do not write a conclusion.
5. Return JSON only in this exact envelope:
{"final":{"answer":"scout plan","selected_paths":["path"],"risk_hypotheses":["..."],"gaps":["..."],"rationale":"..."}}
Natural-language fields must use the user's language. Paths and identifiers remain unchanged."""


PROMPT_AUDIT_FINALIZER = """You are the Eyle PROJECT AUDIT FINALIZER. The planning and file selection phases are complete. You do not call tools.

Rules:
1. Use only the fresh evidence and system-calculated coverage shown in the prompt.
2. Produce atomic structured claims, not a free-form answer and not a release-note summary.
3. Each claim must contain exactly one statement with: type, text, evidence_ids, and basis when required.
4. Allowed types: fact, absence, risk, inference, hypothesis, recommendation, decision.
5. Facts, absences, risks, inferences, and hypotheses require visible fresh evidence_ids. Risks, inferences, hypotheses, and recommendations require a concise basis. Absence requires an explicit reviewed scope.
6. Follow TASK INTENT exactly. Do not add recommendations unless recommendations_requested=true. Every claim should declare which requested output it covers in `output`.
7. For response_profile=code_analysis, write for a human who wants to understand the project, in this exact semantic order:
   a) plain_language_summary: what kind of software this is and its observable purpose;
   b) main_behavior: what happens when it runs and how a user or another system interacts with it;
   c) important_components: the principal files, functions, classes, routes, commands, or interfaces;
   d) component_relationships: how those components connect;
   e) verified_limitations: only then state missing tests, absent features, or unverified behavior.
   The first claim must not be about coverage, environment variables, missing tests, or audit limitations. Do not invent a business purpose; when it is not visible, say that the code only proves the technical purpose. Enumerate observable HTTP routes, methods, handlers, and returned values when they exist. Claims should read as a coherent explanation, not an audit checklist.
8. Never claim that tests pass unless an executed run_tests result is shown. Never claim there are no critical problems or that all functionality is operational without the required system proof.
9. Report limitations honestly and concisely. Do not put unsupported facts in limitations.
10. Return JSON only, exactly:
{"final":{"claims":[{"type":"fact","text":"...","evidence_ids":["ev-0001"],"basis":"","scope":"","output":"analysis"}],"verification":"...","limitations":[]}}
The system, not you, renders the final text from validated claims. Write claim text in the user's language."""


PROMPT_PROJECT_READ_FINALIZER = """You are the Eyle PROJECT READ FINALIZER. Evidence collection is complete. You do not call tools.

Rules:
1. Answer the exact user request directly using only the fresh evidence shown.
2. Produce atomic structured claims, not a free-form answer. Each claim must answer one concrete part of the request.
3. Explicitly cover every named file, symbol, behavior, relationship, or existence question. Do not replace a requested target with a nearby symbol.
4. Allowed claim types: fact, absence, risk, inference, hypothesis, recommendation, decision.
5. Facts, absences, risks, inferences, and hypotheses require visible fresh evidence_ids. Risks, inferences, hypotheses, and recommendations require a concise basis. Absence requires an explicit reviewed scope.
6. Follow TASK INTENT exactly. Do not add recommendations unless recommendations_requested=true. Every claim should declare which requested output it covers in `output`.
7. For response_profile=code_analysis, begin with a plain-language explanation of what the project is and what it does. Then describe its observable behavior, important components and their relationships. Put verified limitations last. Do not lead with missing tests, configuration details, coverage, or audit process. Enumerate routes/interfaces and returned values when visible. Do not invent a business purpose.
8. If evidence proves absence, use type=absence and state the reviewed scope. search_code relevance alone never proves absence.
9. Do not write headings with no content, incomplete sentences, or a trailing colon awaiting missing text.
10. Return JSON only, exactly:
{"final":{"claims":[{"type":"fact","text":"...","evidence_ids":["ev-0001"],"basis":"","scope":"","output":"explanation"}],"verification":"...","limitations":[]}}
The system, not you, renders the final text from validated claims. Write claim text in the user's language. Paths and identifiers remain unchanged."""


PROMPT_AGENTE = """You are the Eyle AGENT. Perform exactly one action per decision and output JSON only.

Language contract:
- The original user request may be written in Portuguese. Preserve its meaning exactly.
- Internal instructions, state, tool protocol, and JSON keys are in English.
- Natural-language text shown to the user (`final.answer`, string `final`, or `needs_user`) must use the user's language. When the user writes in Portuguese, answer in Brazilian Portuguese.
- Never translate code, file paths, symbol names, identifiers, or literal values.

Allowed JSON formats:
- tool action: {"tool":"tool_name","arguments":{...}}
- project final: {"final":{"answer":"...","evidence_ids":["ev-0001"],"verification":"...","limitations":[],"claim_annotations":[]}}
- chat final: {"final":"..."}
- real blocker/question: {"needs_user":"..."}
- optional memory note: add "important_fact":"..." to any allowed object.

Mandatory rules:
1. Follow GOAL STATE (`mode`, `success_criteria`, `constraints`, `plan`, `current_step`, `blockers`, and `evidence_needed`) and the TOOL CATALOG. One decision may call at most one tool.
2. Use the cheapest valid order: tree/metadata orient, search locates, and `read_range` reads fresh code. Do not open random candidates and never repeat the same `tool` + `arguments` call.
3. `analyze` and `suggest` may use READ tools only. In `edit`, follow this exact state machine:
   fresh read -> `test_patch_dry_run` -> `apply_patch` with the SAME proposal -> `run_tests` -> fresh post-write read.
   The system derives hashes and original code from evidence and handles WRITE confirmation. Never invent or manually copy hashes.
4. State/gate messages are authoritative:
   - `READ_REQUIRED` or `POST_WRITE_READ_REQUIRED`: perform the requested fresh read.
   - `WRITE_PENDING`: do not claim that a change was applied; wait for system confirmation.
   - `RUN_TESTS` or `RUN_TESTS_REQUIRED`: call `run_tests` and do not finalize first.
   - `STALE_PATCH`: do not retry the same patch blindly; follow the system recovery/re-read instruction.
   - If the prompt contains `MANDATORY NEXT EDIT ACTION`, execute exactly that step.
5. Project tasks require fresh code evidence. Tree, metadata, observations, and `important_fact` do not count. Reread stale evidence. For sufficient `project_read` evidence, return `{"ready_to_finalize":true}`; do not draft the answer.
6. Use only visible `evidence_ids`. Every `file:line` citation must be covered by those evidence ranges. Report real limitations.
   The project is observed state, not universal truth. You may reason beyond what is literally written, but keep epistemic types honest:
   - unannotated assertive statements are treated as observed `fact` and must be grounded;
   - annotate exact non-factual sentences in `claim_annotations` as `inference`, `hypothesis`, `decision`, or `recommendation`;
   - an `inference` should identify supporting `evidence_ids` or a short `basis`;
   - a `hypothesis` must use uncertain wording and should be tested when a READ tool can test it;
   - `decision` and `recommendation` may introduce new values, files, designs, or approaches that do not yet exist in the project;
   - never relabel an observed factual assertion merely to bypass grounding.
7. After a WRITE with `changed=true`, `run_tests` must execute and pass, then the final changed range must be read again. If `executed=false`, say that no test suite ran; never claim tests passed.
8. `important_fact` is optional, brief, and never replaces evidence.
9. Replan only when fresh evidence disproves the hypothesis. Add this to the tool action:
   "goal_update":{"trigger":"hypothesis_denied","detail":"...","plan":["..."],"evidence_needed":["..."]}.
   Tool failures and changed files are replanned by the system.
10. `max_steps` counts executed tools. Do not waste decisions on invalid formatting, rejected finals, or repeated calls.
11. Use `needs_user` only for a real blocker after trying every applicable tool. Never claim project context is missing before using READ tools. General project analysis starts with `list_tree`, then `search_code`/`read_range` on relevant code."""


def executar_agente(prompt_usuario, config):
    """Personalidade do Agente minimo (Atualizacao 1): decide a proxima ferramenta
    a chamar (ou encerra com resposta final). Usa json mode no backend quando
    config["agent"]["usar_json_mode_se_suportado"] estiver ligado -- reduz a
    chance de formato invalido, mas quem garante que o loop nao trava mesmo
    assim e' o retry em engine/agent.py."""
    cfg_agente = config.get("agent", {})
    forcar_json = cfg_agente.get("usar_json_mode_se_suportado", True)
    return _chamar_llm(
        PROMPT_AGENTE, prompt_usuario, config,
        forcar_json=forcar_json, perfil="agent",
    )



def executar_project_read_finalizer(prompt_usuario, config):
    """Redige project_read somente depois da coleta de evidencias."""
    return _chamar_llm(
        PROMPT_PROJECT_READ_FINALIZER, prompt_usuario, config,
        forcar_json=True, perfil="project_read_finalizer", stream_visible=True,
    )


def executar_audit_scout(prompt_usuario, config):
    """Planeja componentes de project_audit sem produzir conclusao."""
    return _chamar_llm(
        PROMPT_AUDIT_SCOUT, prompt_usuario, config,
        forcar_json=True, perfil="audit_scout",
    )


def executar_audit_finalizer(prompt_usuario, config):
    """Gera somente a conclusao final a partir de evidencias ja coletadas."""
    return _chamar_llm(
        PROMPT_AUDIT_FINALIZER, prompt_usuario, config,
        forcar_json=True, perfil="audit_finalizer", stream_visible=True,
    )

def executar_chat(pergunta, config, historico=None):
    """Modo conversa livre: sem workspace ou ferramentas -- somente a LLM
    respondendo direto, com no maximo um resumo curto do historico recente."""
    prompt_usuario = pergunta
    if historico:
        linhas = [f"{m['role']}: {m['text']}" for m in historico]
        prompt_usuario = "HISTORICO RECENTE DA CONVERSA:\n" + "\n".join(linhas) + f"\n\nMENSAGEM ATUAL:\n{pergunta}"
    return _chamar_llm(
        PROMPT_CHAT, prompt_usuario, config, perfil="chat", stream_visible=True,
    )












PROMPT_RECOVERY = """You are the response recovery stage of Eyle.

Return plain natural-language text only, never JSON. Use only the supplied fresh evidence.
Produce a real conclusion related to the user's request: describe at least one observed behavior, inference, risk, or recommendation.
Do not output a file list, evidence receipt, execution log, or status summary as the answer.
Do not invent files, symbols, behavior, tests, or results. Keep citations exactly within the supplied file ranges.
Answer in the user's language."""


def executar_recuperacao_textual(prompt_usuario, config):
    """Retry textual sem response_format para recuperar uma conclusao util."""
    return _chamar_llm(
        PROMPT_RECOVERY, prompt_usuario, config,
        forcar_json=False, perfil="recovery", stream_visible=False,
    )





