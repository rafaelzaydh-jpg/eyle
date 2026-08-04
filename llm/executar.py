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
import re
import sys
import urllib.request
import urllib.error

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


class ErroLLM(RuntimeError):
    """Falha de transporte/backend; nunca representa uma resposta do modelo."""


# Deteccao basica, somente em memoria, do servidor OpenAI-compativel.
# Evita gravar estado novo no projeto e reaprende a cada reinicio da Eyle.
_CAPACIDADES_OPENAI = {}
_MODELOS_OPENAI = {}
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
    "anyOf": [
        {"required": ["tool"]},
        {"required": ["final"]},
        {"required": ["needs_user"]},
    ],
    "additionalProperties": True,
}


def _endpoint_openai(base_url, recurso):
    """Aceita base_url com ou sem o sufixo /v1."""
    base = str(base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return base + "/" + recurso.lstrip("/")
    return base + "/v1/" + recurso.lstrip("/")


def _detectar_modelos_openai(base_url, timeout):
    """Consulta /v1/models quando disponivel; falha silenciosamente.

    llama-server normalmente expoe um unico modelo/alias. A deteccao evita
    que um nome antigo em config.json derrube a comunicacao depois de trocar
    o GGUF carregado. Servidores sem esse endpoint continuam usando o nome
    configurado, sem mudar o comportamento anterior.
    """
    chave = str(base_url or "").rstrip("/")
    if chave in _MODELOS_OPENAI:
        return list(_MODELOS_OPENAI[chave])

    req = urllib.request.Request(_endpoint_openai(base_url, "models"))
    try:
        with urllib.request.urlopen(req, timeout=min(max(float(timeout), 1.0), 5.0)) as resp:
            corpo = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    modelos = []
    for item in corpo.get("data", []) if isinstance(corpo, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            modelos.append(item["id"].strip())
    if modelos:
        _MODELOS_OPENAI[chave] = tuple(modelos)
    return modelos


def _resolver_modelo_openai(base_url, model, timeout):
    """Prefere o modelo configurado; corrige automaticamente o caso comum
    de llama-server com um unico modelo carregado e config desatualizada."""
    configurado = str(model or "").strip()
    modelos = _detectar_modelos_openai(base_url, timeout)
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


def _chamar_ollama(base_url, model, prompt_sistema, prompt_usuario, temperature, timeout, forcar_json=False, max_tokens=None):
    url = base_url.rstrip("/") + "/api/chat"
    options = {"temperature": temperature}
    if max_tokens:
        # Ollama usa "num_predict" (nao "max_tokens") dentro de "options" --
        # ver Atualizacao 15 no comentario de _chamar_llm pro motivo.
        options["num_predict"] = max_tokens
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario},
        ],
        "stream": False,
        "options": options,
    }
    if forcar_json:
        # Ollama nativo aceita "format": "json" no corpo do /api/chat
        payload["format"] = "json"
    dados = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        corpo = json.loads(resp.read().decode("utf-8"))
    return corpo.get("message", {}).get("content", "")


