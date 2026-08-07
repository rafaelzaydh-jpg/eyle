#!/usr/bin/env python3
"""Nucleo unificado da Eyle 2.7.4.

Existe somente um caminho público para tarefas de projeto: eyle.core.agent.
Toda mensagem não operacional entra na mesma AgentSession.
Os pipelines historicos Retrieval/Analista/Executor/Verify foram removidos.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eyle.core.agent import executar_agente
from eyle.core.workspace import discover_project
from eyle.runtime.config import carregar_config_validada
from eyle.runtime.lock import lock_para
from eyle.runtime.persistence import salvar_json_atomico
from eyle.runtime import queue as fila_persistente
from eyle.runtime import progress as job_progress
from llm.executar import ErroLLM

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

def carregar_projeto():
    return discover_project(BASE_DIR)

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
    mensagem) quanto pela thread do Worker (eyle/runtime/worker.py, quando grava
    a resposta do assistente) -- as duas rodam no MESMO processo ao mesmo
    tempo, por design (agente persistente). Sem lock, ler+somar+gravar
    conversa.json nao e' atomico entre as duas threads: da pra perder uma
    mensagem (lost update) ou gerar o mesmo id duas vezes. O lock cobre a
    operacao inteira (ler, calcular novo_id, gravar), nao so a escrita.
    """
    novo_id, _ = registrar_mensagem_com_snapshot(role, texto, metadata=metadata)
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
    return valor.astimezone(timezone.utc).isoformat()


def _parse_data_utc(valor):
    try:
        parsed = datetime.fromisoformat(str(valor))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _hash_projeto(projeto):
    caminho = (projeto or {}).get("caminho_origem")
    if not caminho:
        return None
    return hashlib.sha256(os.path.realpath(caminho).encode("utf-8")).hexdigest()


def _novo_id_pendencia():
    atual = _carregar_json(AGENT_PENDENTE_PATH, None)
    existente = str((atual or {}).get("id") or "").upper()
    while True:
        candidato = secrets.token_hex(2).upper()
        if candidato != existente:
            return candidato


def _preparar_pendencia(dados, projeto, config=None):
    dados = dict(dados or {})
    cfg = (config or {}).get("confirmacoes") or {}
    ttl = max(60, int(cfg.get("expiracao_segundos", _TTL_PENDENCIA_DEFAULT)))
    agora = _agora_utc()
    expiracao = _parse_data_utc(dados.get("expira_em"))
    projeto_hash = _hash_projeto(projeto)
    reutilizar = (
        re.fullmatch(r"[0-9A-F]{4}", str(dados.get("id") or "").upper()) is not None
        and dados.get("projeto_hash") == projeto_hash
        and expiracao is not None and agora < expiracao
    )
    if not reutilizar:
        dados["id"] = _novo_id_pendencia()
        dados["criado_em"] = _formatar_data_utc(agora)
        dados["expira_em"] = _formatar_data_utc(agora + timedelta(seconds=ttl))
        dados["projeto_hash"] = projeto_hash
    return dados


def _validar_pendencia(pendencia, projeto, agora=None):
    pendencia = pendencia or {}
    if any(not pendencia.get(campo) for campo in ("id", "expira_em", "projeto_hash")):
        return False, "não possui metadados de segurança completos"
    expiracao = _parse_data_utc(pendencia.get("expira_em"))
    if expiracao is None or (agora or _agora_utc()) >= expiracao:
        return False, "expirou"
    if pendencia.get("projeto_hash") != _hash_projeto(projeto):
        return False, "pertence a outro projeto"
    return True, None


def carregar_agent_pendente():
    return _carregar_json(AGENT_PENDENTE_PATH, None)


def salvar_agent_pendente(estado_pendente, projeto=None, config=None):
    dados = _preparar_pendencia(estado_pendente, projeto or carregar_projeto(), config)
    pergunta = str(dados.get("pergunta_ao_usuario") or "Como deseja continuar?").rstrip()
    confirmavel = (dados.get("tool_pendente") or {}).get("tool") not in (None, "__user_response__")
    instrucao = (
        f"Para confirmar: confirmar {dados['id']}"
        if confirmavel else
        f"Responda normalmente para retomar; para cancelar: cancelar {dados['id']}"
    )
    if f"ID da pendência: {dados['id']}" not in pergunta and f"ID da pendencia: {dados['id']}" not in pergunta:
        pergunta = f"{pergunta}\nID da pendência: {dados['id']}. {instrucao}"
    dados["pergunta_ao_usuario"] = pergunta
    _salvar_json(AGENT_PENDENTE_PATH, dados)
    return dados


