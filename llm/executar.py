#!/usr/bin/env python3
"""
executar.py
-----------
Nao pensa pelo sistema. So faz:

    prompt recebido (ja montado pelo engine/compiler.py)
            +
        modelo local
            |
            v
        resposta

Duas personalidades, um modelo so (mesmo GGUF, prompt de sistema
diferente):

    executar_analista(...)  -> decide o que ler, nunca gera codigo/resposta
    executar_executor(...)  -> resolve com o contexto ja compilado

Usa apenas a biblioteca padrao do Python (urllib), entao nao precisa
instalar 'requests' nem nada -- funciona direto contra:
  - Ollama          (http://localhost:11434)
  - LM Studio       (http://localhost:1234, openai_compatible=true)
  - llama.cpp server (openai_compatible=true)
  - text-generation-webui (openai_compatible=true)
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
        "important_fact": {"type": "string"},
        # Legacy key remains accepted during migration.
        "fato_importante": {"type": "string"},
        "goal_update": {"type": "object"},
    },
    "oneOf": [
        {"required": ["tool", "arguments"]},
        {"required": ["final"]},
        {"required": ["needs_user"]},
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


PROMPT_ANALISTA = """Voce e o ANALISTA da Eyle. Sua unica funcao e pensar sobre o que importa.

Regras obrigatorias:
1. Voce NUNCA gera codigo.
2. Voce NUNCA responde ao usuario.
3. Voce so decide: o que ler, o que ignorar, quais riscos existem e o que esta faltando.
4. Use APENAS os candidatos, relacoes e evidencias fornecidos -- nao invente arquivos, funcoes ou simbolos que nao apareceram.
5. Responda SOMENTE com o JSON pedido, sem nenhum texto antes ou depois."""


PROMPT_ENTENDEDOR = """Voce e o ENTENDEDOR da Eyle. Sua unica funcao e ler um arquivo de codigo INTEIRO, uma unica vez, e devolver um retrato estrutural objetivo dele -- isto alimenta o Modelo Interno do Projeto (memory/entendimento.json), usado depois para dar dicas e sugerir mudancas sem precisar reler tudo de novo.

Regras obrigatorias:
1. Use APENAS o conteudo do arquivo fornecido abaixo -- nao invente nada que nao esteja no codigo.
2. \"depende_de\" deve refletir os imports/chamadas REAIS do arquivo, nao suposicoes.
3. \"pontos_criticos\" cobre tanto o que e critico operacionalmente (ex: falha aqui trava o pipeline principal) quanto o que e questionavel arquiteturalmente (ex: alto acoplamento, sem tratamento de erro) -- pode ser lista vazia se nao houver nada relevante.
4. Responda SOMENTE com o JSON pedido, sem nenhum texto antes ou depois."""


PROMPT_EXECUTOR = """Voce e o EXECUTOR da Eyle. Voce trabalha SOMENTE com o contexto fornecido abaixo -- ele ja foi selecionado pelo Analista, entao confie nele.

Regras obrigatorias:
1. Use apenas as informacoes presentes no contexto (TRECHOS, EVIDENCIAS, RESUMO DO PROJETO).
2. Se a informacao necessaria nao estiver no contexto, diga claramente "nao tenho essa informacao no contexto atual" -- nao invente arquivos, funcoes ou linhas que nao apareceram.
3. Ao citar algo, sempre mencione o arquivo e as linhas exatamente como aparecem no contexto (ex: config.py:43-61).
4. Seja direto e objetivo. Nao repita o contexto inteiro na resposta.
5. Voce nao precisa descobrir onde mexer -- isso ja foi decidido. Apenas resolva o objetivo."""


PROMPT_SUGESTOR = """Voce e o SUGESTOR da Eyle. Sua unica funcao e ler o codigo real de componentes ja escolhidos (pelo Modelo Interno do Projeto) e sugerir melhorias fundamentadas -- voce NUNCA aplica nada, so aponta.

