#!/usr/bin/env python3
"""Nucleo unificado da Eyle 2.7.5.

Existe somente um caminho público para tarefas: o Core ECC.
A LLM escolhe Explorar, Construir ou Concluir; Runtime controla somente fatos físicos.
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
from eyle.runtime.continuation import validate_pending_continuation, confirmation_control, is_explicit_confirmation_control
from eyle.host import build_bundled_host
from eyle.runtime.config import carregar_config_validada
from eyle.runtime.storage import lock_para, salvar_json_atomico
from eyle.runtime import queue as fila_persistente
from eyle.runtime import progress as job_progress
from llm.executar import ErroLLM

MEMORY_DIR = os.path.join(BASE_DIR, "memory")
CONTEXT_DIR = os.path.join(BASE_DIR, "context")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
AGENT_PENDENTE_PATH = os.path.join(CONTEXT_DIR, "agent_pendente.json")
HOST = build_bundled_host(BASE_DIR)
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
    return carregar_config_validada(CONFIG_PATH, HOST.registry)

def carregar_provider_context():
    return HOST.provider_context()

def carregar_ambiente():
    """Return opaque Host presentation metadata for product shells.

    Runtime does not interpret domain fields. The bundled CLI/Web shell may
    choose to render fields exposed by its Host; a PetBot or network Host can
    expose different metadata without adding domain knowledge here.
    """
    value = HOST.describe()
    if not isinstance(value, dict):
        raise ValueError("HOST_DESCRIPTION_INVALID")
    return value

def carregar_conversa():
    return _carregar_json(os.path.join(MEMORY_DIR, "conversa.json"), [])

def salvar_conversa(mensagens):
    _salvar_json(os.path.join(MEMORY_DIR, "conversa.json"), mensagens)


def limpar_conversa_preservando_memoria():
    """Zera somente o transcript persistido da conversa.

    O Memory Graph vive em armazenamento separado (core_memory.sqlite3) e nao
    e tocado por esta operacao. O comando e destinado a testes/benchmark em
    que se deseja uma sessao visual limpa usando a mesma memoria cognitiva.
    Recusa a limpeza enquanto houver jobs ativos para evitar corridas entre
    snapshots ja congelados e a interface.
    """
    stats = fila_persistente.estatisticas()
    ativos = int(stats.get("pending", 0) or 0) + int(stats.get("processing", 0) or 0)
    if ativos:
        return {
            "status": "busy",
            "error_code": "CONVERSATION_RESET_BUSY",
            "active_jobs": ativos,
            "removed_messages": 0,
            "memory_graph_preserved": True,
        }

    caminho = os.path.join(MEMORY_DIR, "conversa.json")
    with lock_para(caminho):
        mensagens = carregar_conversa()
        removidas = len(mensagens)
        salvar_conversa([])

    # Uma conversa visual limpa nao deve carregar uma confirmacao pendente de
    # um transcript que acabou de ser descartado. Isto nao altera Memory Graph.
    limpar_agent_pendente()
    return {
        "status": "ok",
        "removed_messages": removidas,
        "memory_graph_preserved": True,
    }

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
    """Adiciona uma mensagem atomicamente e devolve o id gerado.

    A interface web e o Worker podem gravar no mesmo processo. O lock cobre
    leitura, alocacao do id e escrita para evitar lost updates e ids duplicados.
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


