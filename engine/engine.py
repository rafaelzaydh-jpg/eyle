#!/usr/bin/env python3
"""Nucleo unificado da Eyle 2.7.4.

Existe somente um caminho publico para tarefas de projeto: engine.agent.
Conversa livre continua separada porque nao precisa de workspace nem tools.
Os pipelines historicos Retrieval/Analista/Executor/Verify foram removidos.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.agent import executar_agente
from engine.config_schema import carregar_config_validada
from engine.memoria_lock import lock_para
from engine.persistencia import salvar_json_atomico, salvar_texto_atomico
from engine.roteador import classificar_pergunta, classificar_modo_projeto, detectar_resposta_proposta
from engine import queue as fila_persistente
from engine import progress as job_progress
from llm.executar import ErroLLM, executar_chat

MEMORY_DIR = os.path.join(BASE_DIR, "memory")
CONTEXT_DIR = os.path.join(BASE_DIR, "context")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
AGENT_PENDENTE_PATH = os.path.join(CONTEXT_DIR, "agent_pendente.json")
_TTL_PENDENCIA_DEFAULT = 3600

_JOB_ATUAL_ID = contextvars.ContextVar("eyle_source_job_id", default=None)
_MENSAGEM_ORIGEM_ATUAL_ID = contextvars.ContextVar("eyle_source_message_id", default=None)


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
    """2.7.4: o agente e o unico pipeline; off nao possui mais fallback legado."""
    modo = ((config or {}).get("agent") or {}).get("rollout_mode", "full")
    return modo if modo in {"read_only", "full"} else "full"

def _rollout_agente_efetivo(config, projeto, execucao_explicita=False):
    configurado = _rollout_agente_configurado(config)
    return configurado, configurado, None

def carregar_projeto():
    return _carregar_json(os.path.join(MEMORY_DIR, "projeto.json"), None)

def carregar_estrutura():
    return _carregar_json(os.path.join(MEMORY_DIR, "estrutura.json"), {}).get("arquivos", {})

def carregar_entendimento():
    """memory/entendimento.json responde 'para que serve', separado de
    estrutura.json ('o que existe') e evidencias.json ('como se sabe')."""
    return _carregar_json(
        os.path.join(MEMORY_DIR, "entendimento.json"),
        {"version": "1.0", "updated": None, "componentes": {}},
    )

def carregar_conversa():
    return _carregar_json(os.path.join(MEMORY_DIR, "conversa.json"), [])

def salvar_conversa(mensagens):
    _salvar_json(os.path.join(MEMORY_DIR, "conversa.json"), mensagens)

def registrar_mensagem_com_snapshot(role, texto, limite_snapshot=6, metadata=None):
    """Registra uma mensagem e captura, sob o mesmo lock, o historico do job.

    O snapshot atomico impede que outra requisicao web inclua uma mensagem
    posterior entre a gravacao da mensagem atual e a captura do contexto.
    """
    caminho = os.path.join(MEMORY_DIR, "conversa.json")
    with lock_para(caminho):
        mensagens = carregar_conversa()
        novo_id = (max((m["id"] for m in mensagens), default=0)) + 1
        mensagem = {
            "id": novo_id,
            "role": role,
            "text": texto,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if role == "assistant":
            source_job_id = _JOB_ATUAL_ID.get()
            source_message_id = _MENSAGEM_ORIGEM_ATUAL_ID.get()
            if source_job_id is not None:
                mensagem["source_job_id"] = int(source_job_id)
            if source_message_id is not None:
                mensagem["reply_to_message_id"] = int(source_message_id)
        if isinstance(metadata, dict):
            for chave, valor in metadata.items():
                if chave not in {"id", "role", "text", "timestamp"}:
                    mensagem[chave] = valor
        mensagens.append(mensagem)
        salvar_conversa(mensagens)
        historico = _historico_sem_erros_llm(mensagens)
        if limite_snapshot is not None:
            limite_snapshot = max(0, int(limite_snapshot))
            historico = historico[-limite_snapshot:] if limite_snapshot else []
        return novo_id, [dict(mensagem) for mensagem in historico]

def registrar_mensagem(role, texto, metadata=None):
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
    novo_id, _ = registrar_mensagem_com_snapshot(role, texto, metadata=metadata)
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
        mensagem = {
            "id": novo_id,
            "role": role,
            "text": texto,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if role == "assistant":
            source_job_id = _JOB_ATUAL_ID.get()
            source_message_id = _MENSAGEM_ORIGEM_ATUAL_ID.get()
            if source_job_id is not None:
                mensagem["source_job_id"] = int(source_job_id)
            if source_message_id is not None:
                mensagem["reply_to_message_id"] = int(source_message_id)
        mensagens.append(mensagem)
        salvar_conversa(mensagens)
        return novo_id

def remover_mensagem(mensagem_id):
    """Remove uma mensagem de memory/conversa.json pelo id."""
    caminho = os.path.join(MEMORY_DIR, "conversa.json")
    with lock_para(caminho):
        mensagens = carregar_conversa()
        restantes = [m for m in mensagens if m.get("id") != mensagem_id]
        removeu = len(restantes) != len(mensagens)
        if removeu:
            salvar_conversa(restantes)
        return removeu

def remover_respostas_do_job(job_id):
    """Apaga respostas que um job cancelado conseguiu gravar numa corrida final."""
    caminho = os.path.join(MEMORY_DIR, "conversa.json")
    with lock_para(caminho):
        mensagens = carregar_conversa()
        restantes = [
            mensagem for mensagem in mensagens
            if mensagem.get("source_job_id") != int(job_id)
        ]
        removeu = len(restantes) != len(mensagens)
        if removeu:
            salvar_conversa(restantes)
        return removeu

def _marcar_remocao_pendente(mensagem_id, job_ids):
    caminho = os.path.join(MEMORY_DIR, "conversa.json")
    job_ids = sorted({int(job_id) for job_id in job_ids})
    with lock_para(caminho):
        mensagens = carregar_conversa()
        encontrou = False
        for mensagem in mensagens:
            if mensagem.get("id") != int(mensagem_id):
                continue
            encontrou = True
            mensagem["pending_delete"] = True
            anteriores = mensagem.get("delete_after_jobs") or []
            mensagem["delete_after_jobs"] = sorted({
                int(job_id) for job_id in [*anteriores, *job_ids]
            })
            mensagem.setdefault(
                "delete_requested_at", time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            break
        if encontrou:
            salvar_conversa(mensagens)
        return encontrou

def finalizar_remocoes_pendentes():
    """Remove mensagens quando todos os jobs que congelaram seu contexto terminam."""
    caminho = os.path.join(MEMORY_DIR, "conversa.json")
    with lock_para(caminho):
        mensagens = carregar_conversa()
        restantes = []
        removidos = []
        for mensagem in mensagens:
            if not mensagem.get("pending_delete"):
                restantes.append(mensagem)
                continue
            bloqueadores = mensagem.get("delete_after_jobs") or []
            ainda_ativos = []
            for job_id in bloqueadores:
                registro = fila_persistente.obter(job_id)
                if registro and registro.get("status") in ("pending", "processing"):
                    ainda_ativos.append(int(job_id))
            if ainda_ativos:
                mensagem["delete_after_jobs"] = ainda_ativos
                restantes.append(mensagem)
            else:
                removidos.append(mensagem.get("id"))
        if removidos or restantes != mensagens:
            salvar_conversa(restantes)
        return [mensagem_id for mensagem_id in removidos if mensagem_id is not None]

def solicitar_remocao_mensagem(mensagem_id):
    """Aplica as regras de exclusao sem contaminar ou abortar o job errado.

    A mensagem e marcada antes de consultar a fila. Assim, snapshots novos ja
    deixam de usa-la. Jobs que ja possuem uma copia congelada continuam ate o
    fim; o job originado pela propria mensagem recebe cancelamento imediato.
    """
    mensagem_id = int(mensagem_id)
    caminho = os.path.join(MEMORY_DIR, "conversa.json")
    with lock_para(caminho):
        mensagens = carregar_conversa()
        alvo = next((m for m in mensagens if m.get("id") == mensagem_id), None)
        if alvo is None:
            return {
                "status": "not_found", "mensagem_id": mensagem_id,
                "removed": False, "cancelled_jobs": [], "waiting_jobs": [],
            }
        alvo["pending_delete"] = True
        alvo.setdefault("delete_after_jobs", [])
        alvo.setdefault("delete_requested_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        salvar_conversa(mensagens)

    cancelamentos = fila_persistente.cancelar_jobs_da_mensagem(
        mensagem_id, motivo="mensagem de origem removida pelo usuario",
    )
    cancelados = [
        int(item["job_id"]) for item in cancelamentos
        if item.get("changed") or item.get("status") == "cancelled"
    ]
    for job_id in cancelados:
        fila_persistente.cancelar_tarefas_agente_por_job(
            job_id, motivo="mensagem de origem removida pelo usuario",
        )
        remover_respostas_do_job(job_id)

    bloqueadores = fila_persistente.jobs_ativos_usando_mensagem(
        mensagem_id, excluir_job_ids=cancelados,
    )
    aguardando = [int(item["id"]) for item in bloqueadores]
    if aguardando:
        _marcar_remocao_pendente(mensagem_id, aguardando)
    else:
        remover_mensagem(mensagem_id)

    finalizar_remocoes_pendentes()
    ainda_existe = any(
        mensagem.get("id") == mensagem_id for mensagem in carregar_conversa()
    )
    return {
        "status": "deferred" if ainda_existe else "removed",
        "mensagem_id": mensagem_id,
        "removed": not ainda_existe,
        "cancelled_jobs": cancelados,
        "waiting_jobs": aguardando,
    }

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

def _preparar_pendencia(dados, tipo, projeto, config=None):
    dados = dict(dados or {})
    cfg = (config or {}).get("confirmacoes", {})
    try:
        ttl = max(1, int(cfg.get("expiracao_segundos", _TTL_PENDENCIA_DEFAULT)))
    except (TypeError, ValueError):
        ttl = _TTL_PENDENCIA_DEFAULT

    agora = _agora_utc()
    projeto_hash = _hash_projeto(projeto)
    id_existente = str(dados.get("id") or "").upper()
    expiracao_existente = _parse_data_utc(dados.get("expira_em"))
    reutilizar = (
        re.fullmatch(r"[0-9A-F]{4}", id_existente) is not None
        and dados.get("tipo_pendencia") == tipo
        and dados.get("projeto_hash") == projeto_hash
        and expiracao_existente is not None
        and agora < expiracao_existente
    )

    if reutilizar:
        # A mesma tarefa retomada conserva a identidade da pendencia. Isso
        # evita IDs em cascata quando existe uma segunda pergunta humana real.
        dados["id"] = id_existente
        return dados

    dados.update({
        "id": _novo_id_pendencia(),
        "tipo_pendencia": tipo,
        "criado_em": _formatar_data_utc(agora),
        "expira_em": _formatar_data_utc(agora + timedelta(seconds=ttl)),
        "projeto_hash": projeto_hash,
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
    return [
        m for m in mensagens
        if not (m.get("text") or "").startswith("[erro]")
        and not m.get("pending_delete")
    ]

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
    """Conversa geral sem abrir o workspace nem executar ferramentas."""
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

def _novo_id_pendencia():
    existentes = set()
    atual = _carregar_json(AGENT_PENDENTE_PATH, None)
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


def _pendencias_existentes():
    pendencias = []
    agente = carregar_agent_pendente()
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


def _limpar_pendencia_agente(dados=None, motivo="pendencia expirada ou invalida"):
    task_id = (dados or {}).get("task_id")
    if task_id:
        fila_persistente.cancelar_tarefa_agente(task_id, motivo=motivo)
    limpar_agent_pendente()


def _campos_publicos_agente(detalhes):
    detalhes = detalhes if isinstance(detalhes, dict) else {}
    grounding = detalhes.get("semantic_grounding") or detalhes.get("grounding") or {}
    coverage = detalhes.get("coverage") or detalhes.get("analysis_coverage")
    return {
        "confianca": detalhes.get("confidence"),
        "citation_validity": grounding.get("ok") if isinstance(grounding, dict) else None,
        "coverage": coverage,
        "grounding": grounding.get("ok") if isinstance(grounding, dict) else None,
        "verificacao_aprovada": (
            (detalhes.get("completion_gate") or {}).get("passed")
            if isinstance(detalhes.get("completion_gate"), dict) else None
        ),
        "avisos": list(detalhes.get("limitacoes") or []),
    }


def _resultado_agente(status, texto, detalhes, motivo, modo, rollout, task_id):
    return {
        "status": "failed" if status == "failed" else status,
        "resposta": texto,
        "roteador": {
            "tipo": "agente",
            "motivo": motivo,
            "modo": modo,
            "rollout": rollout,
            "task_id": task_id,
        },
        "iteracoes_analista": 0,
        "decisoes_analista": [],
        **_campos_publicos_agente(detalhes),
        "agente_status": status,
        "agente_conclusao": detalhes,
    }


def _processar_agente(pergunta, config, projeto, entendimento, motivo_roteador,
                      execucao_explicita=False, task_id=None, source_job_id=None):
    modo = classificar_modo_projeto(pergunta)
    rollout_configurado, rollout_efetivo, _ = _rollout_agente_efetivo(
        config, projeto, execucao_explicita=execucao_explicita,
    )
    config_execucao = dict(config)
    config_execucao["agent"] = dict(config.get("agent", {}))
    config_execucao["agent"]["rollout_mode"] = rollout_efetivo

    job_progress.publicar(config_execucao, "agent", "Eyle iniciou a tarefa")
    try:
        tarefa = fila_persistente.criar_tarefa_agente(
            pergunta,
            modo,
            projeto_hash=_hash_projeto(projeto),
            task_id=task_id,
            source_job_id=source_job_id,
        )
    except fila_persistente.AgentTaskContextMismatch as erro:
        detalhes = {
            "task_id": task_id,
            "task_type": "project_write" if modo == "edit" else "project_read",
            "mode": modo,
            "failure_code": "REQUEST_CONTEXT_MISMATCH",
            "completion_gate": {"code": "REQUEST_CONTEXT_MISMATCH", "passed": False},
            "evidence_ids": [],
            "evidencias_usadas": [],
            "limitacoes": [str(erro)],
        }
        return _resultado_agente(
            "failed",
            "A tarefa persistida pertence a outro pedido e foi recusada para evitar mistura de contexto.",
            detalhes, motivo_roteador, modo, rollout_efetivo, task_id,
        )

    task_id = tarefa["task_id"]
    if tarefa.get("status") == "completed" and isinstance(tarefa.get("resultado"), dict):
        detalhes = tarefa["resultado"]
        texto = detalhes.get("response") or ""
        if texto.strip():
            registrar_mensagem_se_nova("assistant", texto)
            return _resultado_agente(
                "success", texto, detalhes, "resultado idempotente persistido",
                modo, rollout_efetivo, task_id,
            )

    if tarefa.get("status") == "waiting_user" and tarefa.get("continuacao"):
        texto = tarefa.get("pergunta") or "A tarefa ainda aguarda sua resposta."
        registrar_mensagem_se_nova("assistant", texto)
        detalhes = tarefa.get("resultado") or {
            "task_id": task_id,
            "completion_gate": {"code": "user_input_required", "passed": False},
        }
        return _resultado_agente(
            "needs_user", texto, detalhes, "tarefa aguardando usuario",
            modo, rollout_efetivo, task_id,
        )

    retomar_automatico = None
    if tarefa.get("status") == "running" and tarefa.get("continuacao"):
        acao = tarefa.get("acao_pendente") or {}
        if acao.get("permission") != "WRITE":
            retomar_automatico = tarefa.get("continuacao")

    try:
        status, texto, estado_pendente, detalhes = _desempacotar_resultado_agente(
            executar_agente(
                pergunta,
                config_execucao,
                entendimento=entendimento,
                projeto=projeto,
                retomar=retomar_automatico,
                retornar_detalhes=True,
                modo=modo,
                task_id=task_id,
                checkpoint=_persistir_checkpoint_agente,
            )
        )
    except ErroLLM as erro:
        fila_persistente.atualizar_tarefa_agente(
            task_id,
            status="failed",
            causa_fallback="llm_failure",
            evento={"tipo": "llm_failure", "detail": str(erro)},
        )
        return _resultado_falha_llm(
            "agente", motivo_roteador, erro, agente_status="failed",
        )

    if status == "needs_user" and estado_pendente:
        estado_pendente = salvar_agent_pendente(
            estado_pendente, projeto=projeto, config=config_execucao,
        )
        texto = estado_pendente["pergunta_ao_usuario"]

    job_progress.publicar(
        config_execucao,
        "finalizing",
        "Montando a resposta final",
        partial_text=texto[-16000:] if isinstance(texto, str) else None,
    )
    salvar_texto_atomico(os.path.join(CONTEXT_DIR, "ultima_resposta.txt"), texto)
    registrar_mensagem("assistant", texto)
    return _resultado_agente(
        status, texto, detalhes, motivo_roteador, modo, rollout_efetivo, task_id,
    )


def _retomar_agente_pendente(agente_pendente, config, resposta_usuario=None):
    projeto = carregar_projeto()
    entendimento = carregar_entendimento() if projeto else {"componentes": {}}
    objetivo = agente_pendente.get("objetivo", "")
    task_id = agente_pendente.get("task_id")
    modo = agente_pendente.get("modo") or (
        (agente_pendente.get("estado") or {}).get("goal_state") or {}
    ).get("mode") or classificar_modo_projeto(objetivo)
    _, rollout, _ = _rollout_agente_efetivo(config, projeto)
    config_execucao = dict(config)
    config_execucao["agent"] = dict(config.get("agent", {}))
    config_execucao["agent"]["rollout_mode"] = rollout

    try:
        status, texto, estado_pendente, detalhes = _desempacotar_resultado_agente(
            executar_agente(
                objetivo,
                config_execucao,
                entendimento=entendimento,
                projeto=projeto,
                retomar=agente_pendente,
                retornar_detalhes=True,
                modo=modo,
                task_id=task_id,
                checkpoint=_persistir_checkpoint_agente,
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
            "agente", "falha da LLM ao retomar a tarefa", erro,
            agente_status="failed",
        )

    if status == "needs_user" and estado_pendente:
        estado_pendente = salvar_agent_pendente(
            estado_pendente, projeto=projeto, config=config_execucao,
        )
        texto = estado_pendente["pergunta_ao_usuario"]
    else:
        limpar_agent_pendente()

    salvar_texto_atomico(os.path.join(CONTEXT_DIR, "ultima_resposta.txt"), texto)
    registrar_mensagem("assistant", texto)
    return _resultado_agente(
        status, texto, detalhes, "tarefa retomada", modo, rollout, task_id,
    )


def _cancelar_agente_pendente(agente_pendente):
    task_id = agente_pendente.get("task_id")
    if task_id:
        fila_persistente.cancelar_tarefa_agente(task_id, motivo="cancelada pelo usuario")
    limpar_agent_pendente()
    tool = (agente_pendente.get("tool_pendente") or {}).get("tool", "?")
    resposta = f"Ok, cancelado. A ferramenta '{tool}' nao foi executada."
    registrar_mensagem("assistant", resposta)
    detalhes = {
        "task_id": task_id,
        "completion_gate": {"code": "cancelled", "passed": False},
        "fallback_cause": "cancelled",
    }
    return _resultado_agente(
        "blocked", resposta, detalhes, "usuario cancelou a tarefa",
        agente_pendente.get("modo") or "edit", "full", task_id,
    )


def processar(pergunta, registrar_pergunta=True, forcar_tipo=None, historico_snapshot=None,
              task_id=None, source_job_id=None, source_message_id=None):
    """Ponto de entrada unico da Eyle 2.7.4.

    Somente dois caminhos existem: chat geral ou agente de projeto. Nenhuma
    falha do agente e publicada como falha; nao existe pipeline alternativo.
    """
    _JOB_ATUAL_ID.set(source_job_id)
    _MENSAGEM_ORIGEM_ATUAL_ID.set(source_message_id)
    config = dict(carregar_config() or {})
    cfg_agent = config.get("agent", {})
    deadline = max(1, int(cfg_agent.get("task_deadline_seconds", 900)))
    agora = time.monotonic()
    config["_runtime_agent_budget"] = {
        "started_monotonic": agora,
        "deadline_monotonic": agora + deadline,
        "task_id": task_id,
        "source_job_id": source_job_id,
        "max_llm_calls": max(1, int(cfg_agent.get("max_llm_calls", 12))),
        "max_generated_tokens": max(1, int(cfg_agent.get("max_total_generated_tokens", 12000))),
        "llm_calls": 0,
        "generated_tokens": 0,
    }
    projeto = carregar_projeto()

    if registrar_pergunta:
        registrar_mensagem("user", pergunta)

    if forcar_tipo is None:
        pendencias = _pendencias_existentes()
        decisao = detectar_resposta_proposta(pergunta) if pendencias else None
        if decisao:
            selecionada, erro = _selecionar_pendencia(pergunta, pendencias)
            if erro:
                return _resultado_controle_pendencia(erro)
            dados = selecionada["dados"]
            valida, motivo = _validar_pendencia(dados, projeto)
            if not valida:
                _limpar_pendencia_agente(dados)
                return _resultado_controle_pendencia(
                    f"A pendencia {dados.get('id', 'SEM-ID')} foi descartada: {motivo}."
                )
            if decisao == "aplicar":
                return _retomar_agente_pendente(dados, config, resposta_usuario=pergunta)
            return _cancelar_agente_pendente(dados)

        continuacoes = [
            item for item in pendencias
            if item["dados"].get("continuation_kind") == "user_input"
        ]
        if len(continuacoes) == 1:
            return _retomar_agente_pendente(
                continuacoes[0]["dados"], config, resposta_usuario=pergunta,
            )

    estrutura = carregar_estrutura() if projeto else {}
    entendimento = carregar_entendimento() if projeto else {"componentes": {}}
    if forcar_tipo == "chat":
        tipo, motivo = "chat", "tipo forcado"
    elif forcar_tipo == "agente":
        tipo, motivo = "agente", "tipo forcado"
    else:
        tipo, motivo = classificar_pergunta(
            pergunta, estrutura, entendimento, agent_habilitado=True,
        )

    if tipo == "chat":
        return _processar_chat(
            pergunta, config, motivo, historico_snapshot=historico_snapshot,
        )

    if projeto is None:
        resposta = (
            "Nenhum projeto foi carregado. Coloque o projeto em workspace/ e rode "
            "python main.py ingest workspace antes de pedir analise ou edicao."
        )
        registrar_mensagem("assistant", resposta)
        return {
            "status": "failed",
            "error_code": "PROJECT_NOT_INDEXED",
            "resposta": resposta,
            "roteador": {"tipo": "agente", "motivo": motivo},
            "iteracoes_analista": 0,
            "decisoes_analista": [],
            "confianca": None,
            "avisos": [resposta],
            "agente_status": "failed",
        }

    return _processar_agente(
        pergunta,
        config,
        projeto,
        entendimento,
        motivo,
        execucao_explicita=forcar_tipo == "agente",
        task_id=task_id,
        source_job_id=source_job_id,
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit('Uso: python engine/engine.py "sua pergunta"')
    print(json.dumps(processar(sys.argv[1]), ensure_ascii=False, indent=2))