def _chamar_openai_compatible(
    base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
    forcar_json=False, max_tokens=None, usar_system_role=True,
    desativar_raciocinio=False,
):
    url = _endpoint_openai(base_url, "chat/completions")
    if usar_system_role:
        messages = [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario},
        ]
    else:
        # Fallback para templates/backends antigos que rejeitam role=system.
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
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if forcar_json:
        # Schema explicito: em llama-server moderno isso gera uma gramatica,
        # em vez de apenas pedir genericamente "algum objeto JSON".
        payload["response_format"] = {
            "type": "json_object",
            "schema": _SCHEMA_DECISAO_AGENTE,
        }
        # Modelos thinking (Qwen e semelhantes) podem gastar todo max_tokens
        # em reasoning e devolver content vazio. llama-server atual aceita os
        # dois controles; builds antigas caem no fallback sem esses campos.
        if desativar_raciocinio:
            payload["reasoning_effort"] = "none"
            payload["chat_template_kwargs"] = {"enable_thinking": False}
    dados = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        corpo = json.loads(resp.read().decode("utf-8"))
    mensagem = corpo["choices"][0]["message"]
    conteudo = mensagem.get("content")
    if isinstance(conteudo, str) and conteudo.strip():
        return conteudo
    if isinstance(conteudo, list):
        partes = []
        for item in conteudo:
            if isinstance(item, dict):
                texto = item.get("text") or item.get("content")
                if isinstance(texto, str):
                    partes.append(texto)
            elif isinstance(item, str):
                partes.append(item)
        combinado = "".join(partes).strip()
        if combinado:
            return combinado

    # Algumas combinacoes modelo/template colocam a geracao em
    # reasoning_content e deixam content vazio. Para chamada estruturada,
    # devolver esse campo e melhor do que transformar uma resposta existente
    # em string vazia; o parser do Agente ainda exige um JSON reconhecivel.
    if forcar_json:
        raciocinio = mensagem.get("reasoning_content")
        if isinstance(raciocinio, str) and raciocinio.strip():
            return raciocinio
    return ""


