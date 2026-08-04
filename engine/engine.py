#!/usr/bin/env python3
"""
engine.py
---------
Coordena o ciclo completo da Eyle:

    Retrieval -> LLM Analista -> (ciclo de investigacao opcional)
    -> Compilador de Contexto -> LLM Executor -> Verify
    -> Atualiza memoria (historico) -> Atualiza conversa

Nada aqui fala com o navegador. web/routes.py so poe eventos na fila
(engine/queue.py); engine/worker.py consome a fila e chama processar()
daqui. main.py (CLI) tambem chama processar() direto, para testar sem
precisar subir o Flask.
"""
import copy
import hashlib
import json
import os
import random
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from retrieval.buscar import buscar
from engine.compiler import (
    montar_prompt_analista, montar_prompt_executor, montar_prompt_visao_geral,
    montar_prompt_dicas, montar_prompt_engenheiro, montar_texto_proposta,
)
from engine.dicas import preparar_dicas
from engine.codar import localizar_simbolo, calcular_impacto, testar_patch_em_copia, aplicar_patch
from engine.roteador import (
    classificar_pergunta, classificar_modo_projeto, detectar_resposta_proposta,
)
from engine.agent import executar_agente
from llm.executar import (
    ErroLLM, executar_analista, executar_executor, executar_chat, executar_sugestor, executar_engenheiro,
)
from verify.validar import validar_resposta, registrar_historico
from engine.memoria_lock import lock_para
from engine.persistencia import salvar_json_atomico, salvar_texto_atomico
from engine.config_schema import carregar_config_validada
from engine import queue as fila_persistente

MEMORY_DIR = os.path.join(BASE_DIR, "memory")
CONTEXT_DIR = os.path.join(BASE_DIR, "context")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


# ---------------------------------------------------------------------------
# Leitura/escrita da memoria (config, projeto, estrutura, evidencias, conversa)
# ---------------------------------------------------------------------------

def _carregar_json(caminho, default):
    if not os.path.exists(caminho):
        return default
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)


def _salvar_json(caminho, dados):
    salvar_json_atomico(caminho, dados)


def carregar_config():
    return carregar_config_validada(CONFIG_PATH)


def _rollout_agente_configurado(config):
    """Resolve a chave 48 e conserva leitura de configs anteriores."""
    cfg = (config or {}).get("agent", {})
    modo = cfg.get("rollout_mode")
    if modo in ("off", "read_only", "full"):
        return modo
    return "full" if cfg.get("enabled", False) else "off"


def _projeto_full_confiavel(config, projeto):
    caminho = (projeto or {}).get("caminho_origem")
    if not caminho:
        return False
    confiaveis = (config or {}).get("agent", {}).get("trusted_project_paths") or []
    atual = os.path.realpath(os.path.abspath(str(caminho)))
    for raiz in confiaveis:
        raiz_real = os.path.realpath(os.path.abspath(os.path.expanduser(str(raiz))))
        try:
            if os.path.commonpath((atual, raiz_real)) == raiz_real:
                return True
        except ValueError:
            continue
    return False


def _rollout_agente_efetivo(config, projeto, execucao_explicita=False):
    configurado = _rollout_agente_configurado(config)
    # Compatibilidade: configs anteriores nao tinham a lista de confianca e
    # ja eram exercitadas como full pela CLI/testes.
    cfg_agente = (config or {}).get("agent", {})
    if configurado == "off":
        if execucao_explicita:
            return configurado, "read_only", "explicit_cli_while_off"
        return configurado, "off", "agent_rollout_off"
    if (
        configurado == "full"
        and "rollout_mode" in cfg_agente
        and not _projeto_full_confiavel(config, projeto)
    ):
        return configurado, "read_only", "project_not_in_trusted_paths"
    return configurado, configurado, None


def carregar_projeto():
    return _carregar_json(os.path.join(MEMORY_DIR, "projeto.json"), None)


def carregar_estrutura():
    return _carregar_json(os.path.join(MEMORY_DIR, "estrutura.json"), {}).get("arquivos", {})


def carregar_evidencias():
    return _carregar_json(
        os.path.join(MEMORY_DIR, "evidencias.json"),
        {"version": "1.0", "updated": None, "entidades": []},
    )