def limpar_agent_pendente():
    if os.path.exists(AGENT_PENDENTE_PATH):
        os.remove(AGENT_PENDENTE_PATH)


_CONFIRM_CONTROL = re.compile(
    r"^\s*(?:sim|confirmar|confirme|confirmo|aplicar|aplique)"
    r"(?:\s+[0-9A-Fa-f]{4})?\s*[.!]?\s*$",
    re.IGNORECASE,
)
_CANCEL_CONTROL = re.compile(
    r"^\s*(?:não|nao|cancelar|cancele|cancela)"
    r"(?:\s+[0-9A-Fa-f]{4})?\s*[.!]?\s*$",
    re.IGNORECASE,
)
_EXPLICIT_CONTROL = re.compile(
    r"^\s*(?:(?:sim|não|nao|confirmar|confirme|confirmo|aplicar|aplique|cancelar|cancele|cancela)"
    r"(?:\s+[0-9A-Fa-f]{4})?)\s*[.!]?\s*$",
    re.IGNORECASE,
)


def _controle_pendencia(pergunta):
    text = str(pergunta or "")
    if _CANCEL_CONTROL.fullmatch(text):
        return "cancelar"
    if _CONFIRM_CONTROL.fullmatch(text):
        return "aplicar"
    return None


def _selecionar_pendencia(pergunta, pendencia):
    if not pendencia:
        return None, "Não existe alteração aguardando confirmação."
    codigo = str(pendencia.get("id") or "").upper()
    referencias = re.findall(r"\b[0-9A-Fa-f]{4}\b", pergunta or "")
    if referencias and codigo not in [item.upper() for item in referencias]:
        return None, f"Não existe pendência ativa com o ID {referencias[0].upper()}."
    return pendencia, None


def _resultado_controle_pendencia(resposta):
    registrar_mensagem("assistant", resposta)
    return {
        "resposta": resposta,
        "avisos": [resposta],
    }


def _historico_sem_erros_llm(mensagens):
    return [
        item for item in mensagens
        if not str(item.get("text") or "").startswith("[erro]")
        and not item.get("pending_delete")
    ]


def _historico_sem_mensagem_atual(mensagens, pergunta):
    historico = list(mensagens or [])
    if historico and historico[-1].get("role") == "user" and historico[-1].get("text") == pergunta:
        return historico[:-1]
    return historico


def _resultado_falha_llm(erro, **extras):
    detalhe = str(erro)
    resultado = {
        "status": "failed",
        "error_code": getattr(erro, "error_code", None) or "LLM_FAILURE",
        "transient": bool(getattr(erro, "transient", False)),
        "http_status": getattr(erro, "status_code", None),
        "resposta": f"Não foi possível obter uma resposta da LLM. {detalhe}",
        "avisos": [detalhe],
    }
    resultado.update(extras)
    return resultado


def _desempacotar_resultado_agente(resultado):
    if not isinstance(resultado, tuple) or len(resultado) != 4:
        raise ValueError("CORE_AGENT_RESULT_INVALID")
    return resultado




def _metadata_resposta_agente(detalhes):
    detalhes = detalhes if isinstance(detalhes, dict) else {}
    falha = detalhes.get("write_failure")
    if isinstance(falha, dict) and falha:
        return {"write_failure": falha}
    return None
def _resultado_agente(status, texto, detalhes):
    detalhes = detalhes if isinstance(detalhes, dict) else {}
    return {
        "status": status,
        "resposta": texto,
        "avisos": list(detalhes.get("limitations") or []),
        "details": detalhes,
    }


def _processar_agente(pergunta, config, projeto, task_id=None, conversation_context=None):
    config_execucao = dict(config)
    job_progress.publicar(config_execucao, "agent", "Eyle iniciou a tarefa")
    try:
        status, texto, estado_pendente, detalhes = _desempacotar_resultado_agente(
            executar_agente(
                pergunta,
                config_execucao,
                projeto=projeto,
                retornar_detalhes=True,
                task_id=task_id,
                conversation_context=conversation_context,
            )
        )
    except ErroLLM as erro:
        return _resultado_falha_llm(erro)
    if status == "needs_user" and estado_pendente:
        estado_pendente = salvar_agent_pendente(estado_pendente, projeto=projeto, config=config_execucao)
        texto = estado_pendente["pergunta_ao_usuario"]
    job_progress.publicar(
        config_execucao, "finalizing", "Montando a resposta final",
        partial_text=texto[-16000:] if isinstance(texto, str) else None,
    )
    registrar_mensagem("assistant", texto, metadata=_metadata_resposta_agente(detalhes))
    return _resultado_agente(status, texto, detalhes)