Regras obrigatorias:
1. Use APENAS o codigo real mostrado no COMPONENTES CANDIDATOS -- nao invente linha, funcao ou arquivo que nao apareceu ali.
2. Cada sugestao precisa citar o arquivo (e a linha, se identificavel no codigo mostrado).
3. Se uma sugestao depender de algo que nao esta nos candidatos (ex: um arquivo so mencionado em depende_de mas sem codigo mostrado), diga isso explicitamente em vez de supor o conteudo.
4. Voce NUNCA gera um patch nem diz que a mudanca ja foi aplicada -- isso e a Atualizacao 5 (\"codar de verdade\"), fora do seu escopo. Voce so sugere.
5. Seja objetivo: priorize os pontos_criticos ja identificados no Modelo Interno antes de procurar problemas novos por conta propria."""


PROMPT_ENGENHEIRO = """Voce e o ENGENHEIRO da Eyle. Sua unica funcao e escrever o CODIGO NOVO completo de UM simbolo (funcao/classe) especifico, ja localizado por linha no arquivo real -- isso alimenta um patch de verdade (Atualizacao 5: Proposta -> Impacto -> Patch -> Teste -> Aplicar), nunca aplicado sem confirmacao explicita do usuario depois.

Regras obrigatorias:
1. Use APENAS o CODIGO REAL ATUAL mostrado abaixo e o OBJETIVO pedido -- nao invente funcoes, campos, imports ou comportamento que nao existem no restante do arquivo.
2. "codigo_novo" e o RECORTE COMPLETO E FINAL que substitui o codigo atual -- nunca um diff, nunca "..." indicando partes omitidas. Preserve indentacao e assinatura, salvo se a mudanca pedida for justamente nisso.
3. Considere QUEM DEPENDE deste arquivo (se mostrado) antes de mudar uma assinatura ou comportamento que outros arquivos esperam.
4. Liste em "riscos" qualquer coisa que voce percebe que pode quebrar com esta mudanca especifica -- pode ser lista vazia se nao houver nada.
5. Responda SOMENTE com o JSON pedido, sem nenhum texto antes ou depois."""


def _conteudo_delta_openai(valor):
    if isinstance(valor, str):
        return valor
    if isinstance(valor, list):
        partes = []
        for item in valor:
            if isinstance(item, str):
                partes.append(item)
            elif isinstance(item, dict):
                texto = item.get("text") or item.get("content")
                if isinstance(texto, str):
                    partes.append(texto)
        return "".join(partes)
    return ""


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
            corpo = json.loads(resp.read().decode("utf-8"))
            return corpo.get("message", {}).get("content", "")

        partes = []
        ultimo = {}
        for linha_bruta in resp:
            linha = linha_bruta.decode("utf-8", errors="replace").strip()
            if not linha:
                continue
            corpo = json.loads(linha)
            ultimo = corpo if isinstance(corpo, dict) else {}
            delta = _conteudo_delta_openai(
                (corpo.get("message") or {}).get("content")
                if isinstance(corpo, dict) else ""
            )
            if delta:
                partes.append(delta)
            on_chunk(delta, ultimo, bool(ultimo.get("done")))
        on_chunk("", ultimo, True)
        return "".join(partes)


def _chamar_openai_compatible(
    base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
    forcar_json=False, max_tokens=None, usar_system_role=True,
    desativar_raciocinio=False, read_timeout=None, on_chunk=None,
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
                if not linha.startswith("{"):
                    continue
                corpo = json.loads(linha)
                ultimo = corpo if isinstance(corpo, dict) else {}
                escolhas = ultimo.get("choices") or []
                delta = ""
                if escolhas and isinstance(escolhas[0], dict):
                    bloco = escolhas[0].get("delta") or escolhas[0].get("message") or {}
                    # reasoning_content e deliberadamente ignorado: progresso
                    # publico nao e chain-of-thought.
                    delta = _conteudo_delta_openai(bloco.get("content"))
                if delta:
                    partes.append(delta)
                on_chunk(delta, ultimo, False)
            on_chunk("", ultimo, True)
            return "".join(partes)

        corpo = json.loads(resp.read().decode("utf-8"))
    mensagem = corpo["choices"][0]["message"]
    conteudo = mensagem.get("content")
    if isinstance(conteudo, str) and conteudo.strip():
        return conteudo
    if isinstance(conteudo, list):
        combinado = _conteudo_delta_openai(conteudo).strip()
        if combinado:
            return combinado
    if forcar_json:
        raciocinio = mensagem.get("reasoning_content")
        if isinstance(raciocinio, str) and raciocinio.strip():
            return raciocinio
    return ""


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


def _registrar_tokens_gerados(config, resposta):
    runtime = (config or {}).get("_runtime_agent_budget")
    if not isinstance(runtime, dict):
        return
    chars_por_token = max(
        1, int((config or {}).get("context_engine", {}).get("chars_per_token_fallback", 3)),
    )
    estimativa = (len(str(resposta or "")) + chars_por_token - 1) // chars_por_token
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
    model = cfg_llm.get("model", "qwen2.5:7b-instruct-q4_0")
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

    streaming_ativado = bool(
        job_progress.job_id_de(config) is not None
        and cfg_llm.get("stream_responses", True)
    )
    chars_por_token = max(
        1, int((config or {}).get("context_engine", {}).get("chars_per_token_fallback", 3)),
    )

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
                if openai_compatible:
                    resposta = _chamar_openai_com_fallback(
                        base_url, model, prompt_sistema, prompt_usuario, temperature,
                        connect_atual, forcar_json=forcar_json, max_tokens=max_tokens,
                        read_timeout=read_atual, on_chunk=on_chunk,
                    )
                else:
                    resposta = _chamar_ollama(
                        base_url, model, prompt_sistema, prompt_usuario, temperature,
                        connect_atual, forcar_json=forcar_json, max_tokens=max_tokens,
                        read_timeout=read_atual, on_chunk=on_chunk,
                    )
                if forcar_json:
                    resposta = _limpar_resposta_estruturada(resposta)
                if not isinstance(resposta, str) or not resposta.strip():
                    raise ErroLLM(
                        "O backend respondeu sem conteúdo utilizável.",
                        transient=True, error_code="EMPTY_RESPONSE",
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

    _registrar_tokens_gerados(config, resposta)
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


PROMPT_CHAT = """Voce e a Eyle, uma assistente de IA local que roda no computador do usuario.

Voce pode conversar sobre QUALQUER assunto -- duvidas gerais, ideias, dicas, papo comum --
nao apenas sobre o projeto de codigo que voce tem acesso.

Regras:
1. Responda de forma direta, natural e util, como numa conversa normal.
2. Se a pergunta mencionar o projeto/aplicacao e voce nao tiver o contexto do codigo
   nesta mensagem, diga isso com naturalidade e sugira reformular pedindo pra
   consultar o projeto -- nao invente detalhes do codigo.
3. Nao force respostas sobre programacao se o assunto for outro.
4. Se o pedido for um roteiro, cena, historia ou dialogo, ENTREGUE o texto formatado
   de verdade (cabecalho de cena, rubricas de acao em linha separada, nome do
   personagem antes da fala) -- nao descreva o que o roteiro "poderia" ter nem
   pergunte o tom antes de tentar; escreva uma primeira versao completa seguindo
   o que foi pedido e so pergunte depois se quer ajustar algo.
5. Nao termine a resposta oferecendo genericamente "quer que eu adapte/ajude com
   mais alguma coisa" -- só pergunte algo se for uma decisao real que muda o
   proximo passo."""


PROMPT_AGENTE = """You are the Eyle AGENT. Perform exactly one action per decision and output JSON only.

Language contract:
- The original user request may be written in Portuguese. Preserve its meaning exactly.
- Internal instructions, state, tool protocol, and JSON keys are in English.
- Natural-language text shown to the user (`final.answer`, string `final`, or `needs_user`) must use the user's language. When the user writes in Portuguese, answer in Brazilian Portuguese.
- Never translate code, file paths, symbol names, identifiers, or literal values.

Allowed JSON formats:
- tool action: {"tool":"tool_name","arguments":{...}}
- project final: {"final":{"answer":"...","evidence_ids":["ev-0001"],"verification":"...","limitations":[]}}
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
5. `project_read` and `project_write` may finish only with fresh code evidence. Tree, metadata, observations, and `important_fact` are not evidence. Stale evidence or an old hash requires a new read.
6. Use only visible `evidence_ids`. Every `file:line` citation must be covered by those evidence ranges. Report real limitations.
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


def executar_chat(pergunta, config, historico=None):
    """Modo conversa livre: sem retrieval, sem Analista, sem Verify -- so a LLM
    respondendo direto, com no maximo um resumo curto do historico recente."""
    prompt_usuario = pergunta
    if historico:
        linhas = [f"{m['role']}: {m['text']}" for m in historico]
        prompt_usuario = "HISTORICO RECENTE DA CONVERSA:\n" + "\n".join(linhas) + f"\n\nMENSAGEM ATUAL:\n{pergunta}"
    return _chamar_llm(
        PROMPT_CHAT, prompt_usuario, config, perfil="chat", stream_visible=True,
    )


def executar_analista(prompt_usuario, config):
    """Primeira chamada da LLM: decide o que importa. Nunca gera codigo, nunca responde ao usuario."""
    return _chamar_llm(PROMPT_ANALISTA, prompt_usuario, config, perfil="analyst")


def executar_executor(prompt_usuario, config):
    """Segunda chamada da LLM: resolve o objetivo com o contexto ja compilado."""
    return _chamar_llm(
        PROMPT_EXECUTOR, prompt_usuario, config,
        perfil="executor", stream_visible=True,
    )


def executar_sugestor(prompt_usuario, config):
    """Quarta personalidade (Atualizacao 4): le o codigo real dos componentes
    ja escolhidos pelo Modelo Interno e devolve sugestoes fundamentadas.
    Nunca aplica nada -- so sugere."""
    return _chamar_llm(
        PROMPT_SUGESTOR, prompt_usuario, config,
        perfil="suggester", stream_visible=True,
    )


def executar_engenheiro(prompt_usuario, config):
    """Quinta personalidade (Atualizacao 5): escreve o codigo novo completo
    de UM simbolo ja localizado por linha no arquivo real. Devolve o JSON
    de proposta -- quem aplica de fato (so apos confirmacao) e
    engine/codar.py:aplicar_patch, chamado por engine/engine.py."""
    return _chamar_llm(PROMPT_ENGENHEIRO, prompt_usuario, config, perfil="engineer")


def executar_entendedor(prompt_usuario, config):
    """Terceira personalidade: le um arquivo inteiro (uma vez, na ingestao) e
    devolve o retrato estrutural dele para o Modelo Interno do Projeto
    (memory/entendimento.json). Nunca gera codigo, nunca responde ao usuario --
    so descreve o arquivo que acabou de ler."""
    return _chamar_llm(PROMPT_ENTENDEDOR, prompt_usuario, config, perfil="indexer")


def main():
    """Uso manual: testa o Executor direto contra o ultimo context/atual.json gerado pelo retrieval."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, base_dir)
    from engine.compiler import montar_prompt_executor

    config = carregar_config_validada(os.path.join(base_dir, "config.json"))
    with open(os.path.join(base_dir, "context", "atual.json"), "r", encoding="utf-8") as f:
        atual = json.load(f)

    projeto = None
    projeto_path = os.path.join(base_dir, "memory", "projeto.json")
    if os.path.exists(projeto_path):
        with open(projeto_path, "r", encoding="utf-8") as f:
            projeto = json.load(f)

    prompt_usuario = montar_prompt_executor(atual, projeto)
    resposta = executar_executor(prompt_usuario, config)
    print(resposta)

    salvar_texto_atomico(os.path.join(base_dir, "context", "ultima_resposta.txt"), resposta)


if __name__ == "__main__":
    main()