def _chamar_openai_com_fallback(
    base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
    forcar_json=False, max_tokens=None,
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
            raise ErroLLM(_mensagem_http_error(base_url, primeiro_erro, primeiro_corpo)) from primeiro_erro

    # Fallback 0: builds mais antigas podem aceitar schema JSON, mas rejeitar
    # apenas reasoning_effort/chat_template_kwargs. Retira so esses controles
    # antes de abrir mao da gramatica estruturada.
    if usar_json_nativo and usar_controles_raciocinio:
        try:
            resposta = _chamar_openai_compatible(
                base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
                forcar_json=True, max_tokens=max_tokens,
                usar_system_role=usar_system, desativar_raciocinio=False,
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
                raise ErroLLM(
                    _mensagem_http_error(
                        base_url, erro_sem_controles, corpo_sem_controles,
                    )
                ) from erro_sem_controles
            capacidades["reasoning_controls"] = False

    # Fallback 1: response_format e opcional; o PROMPT_AGENTE ja exige JSON.
    if usar_json_nativo:
        try:
            resposta = _chamar_openai_compatible(
                base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
                forcar_json=False, max_tokens=max_tokens,
                usar_system_role=usar_system,
            )
            capacidades["json_mode"] = False
            if usar_system:
                capacidades["system_role"] = True
            return resposta
        except urllib.error.HTTPError as segundo_erro:
            erro_apos_json = segundo_erro
            segundo_corpo = _ler_corpo_http_error(segundo_erro)
            if not _erro_pode_ser_incompatibilidade(segundo_erro, segundo_corpo):
                raise ErroLLM(
                    _mensagem_http_error(base_url, segundo_erro, segundo_corpo)
                ) from segundo_erro
    else:
        erro_apos_json = erro_inicial
        segundo_corpo = primeiro_corpo

    # Fallback 2: alguns templates antigos nao aceitam uma mensagem system.
    if usar_system:
        try:
            resposta = _chamar_openai_compatible(
                base_url, model, prompt_sistema, prompt_usuario, temperature, timeout,
                forcar_json=False, max_tokens=max_tokens, usar_system_role=False,
            )
            capacidades["system_role"] = False
            if usar_json_nativo:
                capacidades["json_mode"] = False
            return resposta
        except urllib.error.HTTPError as terceiro_erro:
            terceiro_corpo = _ler_corpo_http_error(terceiro_erro)
            raise ErroLLM(
                _mensagem_http_error(base_url, terceiro_erro, terceiro_corpo)
            ) from terceiro_erro

    raise ErroLLM(
        _mensagem_http_error(base_url, erro_apos_json, segundo_corpo)
    ) from erro_apos_json


def _chamar_llm(prompt_sistema, prompt_usuario, config, forcar_json=False):
    """Funcao interna comum a todas as personalidades -- so muda o prompt de sistema.

    Antes de chamar o servidor local, confere o cache por hash do prompt
    completo (Atualizacao 2): pergunta identica, no mesmo contexto exato,
    nao gasta uma chamada de LLM de novo.

    forcar_json (Agente minimo, Atualizacao 1): quando True, tenta pedir ao
    backend que a resposta venha em JSON puro. So faz sentido pra chamada do
    Agente -- as demais personalidades continuam chamando sem esse parametro.

    Atualizacao 15 -- teto de tokens de saida (max_tokens/num_predict):
    nenhuma chamada aqui limitava quantos tokens o modelo podia gerar por
    resposta. Caso real que motivou a correcao: um "oi" simples gerou uma
    resposta de 600+ tokens (ainda incompleta) num modelo local rodando a
    ~7 tokens/s, ate a chamada ser cancelada -- sem teto, uma resposta
    trivial pode consumir o orcamento inteiro de timeout_seconds so' com
    verbosidade. cfg_llm["max_tokens"] (default 700) e' passado como
    "num_predict" pro Ollama e "max_tokens" pro backend OpenAI-compatible
    -- 0/None desliga o teto (comportamento antigo), pra quem preferir
    sem limite.

    Atualizacao 20 -- falhas de rede/backend levantam ``ErroLLM`` em vez
    de devolver uma string ``[erro]`` confundivel com resposta valida. Os
    pipelines capturam essa excecao e encerram com status ``failed`` sem
    Verify, cache ou mensagem de assistente no historico."""
    cfg_llm = config.get("llm", {})
    base_url = cfg_llm.get("base_url", "http://localhost:11434")
    model = cfg_llm.get("model", "qwen2.5:7b-instruct-q4_0")
    temperature = cfg_llm.get("temperature", 0.2)
    timeout = cfg_llm.get("timeout_seconds", 180)
    openai_compatible = cfg_llm.get("openai_compatible", False)
    max_tokens = cfg_llm.get("max_tokens", 700)

    # Em llama-server/OpenAI-compatible, consulta /v1/models quando existe.
    # Se houver um unico modelo carregado, ele prevalece sobre um nome antigo
    # deixado no config.json. Em servidores com varios modelos, preserva o nome
    # configurado para nao escolher um modelo arbitrariamente.
    if openai_compatible:
        model = _resolver_modelo_openai(base_url, model, timeout)

    cache_cfg = cfg_llm.get("cache", {})
    # Decisoes do Agente nao entram no cache. Uma resposta estrutural invalida
    # cacheada envenenava todas as repeticoes da mesma tarefa: o retry voltava
    # a receber exatamente o mesmo texto ruim sem consultar o modelo de novo.
    cache_ativado = bool(cache_cfg.get("ativado", True)) and not forcar_json
    cfg_fingerprint = dict(cfg_llm)
    cfg_fingerprint["model"] = model
    backend_fingerprint = _fingerprint_backend(
        cfg_fingerprint, forcar_json=forcar_json,
    )

    if cache_ativado:
        cacheada = _cache.obter(
            BASE_DIR, backend_fingerprint, prompt_sistema, prompt_usuario,
            max_entradas=cache_cfg.get("max_entradas", 500),
            max_age_days=cache_cfg.get("max_age_days", 30),
        )
        if cacheada is not None:
            # Compatibilidade defensiva com caches produzidos por versoes
            # antigas ou preenchidos manualmente. Erros nunca deveriam ter
            # sido cacheados, mas tambem nao podem voltar como resposta real.
            if cacheada.startswith("[erro]"):
                raise ErroLLM(cacheada[len("[erro]"):].strip())
            return cacheada

    try:
        if openai_compatible:
            resposta = _chamar_openai_com_fallback(
                base_url, model, prompt_sistema, prompt_usuario, temperature,
                timeout, forcar_json=forcar_json, max_tokens=max_tokens,
            )
        else:
            resposta = _chamar_ollama(base_url, model, prompt_sistema, prompt_usuario, temperature, timeout, forcar_json=forcar_json, max_tokens=max_tokens)
    except urllib.error.HTTPError as e:
        # Bug: HTTPError e' subclasse de URLError -- se este except viesse
        # DEPOIS do "except URLError" (como estava antes), um erro HTTP
        # (400/404/500...) seria pego la e reportado como "nao foi possivel
        # conectar", que e' enganoso: o servidor respondeu, so recusou o
        # pedido. Le o corpo da resposta (quando o backend manda um JSON de
        # erro, o que e' comum) porque e' exatamente o que explica O QUE
        # esta errado no pedido -- payload no formato errado para esse
        # backend, campo nao suportado (ex: "format"/"response_format"),
        # nome de modelo desconhecido, etc.
        corpo_erro = _ler_corpo_http_error(e)
        raise ErroLLM(_mensagem_http_error(base_url, e, corpo_erro)) from e
    except urllib.error.URLError as e:
        raise ErroLLM(
            f"Nao foi possivel conectar em {base_url}. "
            f"Verifique se o servidor local (Ollama/LM Studio/llama.cpp) esta rodando. Detalhe: {e}"
        ) from e
    except ErroLLM:
        raise
    except Exception as e:
        raise ErroLLM(f"Falha ao chamar a LLM local: {e}") from e

    if forcar_json:
        resposta = _limpar_resposta_estruturada(resposta)

    if cache_ativado:
        _cache.definir(
            BASE_DIR, backend_fingerprint, prompt_sistema, prompt_usuario, resposta,
            max_entradas=cache_cfg.get("max_entradas", 500),
            max_age_days=cache_cfg.get("max_age_days", 30),
        )

    return resposta


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
    return _chamar_llm(PROMPT_AGENTE, prompt_usuario, config, forcar_json=forcar_json)


def executar_chat(pergunta, config, historico=None):
    """Modo conversa livre: sem retrieval, sem Analista, sem Verify -- so a LLM
    respondendo direto, com no maximo um resumo curto do historico recente."""
    prompt_usuario = pergunta
    if historico:
        linhas = [f"{m['role']}: {m['text']}" for m in historico]
        prompt_usuario = "HISTORICO RECENTE DA CONVERSA:\n" + "\n".join(linhas) + f"\n\nMENSAGEM ATUAL:\n{pergunta}"
    return _chamar_llm(PROMPT_CHAT, prompt_usuario, config)


def executar_analista(prompt_usuario, config):
    """Primeira chamada da LLM: decide o que importa. Nunca gera codigo, nunca responde ao usuario."""
    return _chamar_llm(PROMPT_ANALISTA, prompt_usuario, config)


def executar_executor(prompt_usuario, config):
    """Segunda chamada da LLM: resolve o objetivo com o contexto ja compilado."""
    return _chamar_llm(PROMPT_EXECUTOR, prompt_usuario, config)


def executar_sugestor(prompt_usuario, config):
    """Quarta personalidade (Atualizacao 4): le o codigo real dos componentes
    ja escolhidos pelo Modelo Interno e devolve sugestoes fundamentadas.
    Nunca aplica nada -- so sugere."""
    return _chamar_llm(PROMPT_SUGESTOR, prompt_usuario, config)


def executar_engenheiro(prompt_usuario, config):
    """Quinta personalidade (Atualizacao 5): escreve o codigo novo completo
    de UM simbolo ja localizado por linha no arquivo real. Devolve o JSON
    de proposta -- quem aplica de fato (so apos confirmacao) e
    engine/codar.py:aplicar_patch, chamado por engine/engine.py."""
    return _chamar_llm(PROMPT_ENGENHEIRO, prompt_usuario, config)


def executar_entendedor(prompt_usuario, config):
    """Terceira personalidade: le um arquivo inteiro (uma vez, na ingestao) e
    devolve o retrato estrutural dele para o Modelo Interno do Projeto
    (memory/entendimento.json). Nunca gera codigo, nunca responde ao usuario --
    so descreve o arquivo que acabou de ler."""
    return _chamar_llm(PROMPT_ENTENDEDOR, prompt_usuario, config)


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