def _retomar_agente_pendente(pendente, config, resposta_usuario=None):
    projeto = carregar_projeto()
    try:
        status, texto, nova_pendencia, detalhes = _desempacotar_resultado_agente(
            executar_agente(
                str((pendente.get("estado") or {}).get("request") or ""),
                dict(config),
                projeto=projeto,
                retomar=pendente,
                retornar_detalhes=True,
                task_id=(pendente.get("estado") or {}).get("task_id"),
                resposta_usuario=resposta_usuario,
            )
        )
    except ErroLLM as erro:
        return _resultado_falha_llm(erro)
    if status == "needs_user" and nova_pendencia:
        nova_pendencia = salvar_agent_pendente(nova_pendencia, projeto=projeto, config=config)
        texto = nova_pendencia["pergunta_ao_usuario"]
    else:
        limpar_agent_pendente()
    registrar_mensagem("assistant", texto, metadata=_metadata_resposta_agente(detalhes))
    return _resultado_agente(status, texto, detalhes)


def _cancelar_agente_pendente(pendente):
    limpar_agent_pendente()
    tool = (pendente.get("tool_pendente") or {}).get("tool", "?")
    resposta = f"Ok, cancelado. A ferramenta '{tool}' não foi executada."
    registrar_mensagem("assistant", resposta)
    detalhes = {"status": "blocked", "failure_code": "CANCELLED"}
    return _resultado_agente("blocked", resposta, detalhes)


def processar(pergunta, registrar_pergunta=True, historico_snapshot=None,
              task_id=None, source_job_id=None, source_message_id=None):
    """Single public path: interface -> AgentSession -> LLM/tools -> response."""
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
        "max_generated_tokens": max(1, int(cfg_agent.get("max_completion_tokens", 6000))),
        "max_completion_tokens": max(1, int(cfg_agent.get("max_completion_tokens", 6000))),
        "max_prompt_tokens": max(1, int(cfg_agent.get("max_prompt_tokens", 96000))),
        "max_total_tokens": max(1, int(cfg_agent.get("max_total_tokens", 102000))),
        "llm_calls": 0, "llm_requests": 0,
        "prompt_tokens_reserved": 0, "prompt_tokens_estimated_raw": 0,
        "prompt_tokens_actual": 0, "prompt_tokens_cached": 0,
        "prompt_tokens_uncached": 0, "prompt_tokens_effective": 0,
        "generated_tokens": 0,
        "reasoning_tokens_actual": 0, "provider_reported_tokens": 0,
        "history_messages_omitted": 0,
    }
    projeto = carregar_projeto()
    if registrar_pergunta:
        registrar_mensagem("user", pergunta)

    pendente = carregar_agent_pendente()
    if pendente and pendente.get("continuation_kind") == "user_input":
        return _retomar_agente_pendente(pendente, config, resposta_usuario=pergunta)
    controle = _controle_pendencia(pergunta) if pendente else None
    if not pendente and _EXPLICIT_CONTROL.fullmatch(str(pergunta or "")):
        return _resultado_controle_pendencia("Não existe alteração aguardando confirmação.")
    if controle:
        selecionada, erro = _selecionar_pendencia(pergunta, pendente)
        if erro:
            return _resultado_controle_pendencia(erro)
        valida, motivo = _validar_pendencia(selecionada, projeto)
        if not valida:
            limpar_agent_pendente()
            return _resultado_controle_pendencia(f"A pendência foi descartada: {motivo}.")
        if controle == "aplicar":
            return _retomar_agente_pendente(selecionada, config, resposta_usuario=pergunta)
        return _cancelar_agente_pendente(selecionada)
    if pendente:
        # A new natural-language request supersedes the unapplied proposal.
        # It reaches the same AgentSession instead of being classified by keywords.
        limpar_agent_pendente()

    origem = carregar_conversa() if historico_snapshot is None else historico_snapshot
    historico = _historico_sem_mensagem_atual(_historico_sem_erros_llm(origem), pergunta)
    conversation_context = {"recent_messages": historico[-12:]}
    return _processar_agente(
        pergunta, config, projeto, task_id=task_id,
        conversation_context=conversation_context,
    )