def _hash_provider_context(provider_context):
    """Return a stable identity hash for the opaque provider context.

    Runtime does not interpret provider domains. It only binds a persisted
    continuation to the same provider environment that prepared it.
    """
    if not isinstance(provider_context, dict) or not provider_context:
        return None
    encoded = json.dumps(provider_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def _novo_id_pendencia():
    atual = _carregar_json(AGENT_PENDENTE_PATH, None)
    existente = str((atual or {}).get("id") or "").upper()
    while True:
        candidato = secrets.token_hex(2).upper()
        if candidato != existente:
            return candidato


def _preparar_pendencia(data, provider_context, config=None):
    data = dict(data or {})
    validate_pending_continuation(data)
    cfg = (config or {}).get("confirmacoes") or {}
    ttl = max(60, int(cfg.get("expiracao_segundos", _TTL_PENDENCIA_DEFAULT)))
    now = _agora_utc()
    provider_context_hash = _hash_provider_context(provider_context)
    expires_at = (
        _formatar_data_utc(now + timedelta(seconds=ttl))
        if data.get("continuation_kind") == "capability_confirmation" else None
    )
    data.update({
        "id": _novo_id_pendencia(),
        "created_at": _formatar_data_utc(now),
        "expires_at": expires_at,
        "provider_context_hash": provider_context_hash,
    })
    validate_pending_continuation(data, persisted=True)
    return data


def _validar_pendencia(pending, provider_context, now=None):
    try:
        validate_pending_continuation(pending, persisted=True)
    except ValueError as error:
        return False, str(error)
    if re.fullmatch(r"[0-9A-F]{4}", str(pending.get("id") or "").upper()) is None:
        return False, "PENDING_ID_INVALID"
    expiration = _parse_data_utc(pending.get("expires_at"))
    if pending.get("continuation_kind") == "capability_confirmation":
        if expiration is None or (now or _agora_utc()) >= expiration:
            return False, "PENDING_EXPIRED"
    elif pending.get("expires_at") is not None:
        return False, "PENDING_SCHEMA_INVALID"
    if pending.get("provider_context_hash") != _hash_provider_context(provider_context):
        return False, "PENDING_PROVIDER_CONTEXT_MISMATCH"
    return True, None


def carregar_agent_pendente():
    pending = _carregar_json(AGENT_PENDENTE_PATH, None)
    if pending is None:
        return None
    try:
        validate_pending_continuation(pending, persisted=True)
    except ValueError:
        try:
            os.remove(AGENT_PENDENTE_PATH)
        except OSError:
            pass
        return None
    return pending


def salvar_agent_pendente(estado_pendente, provider_context=None, config=None):
    context = provider_context if isinstance(provider_context, dict) else carregar_provider_context()
    data = _preparar_pendencia(estado_pendente, context, config)
    question = str(data["question"]).rstrip()
    if data.get("continuation_kind") == "capability_confirmation":
        instruction = f"To confirm: confirmar {data['id']}; to cancel: cancelar {data['id']}"
        marker = f"Pending ID: {data['id']}"
        if marker not in question:
            question = f"{question}\n{marker}. {instruction}"
        data["question"] = question
    validate_pending_continuation(data, persisted=True)
    _salvar_json(AGENT_PENDENTE_PATH, data)
    return data


def limpar_agent_pendente():
    if os.path.exists(AGENT_PENDENTE_PATH):
        os.remove(AGENT_PENDENTE_PATH)



def _selecionar_pendencia(pergunta, pendencia):
    if not pendencia:
        return None, "Não existe capability aguardando confirmação."
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
    failed_origin_ids = {
        item.get("reply_to_message_id")
        for item in mensagens
        if isinstance(item, dict)
        and item.get("role") == "assistant"
        and item.get("agent_status") == "failed"
        and not item.get("execution_failure")
        and item.get("reply_to_message_id") is not None
    }
    return [
        item for item in mensagens
        if not str(item.get("text") or "").startswith("[erro]")
        and not item.get("pending_delete")
        and not (
            item.get("role") == "assistant"
            and item.get("agent_status") == "failed"
            and not item.get("execution_failure")
        )
        and item.get("id") not in failed_origin_ids
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




def _public_confirmation(pending):
    if not isinstance(pending, dict) or pending.get("continuation_kind") != "capability_confirmation":
        return None
    return {
        "id": str(pending.get("id") or ""),
        "question": str(pending.get("question") or ""),
        "operation": str((pending.get("session") or {}).get("pending_operation", {}).get("operation") or ""),
    }


def _execution_failure_from_details(details):
    details = details if isinstance(details, dict) else {}
    for event in reversed(list(details.get("operation_history") or [])):
        if not isinstance(event, dict):
            continue
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        if result.get("ok") is False or event.get("ok") is False:
            failure = {
                "capability": event.get("capability"),
                "error_code": result.get("error_code") or event.get("error_code"),
                "detail": result.get("detail"),
                "retryable": result.get("retryable", event.get("retryable")),
                "failure_scope": result.get("failure_scope") or event.get("failure_scope"),
                "failure_resource": result.get("failure_resource") or event.get("failure_resource"),
                "physical_effect": result.get("physical_effect"),
            }
            return {k: v for k, v in failure.items() if v not in (None, "", [], {})}
    return None


def _metadata_resposta_agente(status, detalhes, pending=None):
    detalhes = detalhes if isinstance(detalhes, dict) else {}
    metadata = {"agent_status": str(status or "unknown")}
    failure = _execution_failure_from_details(detalhes)
    if failure:
        metadata["execution_failure"] = failure
    confirmation = _public_confirmation(pending)
    if confirmation:
        metadata["confirmation"] = confirmation
    return metadata


def carregar_confirmacao_publica():
    """Return safe UI metadata for the active Runtime confirmation gate."""
    pending = carregar_agent_pendente()
    if not pending or pending.get("continuation_kind") != "capability_confirmation":
        return None
    provider_context = carregar_provider_context()
    valid, _ = _validar_pendencia(pending, provider_context)
    if not valid:
        return None
    return _public_confirmation(pending)

def _resultado_agente(status, texto, detalhes):
    detalhes = detalhes if isinstance(detalhes, dict) else {}
    return {
        "status": status,
        "resposta": texto,
        "avisos": list(detalhes.get("limitations") or []),
        "details": detalhes,
    }


def _processar_agente(pergunta, config, provider_context, execution_id=None, conversation_context=None, source_job_id=None):
    config_execucao = dict(config)
    if source_job_id is not None:
        job_progress.publicar_job(source_job_id, "agent", "Eyle iniciou a tarefa")
    try:
        status, texto, estado_pendente, detalhes = _desempacotar_resultado_agente(
            executar_agente(
                pergunta,
                config_execucao,
                provider_context=provider_context,
                retornar_detalhes=True,
                execution_id=execution_id,
                conversation_context=conversation_context, source_job_id=source_job_id, registry=HOST.registry,
            )
        )
    except ErroLLM as erro:
        return _resultado_falha_llm(erro)
    if status == "confirmation_required" and estado_pendente:
        estado_pendente = salvar_agent_pendente(estado_pendente, provider_context=provider_context, config=config_execucao)
        texto = estado_pendente["question"]
    if source_job_id is not None:
        job_progress.publicar_job(source_job_id, "finalizing", "Montando a resposta final", partial_text=texto[-16000:] if isinstance(texto, str) else None)
    registrar_mensagem("assistant", texto, metadata=_metadata_resposta_agente(status, detalhes, estado_pendente))
    return _resultado_agente(status, texto, detalhes)


def _retomar_agente_pendente(pendente, config, resposta_usuario=None, source_job_id=None, execution_id=None):
    provider_context = carregar_provider_context()
    try:
        status, texto, nova_pendencia, detalhes = _desempacotar_resultado_agente(
            executar_agente(
                str((pendente.get("session") or {}).get("request") or ""),
                dict(config),
                provider_context=provider_context,
                retomar=pendente,
                retornar_detalhes=True,
                execution_id=execution_id or (pendente.get("session") or {}).get("execution_id"),
                resposta_usuario=resposta_usuario, source_job_id=source_job_id, registry=HOST.registry,
            )
        )
    except ErroLLM as erro:
        return _resultado_falha_llm(erro)
    if status == "confirmation_required" and nova_pendencia:
        nova_pendencia = salvar_agent_pendente(nova_pendencia, provider_context=provider_context, config=config)
        texto = nova_pendencia["question"]
    else:
        limpar_agent_pendente()
    registrar_mensagem("assistant", texto, metadata=_metadata_resposta_agente(status, detalhes, nova_pendencia))
    return _resultado_agente(status, texto, detalhes)


def _cancelar_agente_pendente(pendente):
    limpar_agent_pendente()
    resposta = "Ok, cancelado. A alteração pendente não foi aplicada."
    registrar_mensagem("assistant", resposta)
    detalhes = {"status": "cancelled", "failure_code": "CANCELLED"}
    return _resultado_agente("cancelled", resposta, detalhes)


def processar(pergunta, registrar_pergunta=True, historico_snapshot=None,
              execution_id=None, source_job_id=None, source_message_id=None):
    """Single public path: interface -> AgentSession -> LLM/capabilities -> response."""
    _JOB_ATUAL_ID.set(source_job_id)
    _MENSAGEM_ORIGEM_ATUAL_ID.set(source_message_id)
    config = dict(carregar_config() or {})
    provider_context = carregar_provider_context()
    if registrar_pergunta:
        registrar_mensagem("user", pergunta)

    pendente = carregar_agent_pendente()
    if pendente:
        valida, motivo = _validar_pendencia(pendente, provider_context)
        if not valida:
            limpar_agent_pendente()
            pendente = None

    controle = confirmation_control(pergunta) if pendente else None
    if not pendente and is_explicit_confirmation_control(pergunta):
        return _resultado_controle_pendencia("Não existe capability aguardando confirmação.")

    if pendente and controle == "cancelar":
        selecionada, erro = _selecionar_pendencia(pergunta, pendente)
        if erro:
            return _resultado_controle_pendencia(erro)
        return _cancelar_agente_pendente(selecionada)

    if pendente and controle == "aplicar":
        selecionada, erro = _selecionar_pendencia(pergunta, pendente)
        if erro:
            return _resultado_controle_pendencia(erro)
        if selecionada.get("continuation_kind") != "capability_confirmation":
            return _resultado_controle_pendencia("Essa pendência não é uma confirmação de capability.")
        return _retomar_agente_pendente(
            selecionada, config, resposta_usuario=pergunta,
            source_job_id=source_job_id, execution_id=execution_id,
        )

    if pendente:
        # A new natural-language request supersedes an unapplied write proposal.
        limpar_agent_pendente()

    origem = carregar_conversa() if historico_snapshot is None else historico_snapshot
    historico = _historico_sem_mensagem_atual(_historico_sem_erros_llm(origem), pergunta)
    recent_messages = []
    for item in historico[-12:]:
        if not isinstance(item, dict):
            continue
        message = {
            "role": str(item.get("role") or ""),
            "content": str(item.get("text") or ""),
        }
        execution_failure = item.get("execution_failure")
        if isinstance(execution_failure, dict) and execution_failure:
            message["execution_failure"] = dict(execution_failure)
        recent_messages.append(message)
    conversation_context = {"recent_messages": recent_messages}
    return _processar_agente(
        pergunta, config, provider_context, execution_id=execution_id,
        conversation_context=conversation_context, source_job_id=source_job_id,
    )