def salvar_evidencias(dados):
    caminho = os.path.join(MEMORY_DIR, "evidencias.json")
    with lock_para(caminho):
        dados["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _salvar_json(caminho, dados)


def carregar_entendimento():
    """memory/entendimento.json responde 'para que serve', separado de
    estrutura.json ('o que existe') e evidencias.json ('como se sabe')."""
    return _carregar_json(
        os.path.join(MEMORY_DIR, "entendimento.json"),
        {"version": "1.0", "updated": None, "componentes": {}},
    )


def carregar_decisoes():
    """memory/decisoes.json responde 'por que foi escolhido assim'."""
    return _carregar_json(
        os.path.join(MEMORY_DIR, "decisoes.json"),
        {"version": "1.0", "updated": None, "decisoes": []},
    ).get("decisoes", [])


def carregar_conversa():
    return _carregar_json(os.path.join(MEMORY_DIR, "conversa.json"), [])


def salvar_conversa(mensagens):
    _salvar_json(os.path.join(MEMORY_DIR, "conversa.json"), mensagens)


def registrar_mensagem_com_snapshot(role, texto, limite_snapshot=6):
    """Registra uma mensagem e captura, sob o mesmo lock, o historico do job.

    O snapshot atomico impede que outra requisicao web inclua uma mensagem
    posterior entre a gravacao da mensagem atual e a captura do contexto.
    """
    caminho = os.path.join(MEMORY_DIR, "conversa.json")
    with lock_para(caminho):
        mensagens = carregar_conversa()
        novo_id = (max((m["id"] for m in mensagens), default=0)) + 1
        mensagens.append({
            "id": novo_id,
            "role": role,
            "text": texto,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        salvar_conversa(mensagens)
        historico = _historico_sem_erros_llm(mensagens)
        if limite_snapshot is not None:
            limite_snapshot = max(0, int(limite_snapshot))
            historico = historico[-limite_snapshot:] if limite_snapshot else []
        return novo_id, [dict(mensagem) for mensagem in historico]


def registrar_mensagem(role, texto):
    """Adiciona uma mensagem em memory/conversa.json e devolve o id gerado.

    Bug 2 do plano de correcao: registrar_mensagem e' chamada tanto pela
    thread do Flask (web/routes.py:/enviar, na hora que o usuario manda a
    mensagem) quanto pela thread do Worker (engine/worker.py, quando grava
    a resposta do assistente) -- as duas rodam no MESMO processo ao mesmo
    tempo, por design (agente persistente). Sem lock, ler+somar+gravar
    conversa.json nao e' atomico entre as duas threads: da pra perder uma
    mensagem (lost update) ou gerar o mesmo id duas vezes. O lock cobre a
    operacao inteira (ler, calcular novo_id, gravar), nao so a escrita.
    """
    novo_id, _ = registrar_mensagem_com_snapshot(role, texto)
    return novo_id


def registrar_mensagem_se_nova(role, texto):
    """Republica uma resposta recuperada sem duplicar a ultima mensagem."""
    caminho = os.path.join(MEMORY_DIR, "conversa.json")
    with lock_para(caminho):
        mensagens = carregar_conversa()
        if mensagens and (
            mensagens[-1].get("role") == role
            and mensagens[-1].get("text") == texto
        ):
            return mensagens[-1].get("id")
        novo_id = max((m.get("id", 0) for m in mensagens), default=0) + 1
        mensagens.append({
            "id": novo_id,
            "role": role,
            "text": texto,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        salvar_conversa(mensagens)
        return novo_id


def remover_mensagem(mensagem_id):
    """Remove uma mensagem de memory/conversa.json pelo id. Devolve True se
    removeu algo. Mesmo lock de registrar_mensagem -- e' o mesmo arquivo,
    tambem chamado pelo Worker (via fila de /mensagem/<id>) enquanto o
    Flask pode estar processando outra requisicao ao mesmo tempo."""
    caminho = os.path.join(MEMORY_DIR, "conversa.json")
    with lock_para(caminho):
        mensagens = carregar_conversa()
        restantes = [m for m in mensagens if m["id"] != mensagem_id]
        removeu = len(restantes) != len(mensagens)
        if removeu:
            salvar_conversa(restantes)
        return removeu


# ---------------------------------------------------------------------------
# Pendencias de confirmacao -- proposta do Codar (Atualizacao 5) e tool
# WRITE do Agente (Fase 3), agora vinculadas a uma tarefa (Atualizacao 22).
# ---------------------------------------------------------------------------

PROPOSTA_PENDENTE_PATH = os.path.join(CONTEXT_DIR, "proposta_pendente.json")
AGENT_PENDENTE_PATH = os.path.join(CONTEXT_DIR, "agent_pendente.json")
_TTL_PENDENCIA_DEFAULT = 3600


def _agora_utc():
    return datetime.now(timezone.utc)


def _formatar_data_utc(valor):
    return valor.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_data_utc(valor):
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _hash_projeto(projeto):
    """Hash curto da identidade do projeto atualmente indexado.

    Usa nome + caminho real, nao o `source_hash` legado de projeto.json.
    O objetivo aqui e impedir que uma confirmacao criada para o projeto A
    seja aplicada depois de trocar para o projeto B.
    """
    projeto = projeto or {}
    caminho = projeto.get("caminho_origem")
    if not caminho:
        return None
    identidade = {
        "projeto": projeto.get("projeto") or projeto.get("nome") or "",
        "caminho_origem": os.path.realpath(os.path.abspath(str(caminho))),
    }
    bruto = json.dumps(identidade, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(bruto).hexdigest()[:16]


def _novo_id_pendencia():
    existentes = set()
    for caminho in (PROPOSTA_PENDENTE_PATH, AGENT_PENDENTE_PATH):
        atual = _carregar_json(caminho, None)
        if isinstance(atual, dict) and atual.get("id"):
            existentes.add(str(atual["id"]).upper())
    for tarefa in fila_persistente.listar_tarefas_agente(status="waiting_user", limite=200):
        continuacao = tarefa.get("continuacao") or {}
        if continuacao.get("id"):
            existentes.add(str(continuacao["id"]).upper())
    while True:
        candidato = secrets.token_hex(2).upper()
        if candidato not in existentes:
            return candidato


def _preparar_pendencia(dados, tipo, projeto, config=None):
    dados = dict(dados or {})
    cfg = (config or {}).get("confirmacoes", {})
    try:
        ttl = max(1, int(cfg.get("expiracao_segundos", _TTL_PENDENCIA_DEFAULT)))
    except (TypeError, ValueError):
        ttl = _TTL_PENDENCIA_DEFAULT
    criado = _agora_utc()
    dados.update({
        "id": _novo_id_pendencia(),
        "tipo_pendencia": tipo,
        "criado_em": _formatar_data_utc(criado),
        "expira_em": _formatar_data_utc(criado + timedelta(seconds=ttl)),
        "projeto_hash": _hash_projeto(projeto),
    })
    return dados


def _validar_pendencia(pendencia, projeto, agora=None):
    """Devolve (True, None) ou (False, motivo claro para o usuario)."""
    pendencia = pendencia or {}
    obrigatorios = ("id", "criado_em", "expira_em", "projeto_hash")
    if any(not pendencia.get(campo) for campo in obrigatorios):
        return False, "nao possui metadados de seguranca completos (pendencia de uma versao antiga)"

    expiracao = _parse_data_utc(pendencia.get("expira_em"))
    if expiracao is None:
        return False, "possui uma data de expiracao invalida"
    if (agora or _agora_utc()) >= expiracao:
        return False, f"expirou em {pendencia['expira_em']}"

    hash_atual = _hash_projeto(projeto)
    if hash_atual is None:
        return False, "nao pode ser confirmada porque nao ha projeto indexado agora"
    if pendencia.get("projeto_hash") != hash_atual:
        return False, "pertence a outro projeto indexado"
    return True, None


def carregar_proposta_pendente():
    return _carregar_json(PROPOSTA_PENDENTE_PATH, None)


def salvar_proposta_pendente(proposta, projeto=None, config=None):
    proposta = _preparar_pendencia(proposta, "proposta", projeto or carregar_projeto(), config)
    _salvar_json(PROPOSTA_PENDENTE_PATH, proposta)
    return proposta


def limpar_proposta_pendente():
    if os.path.exists(PROPOSTA_PENDENTE_PATH):
        os.remove(PROPOSTA_PENDENTE_PATH)


def carregar_agent_pendente():
    tarefas = fila_persistente.listar_tarefas_agente(status="waiting_user", limite=1)
    if tarefas:
        continuacao = dict(tarefas[0].get("continuacao") or {})
        continuacao.setdefault("task_id", tarefas[0]["task_id"])
        continuacao.setdefault("pergunta_ao_usuario", tarefas[0].get("pergunta"))
        return continuacao
    return _carregar_json(AGENT_PENDENTE_PATH, None)


def salvar_agent_pendente(estado_pendente, projeto=None, config=None):
    estado_pendente = _preparar_pendencia(
        estado_pendente, "agente", projeto or carregar_projeto(), config,
    )
    pergunta = (estado_pendente.get("pergunta_ao_usuario") or "Como deseja continuar?").rstrip()
    confirmavel = (
        (estado_pendente.get("tool_pendente") or {}).get("tool")
        not in (None, "__user_response__")
    )
    instrucao = (
        f"Para confirmar: confirmar {estado_pendente['id']}"
        if confirmavel else
        f"Responda normalmente para retomar; para cancelar: cancelar {estado_pendente['id']}"
    )
    estado_pendente["pergunta_ao_usuario"] = (
        f"{pergunta}\nID da pendencia: {estado_pendente['id']}. {instrucao}"
    )
    task_id = estado_pendente.get("task_id")
    if task_id:
        fila_persistente.atualizar_tarefa_agente(
            task_id,
            status="waiting_user",
            estado=estado_pendente.get("estado"),
            continuacao=estado_pendente,
            acao_pendente=estado_pendente.get("tool_pendente"),
            orcamento_restante=estado_pendente.get("orcamento_restante"),
            pergunta=estado_pendente["pergunta_ao_usuario"],
            expira_em=estado_pendente.get("expira_em"),
            evento={
                "tipo": "waiting_user_persisted",
                "pending_kind": estado_pendente.get("continuation_kind"),
            },
        )
    else:
        # Migração suave: estados antigos ainda conseguem concluir uma vez.
        _salvar_json(AGENT_PENDENTE_PATH, estado_pendente)
    return estado_pendente


def limpar_agent_pendente():
    if os.path.exists(AGENT_PENDENTE_PATH):
        os.remove(AGENT_PENDENTE_PATH)


def _pendencias_existentes():
    pendencias = []
    proposta = carregar_proposta_pendente()
    agente = carregar_agent_pendente()
    if proposta:
        pendencias.append({"tipo": "proposta", "dados": proposta})
    if agente:
        pendencias.append({"tipo": "agente", "dados": agente})
    task_id_atual = (agente or {}).get("task_id")
    for tarefa in fila_persistente.listar_tarefas_agente(status="waiting_user", limite=50):
        if tarefa.get("task_id") == task_id_atual:
            continue
        continuacao = dict(tarefa.get("continuacao") or {})
        if not continuacao:
            continue
        continuacao.setdefault("task_id", tarefa["task_id"])
        continuacao.setdefault("pergunta_ao_usuario", tarefa.get("pergunta"))
        pendencias.append({"tipo": "agente", "dados": continuacao})
    return pendencias


def _selecionar_pendencia(pergunta, pendencias):
    """Resolve o ID citado; sem ID, so aceita quando existe uma unica."""
    por_id = {
        str(item["dados"].get("id", "")).upper(): item
        for item in pendencias if item["dados"].get("id")
    }
    citados = [
        item for codigo, item in por_id.items()
        if re.search(rf"\b{re.escape(codigo)}\b", pergunta or "", re.IGNORECASE)
    ]
    if len(citados) == 1:
        return citados[0], None

    referencias = re.findall(r"\b[0-9A-Fa-f]{4}\b", pergunta or "")
    if referencias:
        return None, f"Nao existe pendencia ativa com o ID {referencias[0].upper()}."

    if len(pendencias) == 1:
        return pendencias[0], None

    opcoes = ", ".join(
        f"{item['tipo']} {item['dados'].get('id', 'SEM-ID')}" for item in pendencias
    )
    return None, (
        f"Ha mais de uma pendencia ativa ({opcoes}). Diga qual delas: "
        "use 'confirmar ID' ou 'cancelar ID'."
    )


def _limpar_pendencia_por_tipo(tipo, dados=None):
    if tipo == "proposta":
        limpar_proposta_pendente()
    else:
        task_id = (dados or {}).get("task_id")
        if task_id:
            fila_persistente.cancelar_tarefa_agente(
                task_id, motivo="pendencia expirada ou invalida",
            )
        limpar_agent_pendente()


def _resultado_controle_pendencia(resposta, motivo="confirmacao de pendencia recusada"):
    registrar_mensagem("assistant", resposta)
    return {
        "resposta": resposta,
        "roteador": {"tipo": "confirmacao_pendente", "motivo": motivo},
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        "confianca": None,
        "avisos": [resposta],
    }


# ---------------------------------------------------------------------------
# Ciclo do Analista (com investigacao opcional)
# ---------------------------------------------------------------------------

_RE_JSON_BLOCO = re.compile(r"\{.*\}", re.DOTALL)


def _parse_resposta_analista(texto):
    """
    Extrai o JSON de decisao do Analista. Se a LLM nao devolver JSON valido
    (acontece com modelos pequenos em Q4), cai num fallback seguro: nao pede
    mais nada, deixa o retrieval decidir sozinho -- assim o ciclo nunca trava
    por causa de uma resposta mal formatada do Analista.
    """
    match = _RE_JSON_BLOCO.search(texto or "")
    if match:
        try:
            dados = json.loads(match.group(0))
            dados.setdefault("ler", [])
            dados.setdefault("ignorar", [])
            dados.setdefault("faltando", [])
            dados.setdefault("riscos", [])
            dados.setdefault("motivo", "")
            return dados
        except json.JSONDecodeError:
            pass
    return {
        "ler": [], "ignorar": [], "faltando": [], "riscos": [],
        "motivo": "[fallback] Analista nao devolveu JSON valido; usando todos os candidatos do retrieval.",
        "_fallback": True,
    }


def _vencedor_claro(trechos, config):
    """
    Atalho da Atualizacao 2: pula o Analista quando o retrieval ja e' decisivo.

    Estrutura da regra (piso minimo e' SEMPRE obrigatorio, nao e' alternativa):

        top_score >= atalho_score_minimo
        E (
            so existe 1 candidato relevante
            OU top_score / segundo_score >= atalho_score_ratio
        )

    Ou seja: um candidato unico so pula o Analista se ele TAMBEM passar no
    piso minimo. Um candidato unico e fraco (retrieval so achou 1 trecho
    porque nada mais bateu bem) NAO e' um caso obvio -- e' exatamente o tipo
    de situacao ambigua que o Analista existe para julgar, entao cai nele
    normalmente.

    Controlado por config.json -> engine.atalho_analista_ativado /
    atalho_score_minimo / atalho_score_ratio.

    Devolve (True, motivo) ou (False, None).
    """
    cfg = config.get("engine", {})
    if not cfg.get("atalho_analista_ativado", True):
        return False, None
    if not trechos:
        return False, None

    minimo = cfg.get("atalho_score_minimo", 3.0)
    razao = cfg.get("atalho_score_ratio", 2.5)

    top = trechos[0].get("score", 0)
    if top < minimo:
        return False, None

    if len(trechos) == 1:
        return True, f"unico candidato relevante (score {top})"

    segundo = trechos[1].get("score", 0)
    if segundo <= 0 or top / segundo >= razao:
        return True, f"vencedor claro do retrieval (score {top} vs {segundo}, razao >= {razao}x)"

    return False, None


def _lista_seletores(valor):
    """Normaliza ``ler``/``ignorar`` sem confiar no formato da LLM."""
    if valor is None:
        return []
    if isinstance(valor, list):
        return valor
    return [valor]


def _seletor_corresponde_trecho(seletor, trecho):
    """
    Aceita o ID exibido ao Analista (``arquivo:linhas``), o nome do
    arquivo, o simbolo ou um objeto com esses campos. O formato tolerante
    preserva compatibilidade com respostas de modelos pequenos que devolvem
    apenas ``a.py`` mesmo quando o prompt oferece o ID mais preciso.
    """
    arquivo = str(trecho.get("arquivo") or "").strip()
    linhas = str(trecho.get("linhas") or "").strip()
    simbolo = str(trecho.get("simbolo") or "").strip()

    if isinstance(seletor, dict):
        arquivo_sel = str(seletor.get("arquivo") or "").strip()
        linhas_sel = str(seletor.get("linhas") or "").strip()
        simbolo_sel = str(seletor.get("simbolo") or "").strip()
        if arquivo_sel and arquivo_sel.casefold() != arquivo.casefold():
            return False
        if linhas_sel and linhas_sel.casefold() != linhas.casefold():
            return False
        if simbolo_sel and simbolo_sel.casefold() != simbolo.casefold():
            return False
        return bool(arquivo_sel or linhas_sel or simbolo_sel)

    texto = str(seletor or "").strip().casefold()
    if not texto:
        return False

    candidatos = {
        arquivo.casefold(),
        f"{arquivo}:{linhas}".casefold(),
    }
    if simbolo:
        candidatos.update({
            simbolo.casefold(),
            f"{arquivo} ({simbolo})".casefold(),
            f"{arquivo}:{linhas} ({simbolo})".casefold(),
        })
    return texto in candidatos


def _filtrar_trechos_decisao(trechos, decisao):
    """Aplica de verdade a decisao ``ler``/``ignorar`` do Analista."""
    if decisao.get("_fallback"):
        return list(trechos)

    ler = _lista_seletores(decisao.get("ler"))
    ignorar = _lista_seletores(decisao.get("ignorar"))
    aprovados = []
    for trecho in trechos:
        if any(_seletor_corresponde_trecho(s, trecho) for s in ignorar):
            continue
        if ler and not any(_seletor_corresponde_trecho(s, trecho) for s in ler):
            continue
        if ler or ignorar or decisao.get("_atalho"):
            aprovados.append(trecho)
    return aprovados


def _chave_trecho(trecho):
    return (
        trecho.get("arquivo"),
        trecho.get("linhas"),
        trecho.get("simbolo"),
        trecho.get("conteudo"),
    )


def _montar_atual_aprovado(pergunta, modelo_atual, trechos, historico, config):
    """Reconstrui ``atual`` para que disco, Executor e Verify vejam o mesmo contexto."""
    contexto_cfg = config.get("context", {})
    chars_por_token = contexto_cfg.get("chars_per_token", 4) or 4
    token_budget = contexto_cfg.get("token_budget", 1500)
    trechos_no_orcamento = []
    tokens_usados = 0
    for trecho in trechos:
        custo = max(1, len(str(trecho.get("conteudo") or "")) // chars_por_token)
        if token_budget is not None and tokens_usados + custo > token_budget:
            continue
        trechos_no_orcamento.append(trecho)
        tokens_usados += custo

    arquivos = []
    for trecho in trechos_no_orcamento:
        arquivo = trecho.get("arquivo")
        if arquivo and arquivo not in arquivos:
            arquivos.append(arquivo)

    arquivos_set = set(arquivos)
    historico = [
        item for item in historico
        if arquivos_set & set(item.get("arquivos_relevantes") or [])
    ]

    atual = dict(modelo_atual or {})
    atual.update({
        "pergunta": pergunta,
        "token_budget": token_budget,
        "tokens_usados": tokens_usados,
        "arquivos_relevantes": arquivos,
        "trechos": trechos_no_orcamento,
        "trechos_aprovados_fora_do_orcamento": len(trechos) - len(trechos_no_orcamento),
        "historico_relacionado": historico,
    })
    return atual


def ciclo_analista(pergunta, config, estrutura, evidencias, entendimento=None):
    """
    Retrieval -> Analista -> (se 'faltando' nao vazio: retrieval direcionado -> Analista de novo)
    ate `engine.max_iteracoes_analista` (config.json) ou ate o Analista nao pedir mais nada.

    Devolve (contexto aprovado acumulado, lista_de_decisoes_do_analista).
    """
    max_iteracoes = config.get("engine", {}).get("max_iteracoes_analista", 2)
    atual_path = os.path.join(CONTEXT_DIR, "atual.json")

    pergunta_busca = pergunta
    decisoes = []
    atual = None
    trechos_aprovados = []
    chaves_aprovadas = set()
    historico_acumulado = []
    chaves_historico = set()
    cache_buscas = {}
    faltantes_vistos = set()

    for iteracao in range(1, max_iteracoes + 1):
        assinatura_busca = " ".join(str(pergunta_busca or "").casefold().split())
        if assinatura_busca in cache_buscas:
            atual = copy.deepcopy(cache_buscas[assinatura_busca])
        else:
            atual = buscar(
                pergunta_busca, memory_dir=MEMORY_DIR,
                config=config, out_path=atual_path,
            )
            cache_buscas[assinatura_busca] = copy.deepcopy(atual)
        trechos = atual.get("trechos", [])

        claro, motivo_atalho = _vencedor_claro(trechos, config)
        if claro:
            decisoes.append({
                "ler": [t["arquivo"] for t in trechos],
                "ignorar": [],
                "faltando": [],
                "riscos": [],
                "motivo": f"[atalho] {motivo_atalho} -- Analista pulado.",
                "_atalho": True,
            })
            break

        prompt_analista = montar_prompt_analista(
            pergunta=pergunta,
            candidatos=trechos,
            estrutura=estrutura,
            historico_relacionado=atual.get("historico_relacionado"),
            evidencias=evidencias,
            entendimento=entendimento,
            iteracao=iteracao,
            respostas_anteriores=decisoes,
        )
        resposta_bruta = executar_analista(prompt_analista, config)
        decisao = _parse_resposta_analista(resposta_bruta)
        decisoes.append(decisao)

        # Atualizacao 23: ``ler`` e ``ignorar`` deixam de ser apenas
        # informativos. Cada rodada contribui com os trechos aprovados, sem
        # apagar evidencias boas encontradas nas rodadas anteriores.
        for trecho in _filtrar_trechos_decisao(trechos, decisao):
            chave = _chave_trecho(trecho)
            if chave not in chaves_aprovadas:
                chaves_aprovadas.add(chave)
                trechos_aprovados.append(trecho)

        for item in atual.get("historico_relacionado") or []:
            chave = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if chave not in chaves_historico:
                chaves_historico.add(chave)
                historico_acumulado.append(item)

        faltando = decisao.get("faltando") or []
        if not faltando or iteracao == max_iteracoes:
            break

        assinatura_faltando = tuple(sorted(
            " ".join(str(item).casefold().split())
            for item in faltando if str(item).strip()
        ))
        if not assinatura_faltando:
            break
        if assinatura_faltando in faltantes_vistos:
            decisao.setdefault("riscos", []).append(
                "Investigacao interrompida: o Analista repetiu exatamente as mesmas lacunas sem progresso."
            )
            decisao["_early_exit"] = "repeated_missing"
            break
        faltantes_vistos.add(assinatura_faltando)

        # retrieval direcionado: a proxima rodada busca especificamente o que falta
        proxima_busca = pergunta + " " + " ".join(faltando)
        if " ".join(proxima_busca.casefold().split()) == assinatura_busca:
            decisao.setdefault("riscos", []).append(
                "Investigacao interrompida: a busca direcionada seria identica a anterior."
            )
            decisao["_early_exit"] = "identical_retrieval_query"
            break
        pergunta_busca = proxima_busca

    # O atalho nao passa pelo bloco de filtragem acima; nesse caso todos os
    # candidatos ja foram explicitamente aprovados pela regra objetiva.
    if decisoes and decisoes[-1].get("_atalho"):
        for trecho in _filtrar_trechos_decisao(atual.get("trechos", []), decisoes[-1]):
            chave = _chave_trecho(trecho)
            if chave not in chaves_aprovadas:
                chaves_aprovadas.add(chave)
                trechos_aprovados.append(trecho)
        for item in atual.get("historico_relacionado") or []:
            chave = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if chave not in chaves_historico:
                chaves_historico.add(chave)
                historico_acumulado.append(item)

    atual = _montar_atual_aprovado(
        pergunta, atual, trechos_aprovados, historico_acumulado, config,
    )
    _salvar_json(atual_path, atual)
    return atual, decisoes


def _esperar_retry_executor(config, tentativa):
    """Backoff curto entre respostas reprovadas pelo Verify.

    O retry de transporte vive em ``llm/executar.py``. Este backoff cobre o
    segundo tipo de repeticao: o backend respondeu, mas o Verify recusou a
    resposta. Sem pausa, duas chamadas pesadas podiam ser disparadas em rajada.
    """
    cfg = (config or {}).get("engine", {})
    base = max(0.0, float(cfg.get("executor_retry_base_delay_seconds", 0.0)))
    max_delay = max(
        base, float(cfg.get("executor_retry_max_delay_seconds", base)),
    )
    jitter = max(0.0, float(cfg.get("executor_retry_jitter_seconds", 0.0)))
    atraso = min(max_delay, base * (2 ** max(0, int(tentativa) - 1)))
    if jitter:
        atraso += random.uniform(0.0, jitter)

    runtime = (config or {}).get("_runtime_agent_budget") or {}
    deadline = runtime.get("deadline_monotonic")
    if deadline is not None:
        restante = max(0.0, float(deadline) - time.monotonic())
        atraso = min(atraso, restante)
    if atraso > 0:
        time.sleep(atraso)
    return atraso


# ---------------------------------------------------------------------------
# Ciclo completo
# ---------------------------------------------------------------------------

def processar(pergunta, registrar_pergunta=True, forcar_tipo=None, historico_snapshot=None,
              task_id=None, source_job_id=None):
    """
    Roteia a pergunta ANTES de qualquer retrieval/LLM pesada. Na configuracao
    2.4, o alto nivel e conversa livre ou Agente Eyle; os pipelines antigos
    abaixo permanecem como estrategias/fallbacks internos:

        chat         -> Executor direto (sem retrieval, sem Analista, sem Verify)
        consulta     -> Retrieval + Executor (sem Analista, sem retry)
        dicas        -> Modelo Interno escolhe componentes -> le codigo real ->
                         Sugestor analisa e sugere (Atualizacao 4, sem retrieval BM25)
        visao_geral  -> panorama direto de estrutura/entendimento, sem retrieval
        engenharia   -> pipeline completo: Retrieval -> Analista -> Executor -> Verify
                         (Atualizacao 5: quando o alvo e um unico simbolo claro,
                         tenta virar uma PROPOSTA de patch de verdade em vez de
                         so texto -- ver _tentar_gerar_proposta)
        agente       -> qualquer pedido sobre projeto; delega a tarefa para
                         engine/agent.py:executar_agente nos modos analyze/suggest
                         e usa engenharia como fallback temporario do modo edit
                         (loop proprio de tool_calls: search_code/find_symbol/
                         read_file/test_patch_dry_run/run_tests/apply_patch) --
                         atras de agent.rollout_mode/enabled_modes

    Atualizacoes 5/Fase 3/22: ANTES de rotear normalmente, reune proposta
    do Codar e tool confirmavel (WRITE/EXEC) do Agente pendentes. Uma confirmacao/cancelamento
    tem prioridade sobre o roteador, mas so e executada depois de validar
    ID, expiracao e hash do projeto. Com uma unica pendencia, 'sim'/'nao'
    continuam suficientes; com duas, exige 'confirmar ID'/'cancelar ID' em
    vez de escolher silenciosamente a proposta primeiro. Mensagem que nao
    seja resposta a pendencia segue o fluxo normal sem descarta-la. Isso so
    roda quando forcar_tipo e' None (ver abaixo).

    forcar_tipo: quando informado ("agente", por exemplo), pula
    classificar_pergunta e a checagem de proposta pendente, e roda direto o
    pipeline daquele tipo -- usado por 'python main.py agente "objetivo"'
    (main.py:cmd_agente) pra garantir que a tarefa chega em executar_agente()
    mesmo com rollout `off`; nesse caso explicito, roda em `read_only` com
    trace, sem alterar o roteamento automatico.

    historico_snapshot: copia imutavel do historico capturada quando um
    job web entrou na fila. So o pipeline de chat a consome; sem ela, o
    comportamento da CLI continua carregando a conversa atual do disco.

    Chamado tanto pelo worker (fila) quanto pelo CLI (main.py).
    """
    config = dict(carregar_config() or {})
    cfg_agente_runtime = config.get("agent", {})
    deadline_segundos = max(
        1, int(config.get("engine", {}).get("task_deadline_seconds", 300)),
    )
    agora_monotonic = time.monotonic()
    config["_runtime_agent_budget"] = {
        "started_monotonic": agora_monotonic,
        "deadline_monotonic": agora_monotonic + deadline_segundos,
        "task_id": task_id,
        "source_job_id": source_job_id,
        "max_llm_calls": max(1, int(cfg_agente_runtime.get("max_llm_calls", 12))),
        "max_generated_tokens": max(
            1, int(cfg_agente_runtime.get("max_total_generated_tokens", 12000)),
        ),
        "llm_calls": 0,
        "generated_tokens": 0,
    }
    projeto = carregar_projeto()

    if registrar_pergunta:
        registrar_mensagem("user", pergunta)

    if forcar_tipo is None:
        pendencias = _pendencias_existentes()
        decisao_pendencia = detectar_resposta_proposta(pergunta) if pendencias else None
        if decisao_pendencia:
            selecionada, erro_selecao = _selecionar_pendencia(pergunta, pendencias)
            if erro_selecao:
                return _resultado_controle_pendencia(erro_selecao)

            tipo_pendencia = selecionada["tipo"]
            dados_pendencia = selecionada["dados"]
            valida, motivo_invalido = _validar_pendencia(dados_pendencia, projeto)
            if not valida:
                _limpar_pendencia_por_tipo(tipo_pendencia, dados_pendencia)
                resposta = (
                    f"A pendencia {dados_pendencia.get('id', 'SEM-ID')} foi rejeitada: "
                    f"{motivo_invalido}. Ela foi descartada; gere a tarefa novamente."
                )
                return _resultado_controle_pendencia(resposta, "pendencia expirada ou de outro projeto")

            continuacao_livre = (
                tipo_pendencia == "agente"
                and dados_pendencia.get("continuation_kind") == "user_input"
            )
            cancelamento_explicito = bool(re.search(
                r"\b(cancel|cancelar|cancela|descart|abortar|aborta)\w*\b",
                pergunta or "", re.IGNORECASE,
            ))
            if continuacao_livre and not cancelamento_explicito:
                return _retomar_agente_pendente(
                    dados_pendencia, config, resposta_usuario=pergunta,
                )

            if decisao_pendencia == "aplicar":
                if tipo_pendencia == "proposta":
                    return _aplicar_proposta_pendente(dados_pendencia, config)
                return _retomar_agente_pendente(
                    dados_pendencia, config, resposta_usuario=pergunta,
                )

            if tipo_pendencia == "proposta":
                return _cancelar_proposta_pendente(dados_pendencia)
            return _cancelar_agente_pendente(dados_pendencia)

        # Atualizacao 49: uma pergunta livre do Agente tambem e continuacao.
        # Com uma unica espera, a proxima mensagem e a resposta; com varias,
        # pede-se o ID para nao retomar a tarefa errada.
        continuacoes_livres = [
            item for item in pendencias
            if item["tipo"] == "agente"
            and item["dados"].get("continuation_kind") == "user_input"
        ]
        if len(continuacoes_livres) == 1:
            return _retomar_agente_pendente(
                continuacoes_livres[0]["dados"], config,
                resposta_usuario=pergunta,
            )
        if len(continuacoes_livres) > 1:
            selecionada, erro_selecao = _selecionar_pendencia(
                pergunta, continuacoes_livres,
            )
            if selecionada is not None:
                return _retomar_agente_pendente(
                    selecionada["dados"], config, resposta_usuario=pergunta,
                )
            return _resultado_controle_pendencia(erro_selecao, "retomada ambigua")

    estrutura = carregar_estrutura() if projeto else {}
    entendimento = carregar_entendimento() if projeto else {"componentes": {}}

    if forcar_tipo is not None:
        tipo, motivo_roteador = forcar_tipo, "tipo forcado explicitamente (bypass do roteador heuristico)"
    else:
        agent_habilitado = _rollout_agente_configurado(config) != "off"
        tipo, motivo_roteador = classificar_pergunta(pergunta, estrutura, entendimento, agent_habilitado)

    fallback_rollout = (
        "agent_rollout_off"
        if forcar_tipo is None
        and _rollout_agente_configurado(config) == "off"
        and tipo != "chat"
        else None
    )

    def _anotar_fallback_rollout(resultado):
        if fallback_rollout and isinstance(resultado, dict):
            resultado["agent_rollout"] = "off"
            resultado["fallback_cause"] = fallback_rollout
        return resultado

    if tipo == "chat":
        return _processar_chat(
            pergunta, config, motivo_roteador, historico_snapshot=historico_snapshot,
        )

    # A partir daqui o tipo pede alguma leitura do projeto (consulta/visao_geral/
    # engenharia). Se nao ha projeto indexado, NAO rebaixa mais silenciosamente
    # pra chat -- isso e o que causava o "loop generico" (o roteador acertava a
    # classificacao, mas o resultado virava uma conversa livre sem avisar o
    # usuario do motivo real). Agora avisa direto e objetivamente.
    if projeto is None:
        resposta = (
            "Nenhum projeto indexado ainda, entao nao tenho o que ler pra responder isso. "
            "Rode: python ingest.py /caminho/do/projeto"
        )
        registrar_mensagem("assistant", resposta)
        return {
            "resposta": resposta,
            "roteador": {"tipo": tipo, "motivo": motivo_roteador + " (nenhum projeto indexado ainda)"},
            "iteracoes_analista": 0,
            "decisoes_analista": [],
            "confianca": None,
            "avisos": [],
        }

    if tipo == "consulta":
        return _anotar_fallback_rollout(
            _processar_consulta(pergunta, config, projeto, estrutura, entendimento, motivo_roteador)
        )

    if tipo == "dicas":
        return _anotar_fallback_rollout(
            _processar_dicas(pergunta, config, projeto, entendimento, motivo_roteador)
        )

    if tipo == "visao_geral":
        return _anotar_fallback_rollout(
            _processar_visao_geral(pergunta, config, projeto, estrutura, entendimento, motivo_roteador)
        )

    if tipo == "agente":
        if forcar_tipo != "agente" and task_id is None and source_job_id is None:
            return _processar_agente(
                pergunta, config, projeto, entendimento, motivo_roteador,
            )
        return _processar_agente(
            pergunta, config, projeto, entendimento, motivo_roteador,
            execucao_explicita=forcar_tipo == "agente",
            task_id=task_id,
            source_job_id=source_job_id,
        )

    return _anotar_fallback_rollout(
        _processar_engenharia(pergunta, config, projeto, estrutura, entendimento, motivo_roteador)
    )


def _historico_sem_erros_llm(mensagens):
    """Atualizacao 14: filtra mensagens legadas com prefixo ``[erro]``
    antes de virarem HISTORICO RECENTE pra LLM.

    Bug corrigido: quando o servidor local falhava (timeout, conexao
    recusada etc.), _chamar_llm devolve uma string '[erro] ...' que
    registrar_mensagem("assistant", ...) salvava em conversa.json
    exatamente como qualquer resposta real. Na proxima mensagem,
    _processar_chat recarregava as ultimas 6 mensagens cruas e mandava
    isso de volta pra LLM como conversa de verdade -- fazendo o modelo
    "reagir" ao proprio erro anterior (ex: comentar sobre "interrupcao
    na conexao" numa mensagem que so dizia "ola"), em vez de conversar
    normalmente. Desde a Atualizacao 20 erros novos levantam ErroLLM e
    nem sao salvos como mensagem; este filtro continua para limpar o
    historico legado criado antes dela. Filtra ANTES de cortar as ultimas
    6, pra uma sequencia de erros nao empurrar conteudo real pra fora da
    janela."""
    return [m for m in mensagens if not (m.get("text") or "").startswith("[erro]")]


def _historico_sem_mensagem_atual(mensagens, pergunta):
    """Atualizacao 31: remove somente a ocorrencia corrente da pergunta.

    Tanto a CLI quanto a fila web registram a mensagem do usuario antes de
    chamar o pipeline. Sem este corte, ela entrava uma vez em HISTORICO
    RECENTE e outra em MENSAGEM ATUAL. Remove apenas o ultimo item quando ele
    e' exatamente a pergunta corrente, preservando perguntas iguais feitas em
    turnos anteriores.
    """
    historico = list(mensagens or [])
    if (
        historico
        and historico[-1].get("role") == "user"
        and historico[-1].get("text") == pergunta
    ):
        return historico[:-1]
    return historico


def _campos_validacao(resultado_validacao):
    """Campos publicos do Verify, separados pela Atualizacao 30."""
    return {
        "confianca": resultado_validacao.get("confianca"),
        "citation_validity": resultado_validacao.get("citation_validity"),
        "coverage": resultado_validacao.get("coverage"),
        "grounding": resultado_validacao.get("grounding"),
        "verificacao_aprovada": resultado_validacao.get("verificacao_aprovada"),
        "avisos": resultado_validacao.get("avisos", []),
    }


def _resultado_falha_llm(tipo, motivo_roteador, erro, **extras):
    """Converte ErroLLM em estado de pipeline, nunca em fala do assistente.

    Deliberadamente nao chama registrar_mensagem, validar_resposta nem
    registrar_historico: uma falha de transporte/backend nao e' conteudo
    produzido pela LLM e nao pode ganhar confianca ou contaminar conversa.
    """
    detalhe = str(erro)
    resultado = {
        "status": "failed",
        "error_code": getattr(erro, "error_code", None) or "LLM_FAILURE",
        "transient": bool(getattr(erro, "transient", False)),
        "http_status": getattr(erro, "status_code", None),
        "resposta": f"Nao foi possivel obter uma resposta da LLM local. {detalhe}",
        "roteador": {"tipo": tipo, "motivo": motivo_roteador},
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        "confianca": None,
        "avisos": [detalhe],
    }
    resultado.update(extras)
    return resultado


def _processar_chat(pergunta, config, motivo_roteador, historico_snapshot=None):
    """Pipeline 'chat': zero retrieval, zero Analista, zero Verify -- so a LLM."""
    origem_historico = carregar_conversa() if historico_snapshot is None else historico_snapshot
    historico = _historico_sem_erros_llm(origem_historico)
    historico = _historico_sem_mensagem_atual(historico, pergunta)[-6:]
    try:
        resposta = executar_chat(pergunta, config, historico=historico)
    except ErroLLM as erro:
        return _resultado_falha_llm("chat", motivo_roteador, erro)
    registrar_mensagem("assistant", resposta)
    return {
        "resposta": resposta,
        "roteador": {"tipo": "chat", "motivo": motivo_roteador},
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        "confianca": None,
        "avisos": [],
    }


def _processar_consulta(pergunta, config, projeto, estrutura, entendimento, motivo_roteador):
    """Pipeline 'consulta': Retrieval + Executor direto, sem Analista e sem retry --
    a pergunta so precisa de leitura/explicacao, nao de uma decisao de risco."""
    atual = buscar(pergunta, memory_dir=MEMORY_DIR, config=config,
                    out_path=os.path.join(CONTEXT_DIR, "atual.json"))

    tem_entendimento = any(
        item.get("funcao") for item in entendimento.get("componentes", {}).values()
    )

    if not atual.get("trechos") and not tem_entendimento:
        resposta = "Nao encontrei nada relevante na memoria indexada para essa pergunta."
        registrar_mensagem("assistant", resposta)
        return {
            "resposta": resposta,
            "roteador": {"tipo": "consulta", "motivo": motivo_roteador},
            "iteracoes_analista": 0,
            "decisoes_analista": [],
            "confianca": None,
            "avisos": [],
        }

    evidencias = carregar_evidencias().get("entidades", [])
    prompt_executor = montar_prompt_executor(
        atual, projeto=projeto, evidencias=evidencias, entendimento=entendimento,
    )
    try:
        resposta = executar_executor(prompt_executor, config)
    except ErroLLM as erro:
        return _resultado_falha_llm("consulta", motivo_roteador, erro)

    salvar_texto_atomico(os.path.join(CONTEXT_DIR, "ultima_resposta.txt"), resposta)

    resultado_validacao = validar_resposta(resposta, MEMORY_DIR, atual.get("arquivos_relevantes"))
    registrar_historico(MEMORY_DIR, pergunta, atual.get("arquivos_relevantes", []), resultado_validacao,
                         resumo_decisao="consulta respondida sem Analista (roteador)")
    registrar_mensagem("assistant", resposta)

    return {
        "resposta": resposta,
        "roteador": {"tipo": "consulta", "motivo": motivo_roteador},
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        **_campos_validacao(resultado_validacao),
    }


def _processar_dicas(pergunta, config, projeto, entendimento, motivo_roteador):
    """Pipeline 'dicas' (Atualizacao 4): NAO usa retrieval/buscar.py (BM25 sobre
    chunks) -- usa o Modelo Interno do Projeto (entendimento.json['arquivos'])
    para escolher componentes candidatos por tipo/responsabilidade/depende_de/
    pontos_criticos (engine/dicas.py), le o CODIGO REAL desses componentes
    (arquivo inteiro, nao chunk) e manda pro Sugestor. Sem Analista (nao ha
    decisao de risco a tomar, e so sugestao) e sem retry (e uma opiniao
    fundamentada no codigo, nao um fato verificavel linha a linha como no
    Executor de engenharia)."""
    arquivos_entendidos = (entendimento or {}).get("arquivos", {})
    if not arquivos_entendidos:
        resposta = (
            "Ainda nao tenho o Modelo Interno do Projeto (entendimento.json['arquivos']) "
            "para dar uma dica fundamentada no codigo real. Rode 'python main.py ingest' "
            "sem a flag --pular-entendimento-llm para gerar isso primeiro."
        )
        registrar_mensagem("assistant", resposta)
        return {
            "resposta": resposta,
            "roteador": {"tipo": "dicas", "motivo": motivo_roteador},
            "iteracoes_analista": 0,
            "decisoes_analista": [],
            "confianca": None,
            "avisos": [],
        }

    caminho_projeto = (projeto or {}).get("caminho_origem")
    candidatos, codigos = preparar_dicas(pergunta, entendimento, caminho_projeto, config=config)

    if not candidatos:
        resposta = (
            "Nao encontrei nenhum componente do Modelo Interno que bata com essa pergunta "
            "(tipo/responsabilidade/funcoes_principais/pontos_criticos). Tente ser mais "
            "especifico sobre que parte do projeto voce quer sugestao."
        )
        registrar_mensagem("assistant", resposta)
        return {
            "resposta": resposta,
            "roteador": {"tipo": "dicas", "motivo": motivo_roteador},
            "iteracoes_analista": 0,
            "decisoes_analista": [],
            "confianca": None,
            "avisos": [],
        }

    prompt_sugestor = montar_prompt_dicas(
        pergunta, candidatos, codigos, projeto=projeto, entendimento=entendimento,
    )
    try:
        resposta = executar_sugestor(prompt_sugestor, config)
    except ErroLLM as erro:
        return _resultado_falha_llm("dicas", motivo_roteador, erro)

    salvar_texto_atomico(os.path.join(CONTEXT_DIR, "ultima_resposta.txt"), resposta)

    arquivos_relevantes = [c["arquivo"] for c in candidatos]
    resultado_validacao = validar_resposta(resposta, MEMORY_DIR, arquivos_relevantes)
    registrar_historico(
        MEMORY_DIR, pergunta, arquivos_relevantes, resultado_validacao,
        resumo_decisao="dicas geradas a partir do Modelo Interno + codigo real (Sugestor)",
    )
    registrar_mensagem("assistant", resposta)

    return {
        "resposta": resposta,
        "roteador": {"tipo": "dicas", "motivo": motivo_roteador},
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        **_campos_validacao(resultado_validacao),
    }


def _processar_visao_geral(pergunta, config, projeto, estrutura, entendimento, motivo_roteador):
    """Pipeline 'visao_geral': pedido generico tipo 'da uma olhada no projeto',
    'confere o codigo' -- NAO roda retrieval (a pergunta nao tem vocabulario em
    comum com o codigo, BM25 so acharia ruido). Monta o panorama direto de
    estrutura.json + entendimento.json + decisoes.json e manda pro Executor.
    Sem Analista (e so leitura, nao ha risco de mudanca) e sem retry."""
    ctx_cfg = config.get("context", {})
    decisoes = carregar_decisoes()

    prompt_executor = montar_prompt_visao_geral(
        pergunta, projeto=projeto, estrutura=estrutura, entendimento=entendimento,
        decisoes=decisoes, token_budget=ctx_cfg.get("token_budget", 1500),
        chars_per_token=ctx_cfg.get("chars_per_token", 4),
    )
    try:
        resposta = executar_executor(prompt_executor, config)
    except ErroLLM as erro:
        return _resultado_falha_llm("visao_geral", motivo_roteador, erro)

    salvar_texto_atomico(os.path.join(CONTEXT_DIR, "ultima_resposta.txt"), resposta)

    arquivos_no_mapa = list(estrutura.keys()) if estrutura else []
    resultado_validacao = validar_resposta(resposta, MEMORY_DIR, arquivos_no_mapa)
    registrar_historico(MEMORY_DIR, pergunta, arquivos_no_mapa, resultado_validacao,
                         resumo_decisao="visao geral do projeto (sem retrieval, roteador)")
    registrar_mensagem("assistant", resposta)

    return {
        "resposta": resposta,
        "roteador": {"tipo": "visao_geral", "motivo": motivo_roteador},
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        **_campos_validacao(resultado_validacao),
    }


def _fallback_leitura_legado(
    pergunta, config, projeto, entendimento, motivo_roteador, task_id, causa,
):
    """Usa os pipelines de leitura sem JSON quando o Agente estruturado falha.

    O fallback existe apenas para tarefas READ. Ele reaproveita a classificacao
    legada com ``agent_habilitado=False`` e nunca encaminha pedidos de edicao.
    Assim, uma falha de transporte ou de formato do protocolo interno nao vira
    uma resposta vazia quando o Executor textual ainda esta funcional.
    """
    estrutura = carregar_estrutura()
    tipo_legado, motivo_legado = classificar_pergunta(
        pergunta, estrutura, entendimento, agent_habilitado=False,
    )
    if tipo_legado not in {"consulta", "dicas", "visao_geral"}:
        return None

    motivo_fallback = (
        f"{motivo_roteador}; fallback de leitura apos {causa}: {motivo_legado}"
    )
    if tipo_legado == "consulta":
        resultado = _processar_consulta(
            pergunta, config, projeto, estrutura, entendimento, motivo_fallback,
        )
    elif tipo_legado == "dicas":
        resultado = _processar_dicas(
            pergunta, config, projeto, entendimento, motivo_fallback,
        )
    else:
        resultado = _processar_visao_geral(
            pergunta, config, projeto, estrutura, entendimento, motivo_fallback,
        )

    falhou = isinstance(resultado, dict) and resultado.get("status") == "failed"
    detalhes = {
        "task_id": task_id,
        "task_type": "project_read",
        "mode": classificar_modo_projeto(pergunta),
        "response": (resultado or {}).get("resposta") if isinstance(resultado, dict) else None,
        "completion_gate": {
            "code": "legacy_read_fallback_failed" if falhou else "legacy_read_fallback",
            "passed": not falhou,
        },
        "fallback_cause": causa,
        "fallback_pipeline": tipo_legado,
        "evidence_ids": [],
        "evidencias_usadas": [],
    }
    fila_persistente.atualizar_tarefa_agente(
        task_id,
        status="failed" if falhou else "completed",
        continuacao=None,
        acao_pendente=None,
        pergunta=None,
        resultado=detalhes,
        causa_fallback=causa,
        evento={
            "tipo": "legacy_read_fallback",
            "pipeline": tipo_legado,
            "cause": causa,
            "success": not falhou,
        },
    )
    if isinstance(resultado, dict):
        resultado["agente_status"] = "failed" if falhou else "success"
        resultado["agente_conclusao"] = detalhes
        roteador = resultado.setdefault("roteador", {})
        roteador["tipo"] = "agente_fallback_leitura"
        roteador["fallback_pipeline"] = tipo_legado
        roteador["fallback_cause"] = causa
        roteador["task_id"] = task_id
    return resultado


def _desempacotar_resultado_agente(resultado):
    """Aceita o contrato 42 (4 itens) e mocks/implementacoes legadas (3)."""
    if len(resultado) == 4:
        return resultado
    status, texto, estado_pendente = resultado
    return status, texto, estado_pendente, {
        "task_type": None,
        "goal_state": {},
        "evidence_ids": [],
        "evidencias_usadas": [],
        "limitacoes": [],
    }


def _persistir_checkpoint_agente(payload):
    """Adaptador fino entre o loop e a tabela ``agent_tasks``."""
    return fila_persistente.atualizar_tarefa_agente(
        payload["task_id"],
        status=payload.get("status"),
        estado=payload.get("estado"),
        continuacao=payload.get("continuacao"),
        acao_pendente=payload.get("acao_pendente"),
        orcamento_restante=payload.get("orcamento_restante"),
        pergunta=payload.get("pergunta"),
        resultado=payload.get("resultado"),
        causa_fallback=payload.get("causa_fallback"),
        evento=payload.get("evento"),
    )


def _processar_agente(pergunta, config, projeto, entendimento, motivo_roteador,
                      execucao_explicita=False, task_id=None, source_job_id=None):
    """Pipeline 'agente' (Atualizacao Agente / Fase 2 -- conecta o roteador a
    engine/agent.py, que ja existia mas nunca era chamado a partir de uma
    mensagem real de usuario). Delega a tarefa inteira pro loop proprio do
    Agente (executar_agente): decidir_passo -> tool_call (search_code/
    find_symbol/read_file/test_patch_dry_run/run_tests/apply_patch) ->
    observar -> repete ate 'final'/'needs_user'/'max_steps'/'failed'. Sem
    Retrieval/Analista daqui -- o proprio Agente decide o que ler, e ja tem sua
    guarda de chamada repetida e limite de passos (config['agent']). Desde a
    Atualizacao 43, o resultado aceito pelo gate de evidencias tambem passa pelo
    Verify honesto para publicar citacao/cobertura/grounding separados.

    Atualizacao 49: todo 'needs_user' produz continuacao em `agent_tasks`, na
    mesma base SQLite da fila. A proxima resposta e interceptada antes do
    roteador, reidrata GoalState/evidencias/orcamento e continua o passo exato.
    O JSON legado e aceito somente para uma ultima migracao compativel."""
    modo = classificar_modo_projeto(pergunta)
    rollout_configurado, rollout_efetivo, causa_rollout = _rollout_agente_efetivo(
        config, projeto, execucao_explicita=execucao_explicita,
    )
    config_execucao = dict(config)
    config_execucao["agent"] = dict(config.get("agent", {}))
    config_execucao["agent"]["rollout_mode"] = rollout_efetivo

    tarefa = fila_persistente.criar_tarefa_agente(
        pergunta,
        modo,
        projeto_hash=_hash_projeto(projeto),
        task_id=task_id,
        source_job_id=source_job_id,
    )
    task_id = tarefa["task_id"]

    if tarefa.get("status") == "completed" and isinstance(tarefa.get("resultado"), dict):
        detalhes_salvos = tarefa["resultado"]
        texto_salvo = detalhes_salvos.get("response") or "Tarefa ja concluida."
        registrar_mensagem_se_nova("assistant", texto_salvo)
        return {
            "resposta": texto_salvo,
            "roteador": {
                "tipo": "agente", "motivo": "resultado idempotente ja persistido",
                "modo": modo, "rollout": rollout_efetivo, "task_id": task_id,
            },
            "iteracoes_analista": 0,
            "decisoes_analista": [],
            "confianca": None,
            "avisos": [],
            "agente_status": "success",
            "agente_conclusao": detalhes_salvos,
        }

    if tarefa.get("status") == "waiting_user" and tarefa.get("continuacao"):
        texto_espera = tarefa.get("pergunta") or "A tarefa ainda aguarda sua resposta."
        registrar_mensagem_se_nova("assistant", texto_espera)
        return {
            "resposta": texto_espera,
            "roteador": {
                "tipo": "agente", "motivo": "tarefa recuperada aguardando usuario",
                "modo": modo, "rollout": rollout_efetivo, "task_id": task_id,
            },
            "iteracoes_analista": 0,
            "decisoes_analista": [],
            "confianca": None,
            "avisos": [],
            "agente_status": "needs_user",
            "agente_conclusao": {
                "task_id": task_id,
                "completion_gate": {"code": "user_input_required", "passed": False},
                "fallback_cause": tarefa.get("causa_fallback"),
            },
        }

    retomar_automatico = None
    if tarefa.get("status") == "running" and tarefa.get("continuacao"):
        acao = tarefa.get("acao_pendente") or {}
        if acao.get("permission") != "WRITE":
            retomar_automatico = tarefa.get("continuacao")

    modos_habilitados = config_execucao.get("agent", {}).get(
        "enabled_modes", ["analyze", "suggest"],
    )
    if modo == "edit" and "edit" not in modos_habilitados:
        # Atualizacao 46: edit nao cai mais silenciosamente no pipeline antigo.
        # Desativar o modo e um bloqueio real e reversivel de escrita.
        texto = "O modo edit do Agente esta desativado em agent.enabled_modes; nenhuma escrita foi iniciada."
        fila_persistente.atualizar_tarefa_agente(
            task_id,
            status="blocked",
            causa_fallback="edit_mode_disabled",
            resultado={"task_id": task_id, "response": texto},
            evento={"tipo": "edit_mode_disabled"},
        )
        registrar_mensagem("assistant", texto)
        return {
            "resposta": texto,
            "roteador": {
                "tipo": "agente", "motivo": motivo_roteador, "modo": "edit",
                "rollout": rollout_efetivo, "task_id": task_id,
            },
            "iteracoes_analista": 0,
            "decisoes_analista": [],
            "confianca": None,
            "citation_validity": None,
            "coverage": None,
            "grounding": None,
            "avisos": [],
            "agente_status": "blocked",
            "agente_conclusao": {
                "task_type": "project_write", "mode": "edit", "task_id": task_id,
                "completion_gate": {"code": "edit_mode_disabled", "passed": False},
                "fallback_cause": "edit_mode_disabled",
            },
        }

    try:
        status, texto, estado_pendente, detalhes_agente = _desempacotar_resultado_agente(
            executar_agente(
                pergunta, config_execucao, entendimento=entendimento, projeto=projeto,
                retomar=retomar_automatico,
                retornar_detalhes=True, modo=modo, task_id=task_id,
                checkpoint=_persistir_checkpoint_agente,
            )
        )
    except ErroLLM as erro:
        codigo_erro = getattr(erro, "error_code", None) or "LLM_FAILURE"
        fallback = _fallback_leitura_legado(
            pergunta, config, projeto, entendimento, motivo_roteador, task_id,
            f"agent_llm_{str(codigo_erro).lower()}",
        )
        if fallback is not None:
            return fallback
        fila_persistente.atualizar_tarefa_agente(
            task_id,
            status="failed",
            causa_fallback="llm_failure",
            evento={"tipo": "llm_failure", "detail": str(erro)},
        )
        return _resultado_falha_llm(
            "agente", motivo_roteador, erro, agente_status="failed",
        )

    if (
        status == "failed"
        and detalhes_agente.get("fallback_cause") == "invalid_agent_json"
    ):
        fallback = _fallback_leitura_legado(
            pergunta, config, projeto, entendimento, motivo_roteador, task_id,
            "invalid_agent_json",
        )
        if fallback is not None:
            return fallback

    if causa_rollout:
        detalhes_agente["fallback_cause"] = causa_rollout
        fila_persistente.atualizar_tarefa_agente(
            task_id,
            causa_fallback=causa_rollout,
            resultado=detalhes_agente if status != "needs_user" else None,
            evento={"tipo": "rollout_fallback", "cause": causa_rollout},
        )

    if status == "needs_user" and estado_pendente:
        estado_pendente = salvar_agent_pendente(
            estado_pendente, projeto=projeto, config=config_execucao,
        )
        texto = estado_pendente["pergunta_ao_usuario"]

    salvar_texto_atomico(os.path.join(CONTEXT_DIR, "ultima_resposta.txt"), texto)

    arquivos_evidencia = [
        item.get("arquivo")
        for item in detalhes_agente.get("evidencias_usadas", [])
        if item.get("arquivo")
    ]
    if status == "success":
        resultado_validacao = validar_resposta(
            texto, MEMORY_DIR, arquivos_evidencia,
        )
        avisos = list(resultado_validacao.get("avisos", []))
    else:
        resultado_validacao = {
            "confianca": None,
            "citation_validity": None,
            "coverage": None,
            "grounding": None,
            "verificacao_aprovada": None,
            "avisos": [] if status == "needs_user" else [texto],
        }
        avisos = list(resultado_validacao["avisos"])

    registrar_historico(
        MEMORY_DIR, pergunta, arquivos_evidencia, resultado_validacao,
        resumo_decisao=(
            f"agente executado via engine/agent.py, status={status}, "
            f"task_type={detalhes_agente.get('task_type')}, "
            f"evidence_ids={detalhes_agente.get('evidence_ids', [])}"
        ),
    )
    registrar_mensagem("assistant", texto)

    return {
        "resposta": texto,
        "roteador": {
            "tipo": "agente", "motivo": motivo_roteador, "modo": modo,
            "rollout_configurado": rollout_configurado,
            "rollout": rollout_efetivo,
            "fallback_cause": causa_rollout,
            "task_id": task_id,
        },
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        **_campos_validacao(resultado_validacao),
        "agente_status": status,
        "agente_conclusao": detalhes_agente,
    }


def _retomar_agente_pendente(agente_pendente, config, resposta_usuario=None):
    """Fase 3/Atualizacao 39: usuario confirmou uma tool WRITE/EXEC que o
    Agente tinha pausado esperando aprovacao. Reidrata o AgentState a
    partir do checkpoint salvo em `agent_tasks`/JSON legado (via
    engine/agent.py:executar_agente(..., retomar=agente_pendente)) e
    retoma o loop do passo onde parou -- NAO recomeca a tarefa do zero,
    e NAO pede o objetivo de novo pro usuario (ja esta salvo no proprio
    agente_pendente)."""
    projeto = carregar_projeto()
    entendimento = carregar_entendimento() if projeto else {"componentes": {}}
    objetivo = agente_pendente.get("objetivo", "")
    nome_tool_confirmada = (agente_pendente.get("tool_pendente") or {}).get("tool", "?")
    task_id = agente_pendente.get("task_id")
    modo = agente_pendente.get("modo") or (
        (agente_pendente.get("estado") or {}).get("goal_state") or {}
    ).get("mode") or classificar_modo_projeto(objetivo)
    _, rollout_efetivo, causa_rollout = _rollout_agente_efetivo(
        config, projeto, execucao_explicita=False,
    )
    config_execucao = dict(config)
    config_execucao["agent"] = dict(config.get("agent", {}))
    config_execucao["agent"]["rollout_mode"] = rollout_efetivo
    if task_id and fila_persistente.obter_tarefa_agente(task_id) is None:
        fila_persistente.criar_tarefa_agente(
            objetivo, modo, projeto_hash=_hash_projeto(projeto), task_id=task_id,
        )

    try:
        status, texto, estado_pendente, detalhes_agente = _desempacotar_resultado_agente(
            executar_agente(
                objetivo, config_execucao, entendimento=entendimento, projeto=projeto,
                retomar=agente_pendente, retornar_detalhes=True, modo=modo,
                task_id=task_id, checkpoint=_persistir_checkpoint_agente,
                resposta_usuario=resposta_usuario,
            )
        )
    except ErroLLM as erro:
        if task_id:
            fila_persistente.atualizar_tarefa_agente(
                task_id,
                status="failed",
                causa_fallback="llm_failure_on_resume",
                evento={"tipo": "llm_failure_on_resume", "detail": str(erro)},
            )
        return _resultado_falha_llm(
            "agente_retomado", "falha da LLM ao retomar a tarefa pendente",
            erro, agente_status="failed",
        )

    if status == "needs_user" and estado_pendente:
        # o Agente encadeou outra tool confirmavel --
        # substitui a continuacao SQLite pelo novo passo pendente.
        estado_pendente = salvar_agent_pendente(
            estado_pendente, projeto=projeto, config=config_execucao,
        )
        texto = estado_pendente["pergunta_ao_usuario"]
    else:
        limpar_agent_pendente()

    if causa_rollout:
        detalhes_agente["fallback_cause"] = causa_rollout
        if task_id:
            fila_persistente.atualizar_tarefa_agente(
                task_id,
                causa_fallback=causa_rollout,
                resultado=detalhes_agente if status != "needs_user" else None,
                evento={"tipo": "rollout_fallback", "cause": causa_rollout},
            )

    salvar_texto_atomico(os.path.join(CONTEXT_DIR, "ultima_resposta.txt"), texto)

    arquivos_evidencia = [
        item.get("arquivo")
        for item in detalhes_agente.get("evidencias_usadas", [])
        if item.get("arquivo")
    ]
    if status == "success":
        resultado_validacao = validar_resposta(
            texto, MEMORY_DIR, arquivos_evidencia,
        )
        avisos = list(resultado_validacao.get("avisos", []))
    else:
        resultado_validacao = {
            "confianca": None,
            "citation_validity": None,
            "coverage": None,
            "grounding": None,
            "verificacao_aprovada": None,
            "avisos": [] if status == "needs_user" else [texto],
        }
        avisos = list(resultado_validacao["avisos"])

    registrar_historico(
        MEMORY_DIR, objetivo, arquivos_evidencia, resultado_validacao,
        resumo_decisao=(
            f"agente retomado apos confirmacao de tool {nome_tool_confirmada}, "
            f"status={status}, task_type={detalhes_agente.get('task_type')}, "
            f"evidence_ids={detalhes_agente.get('evidence_ids', [])}"
        ),
    )
    registrar_mensagem("assistant", texto)

    return {
        "resposta": texto,
        "roteador": {
            "tipo": "agente_retomado",
            "motivo": "usuario respondeu a continuacao persistida do agente",
            "modo": modo,
            "rollout": rollout_efetivo,
            "fallback_cause": causa_rollout,
            "task_id": task_id,
        },
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        **_campos_validacao(resultado_validacao),
        "agente_status": status,
        "agente_conclusao": detalhes_agente,
    }


def _cancelar_agente_pendente(agente_pendente):
    """Usuario recusou a tool WRITE/EXEC pendente do
    Agente -- descarta a tarefa inteira (nao ha como retomar so a parte
    de leitura sem a escrita que estava pendente), mesmo espirito de
    _cancelar_proposta_pendente."""
    task_id = agente_pendente.get("task_id")
    if task_id:
        fila_persistente.cancelar_tarefa_agente(
            task_id, motivo="cancelada pelo usuario",
        )
    limpar_agent_pendente()
    tool_pendente = agente_pendente.get("tool_pendente") or {}
    resposta = (
        f"Ok, cancelado. A tool '{tool_pendente.get('tool', '?')}' nao foi executada "
        "e a tarefa do agente foi descartada."
    )
    registrar_mensagem("assistant", resposta)
    return {
        "resposta": resposta,
        "roteador": {"tipo": "cancelar_agente", "motivo": "usuario cancelou a tool pendente do agente"},
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        "confianca": None,
        "avisos": [],
        "agente_status": "blocked",
        "agente_conclusao": {
            "task_id": task_id,
            "completion_gate": {"code": "cancelled", "passed": False},
            "fallback_cause": "cancelled",
        },
    }


def _aplicar_proposta_pendente(proposta, config):
    """Atualizacao 5: usuario confirmou ('sim'/'aplica') uma proposta que
    ja passou pelo teste em copia temporaria. Chama engine/codar.py:
    aplicar_patch, que re-confere o arquivo real antes de escrever (aborta
    se mudou desde a proposta), faz backup e roda uma segunda checagem."""
    projeto = carregar_projeto()
    caminho_projeto = (projeto or {}).get("caminho_origem")
    cfg_codar = config.get("codar", {})
    backups_dir = os.path.join(CONTEXT_DIR, "backups") if cfg_codar.get("fazer_backup", True) else None
    cfg_testes = cfg_codar.get("testes", {})

    resultado = aplicar_patch(
        caminho_projeto, proposta["arquivo"], proposta["linha_inicio"], proposta["linha_fim"],
        proposta["codigo_original"], proposta["codigo_novo"], backups_dir=backups_dir,
        cfg_testes=cfg_testes, cfg_retention=config.get("retention", {}),
    )
    limpar_proposta_pendente()

    if resultado["ok"]:
        resposta = f"Aplicado. {resultado['detalhe']}\n"
        if resultado.get("backup_path"):
            resposta += f"Backup do conteudo anterior salvo em: {resultado['backup_path']}\n"
        resposta += (
            "\nAtencao: o indice (estrutura.json/entendimento.json/chunks.jsonl) ainda reflete a versao "
            "anterior deste arquivo ate o proximo 'python main.py ingest'."
        )
        registrar_historico(
            MEMORY_DIR, proposta.get("pergunta_original", ""), [proposta["arquivo"]],
            {
                "confianca": None,
                "citation_validity": None,
                "coverage": None,
                "grounding": None,
                "avisos": [],
                "total_mencoes_verificadas": 0,
                "confirmadas": 0,
            },
            resumo_decisao=(
                f"patch aplicado em {proposta['arquivo']}:{proposta['linha_inicio']}-"
                f"{proposta['linha_fim']} (Atualizacao 5, confirmado pelo usuario)"
            ),
        )
    else:
        resposta = f"Nao apliquei. {resultado['detalhe']}"

    registrar_mensagem("assistant", resposta)
    return {
        "resposta": resposta,
        "roteador": {"tipo": "aplicar_proposta", "motivo": "usuario confirmou a proposta pendente"},
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        "confianca": None,
        "avisos": [] if resultado["ok"] else [resultado["detalhe"]],
    }


def _cancelar_proposta_pendente(proposta):
    """Atualizacao 5: usuario recusou ('nao'/'cancela') -- descarta a
    proposta sem tocar em nada."""
    limpar_proposta_pendente()
    resposta = f"Ok, cancelado. Nenhuma mudanca foi aplicada em '{proposta['arquivo']}'."
    registrar_mensagem("assistant", resposta)
    return {
        "resposta": resposta,
        "roteador": {"tipo": "cancelar_proposta", "motivo": "usuario cancelou a proposta pendente"},
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        "confianca": None,
        "avisos": [],
    }


def _identificar_alvo_unico(atual):
    """
    Atualizacao 5: so tenta gerar uma proposta de patch de verdade quando
    o retrieval final do ciclo_analista ja convergiu num UNICO alvo claro
    -- todos os trechos selecionados sao do MESMO arquivo E do MESMO
    simbolo (funcoes grandes demais viram varios sub-chunks, mas todos com
    o mesmo nome de simbolo, entao ainda contam como um alvo so).

    Conservador de proposito: qualquer ambiguidade (0 ou 2+ arquivos, 2+
    simbolos diferentes, chunk sem simbolo reconhecido/generico por
    tamanho) devolve None -- quem chama cai no fallback de sempre
    (Executor so explica em texto, nao propoe patch).

    Devolve (arquivo, simbolo) ou None.
    """
    trechos = atual.get("trechos", [])
    if not trechos:
        return None

    arquivos = {t["arquivo"] for t in trechos}
    if len(arquivos) != 1:
        return None

    simbolos = {t.get("simbolo") for t in trechos if t.get("simbolo")}
    if len(simbolos) != 1:
        return None

    return next(iter(arquivos)), next(iter(simbolos))


def _parse_resposta_engenheiro(texto):
    """Extrai o JSON de proposta do Engenheiro. Exige 'codigo_novo' nao
    vazio -- sem isso nao ha o que propor, e quem chama cai no fallback
    de texto livre em vez de gerar uma proposta incompleta/inventada."""
    match = _RE_JSON_BLOCO.search(texto or "")
    if not match:
        return None
    try:
        dados = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(dados, dict):
        return None

    codigo_novo = dados.get("codigo_novo")
    if not codigo_novo or not isinstance(codigo_novo, str) or not codigo_novo.strip():
        return None

    return {
        "resumo": dados.get("resumo") or "",
        "codigo_novo": codigo_novo,
        "riscos": dados.get("riscos") if isinstance(dados.get("riscos"), list) else [],
    }


def _tentar_gerar_proposta(pergunta, config, projeto, atual, entendimento, decisoes):
    """
    Atualizacao 5 -- o ciclo completo: Proposta (LLM Engenheiro) -> Impacto
    (depende_de invertido) -> Patch (recorte real por linha) -> Teste
    (copia temporaria). NUNCA aplica nada aqui -- so monta e devolve o
    resultado pronto pra virar a resposta; a aplicacao de verdade so
    acontece depois, numa mensagem seguinte, via _aplicar_proposta_pendente.

    Devolve o dict de resultado (pronto pra processar()/_processar_engenharia
    devolver direto) se conseguiu gerar uma proposta -- mesmo que o teste
    tenha falhado (ainda e informativo mostrar o que foi tentado, so nao
    fica pendente de confirmacao). Devolve None se NAO deu pra tentar
    (config desativado, alvo ambiguo, simbolo nao localizado no arquivo
    real, ou a LLM nao devolveu um JSON valido) -- nesses casos quem chama
    cai no fallback de sempre (Executor gera so texto).
    """
    def fallback(cause, detail=None):
        return {
            "_fallback": True,
            "fallback_used": True,
            "fallback_cause": cause,
            "fallback_detail": detail,
            "original_strategy": "structured_patch_proposal",
            "fallback_strategy": "verified_text_response",
        }

    cfg_codar = config.get("codar", {})
    if not cfg_codar.get("ativado", True):
        return fallback("CODING_DISABLED")

    alvo_identificado = _identificar_alvo_unico(atual)
    if alvo_identificado is None:
        return fallback("AMBIGUOUS_PATCH_TARGET")
    arquivo, simbolo = alvo_identificado

    caminho_projeto = (projeto or {}).get("caminho_origem")
    if not caminho_projeto:
        return fallback("PROJECT_PATH_UNAVAILABLE")

    alvo = localizar_simbolo(caminho_projeto, arquivo, simbolo)
    if alvo is None:
        return fallback(
            "SYMBOL_NOT_FOUND",
            f"{arquivo}:{simbolo} nao existe mais no arquivo real",
        )

    impacto = calcular_impacto(arquivo, entendimento)
    prompt_engenheiro = montar_prompt_engenheiro(
        pergunta, arquivo, simbolo, alvo, entendimento=entendimento,
        decisoes=decisoes, impacto=impacto,
    )
    resposta_bruta = executar_engenheiro(prompt_engenheiro, config)
    proposta_llm = _parse_resposta_engenheiro(resposta_bruta)
    if proposta_llm is None:
        return fallback("INVALID_ENGINEER_RESPONSE")

    teste = testar_patch_em_copia(
        caminho_projeto, arquivo, alvo["linha_inicio"], alvo["linha_fim"], proposta_llm["codigo_novo"],
    )

    proposta = {
        "pergunta_original": pergunta,
        "arquivo": arquivo,
        "simbolo": simbolo,
        "linha_inicio": alvo["linha_inicio"],
        "linha_fim": alvo["linha_fim"],
        "codigo_original": alvo["codigo_original"],
        "codigo_novo": proposta_llm["codigo_novo"],
        "resumo": proposta_llm["resumo"],
        "riscos": proposta_llm["riscos"],
        "impacto": impacto,
        "teste": teste,
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if teste.get("ok"):
        proposta = salvar_proposta_pendente(proposta, projeto=projeto, config=config)
        texto = montar_texto_proposta(proposta)
        texto += (
            f"\n\nID da pendencia: {proposta['id']}. "
            f"Para confirmar: confirmar {proposta['id']}"
        )
    else:
        # nao fica pendente -- nada aqui pode ser confirmado como esta
        limpar_proposta_pendente()
        texto = montar_texto_proposta(proposta)

    salvar_texto_atomico(os.path.join(CONTEXT_DIR, "ultima_resposta.txt"), texto)

    registrar_historico(
        MEMORY_DIR, pergunta, [arquivo],
        {
            "confianca": None,
            "citation_validity": None,
            "coverage": None,
            "grounding": None,
            "avisos": [] if teste.get("ok") else [teste.get("detalhe", "")],
        },
        resumo_decisao=f"proposta de patch gerada para {arquivo}:{simbolo} (Atualizacao 5)",
    )
    registrar_mensagem("assistant", texto)

    return {
        "resposta": texto,
        "roteador": {"tipo": "engenharia", "motivo": "proposta de patch gerada (Atualizacao 5)"},
        "confianca": None,
        "avisos": [] if teste.get("ok") else [teste.get("detalhe", "")],
    }


def _processar_engenharia_impl(pergunta, config, projeto, estrutura, entendimento, motivo_roteador):
    """Pipeline completo (o que a Eyle sempre fez): Retrieval -> Analista ->
    Executor -> Verify, com retry -- reservado para pedidos de mudanca real.

    Atualizacao 5 ('codar de verdade'): quando o ciclo Retrieval->Analista
    converge num UNICO (arquivo, simbolo) alvo claro, tenta ir alem de so
    explicar em texto -- gera uma PROPOSTA de patch de verdade (ver
    _tentar_gerar_proposta) e a devolve como resposta, aguardando
    confirmacao explicita numa mensagem seguinte. So cai no comportamento
    antigo (Executor livre, sem propor patch) quando o alvo nao e unico e
    claro, ou qualquer etapa da geracao da proposta falhar -- e sempre um
    "opt-in" silencioso, nunca um requisito pra responder."""
    if projeto is None:
        resposta = "Nenhum projeto indexado ainda. Rode: python main.py ingest /caminho/do/projeto"
        registrar_mensagem("assistant", resposta)
        return {"erro": resposta, "roteador": {"tipo": "engenharia", "motivo": motivo_roteador}}

    evidencias_dados = carregar_evidencias()
    evidencias = evidencias_dados.get("entidades", [])
    decisoes = carregar_decisoes()

    atual, decisoes_analista = ciclo_analista(pergunta, config, estrutura, evidencias, entendimento)

    tem_entendimento = any(
        item.get("funcao") for item in entendimento.get("componentes", {}).values()
    )

    if not atual.get("trechos") and not tem_entendimento:
        resposta = "Nao encontrei trechos relevantes na memoria indexada para essa pergunta."
        registrar_mensagem("assistant", resposta)
        return {
            "resposta": resposta,
            "roteador": {"tipo": "engenharia", "motivo": motivo_roteador},
            "iteracoes_analista": len(decisoes_analista),
            "decisoes_analista": decisoes_analista,
            "confianca": None,
            "avisos": [],
        }

    resultado_proposta = _tentar_gerar_proposta(pergunta, config, projeto, atual, entendimento, decisoes)
    fallback_proposta = None
    if resultado_proposta is not None and not resultado_proposta.get("_fallback"):
        resultado_proposta["iteracoes_analista"] = len(decisoes_analista)
        resultado_proposta["decisoes_analista"] = decisoes_analista
        return resultado_proposta
    if isinstance(resultado_proposta, dict):
        fallback_proposta = resultado_proposta

    prompt_executor = montar_prompt_executor(
        atual, projeto=projeto, evidencias=evidencias,
        entendimento=entendimento, decisoes=decisoes,
    )

    max_tentativas = config.get("engine", {}).get("max_tentativas_executor", 2)
    resposta = ""
    resultado_validacao = None
    assinatura_avisos_anterior = None

    for tentativa in range(1, max_tentativas + 1):
        resposta = executar_executor(prompt_executor, config)

        salvar_texto_atomico(os.path.join(CONTEXT_DIR, "ultima_resposta.txt"), resposta)

        resultado_validacao = validar_resposta(resposta, MEMORY_DIR, atual.get("arquivos_relevantes"))

        if resultado_validacao["verificacao_aprovada"] is not False or tentativa == max_tentativas:
            break

        assinatura_avisos = tuple(sorted(str(item) for item in resultado_validacao.get("avisos", [])))
        if assinatura_avisos and assinatura_avisos == assinatura_avisos_anterior:
            resultado_validacao.setdefault("avisos", []).append(
                "Retry interrompido: o Verify repetiu exatamente os mesmos avisos sem progresso."
            )
            break
        assinatura_avisos_anterior = assinatura_avisos

        # reprovado: Executor tenta de novo, agora sabendo o que falhou na verificacao
        prompt_executor += (
            "\n\nSUA RESPOSTA ANTERIOR TEVE PROBLEMAS DE VERIFICACAO, CORRIJA:\n- "
            + "\n- ".join(resultado_validacao["avisos"])
        )
        _esperar_retry_executor(config, tentativa)

    registrar_historico(
        MEMORY_DIR, pergunta, atual.get("arquivos_relevantes", []), resultado_validacao,
        resumo_decisao="; ".join(d.get("motivo", "") for d in decisoes_analista if d.get("motivo")),
    )

    registrar_mensagem("assistant", resposta)

    retorno = {
        "resposta": resposta,
        "roteador": {"tipo": "engenharia", "motivo": motivo_roteador},
        "iteracoes_analista": len(decisoes_analista),
        "decisoes_analista": decisoes_analista,
        **_campos_validacao(resultado_validacao),
    }
    if fallback_proposta:
        retorno.update({
            "fallback_used": True,
            "fallback_cause": fallback_proposta.get("fallback_cause"),
            "fallback_detail": fallback_proposta.get("fallback_detail"),
            "original_strategy": fallback_proposta.get("original_strategy"),
            "fallback_strategy": fallback_proposta.get("fallback_strategy"),
        })
    return retorno


def _processar_engenharia(pergunta, config, projeto, estrutura, entendimento, motivo_roteador):
    """Fronteira do pipeline completo para o contrato de erro da LLM."""
    try:
        return _processar_engenharia_impl(
            pergunta, config, projeto, estrutura, entendimento, motivo_roteador,
        )
    except ErroLLM as erro:
        return _resultado_falha_llm("engenharia", motivo_roteador, erro)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Uso: python engine/engine.py "sua pergunta"')
        sys.exit(1)
    resultado = processar(sys.argv[1])
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
