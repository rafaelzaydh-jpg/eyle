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
from eyle.runtime.continuation import validate_pending_continuation, confirmation_control, is_explicit_confirmation_control, resolve_semantic_choice
from eyle.host import build_bundled_host
from eyle.runtime.config import carregar_config_validada
from eyle.runtime.storage import lock_para, salvar_json_atomico
from eyle.runtime.memory_graph import gc_orphan_recall_snapshots, ingest_chat_message, world_scope
from eyle.runtime import queue as fila_persistente
from eyle.runtime import progress as job_progress
from eyle.runtime import telemetry
from llm.executar import ErroLLM

MEMORY_DIR = os.path.join(BASE_DIR, "memory")
CONTEXT_DIR = os.path.join(BASE_DIR, "context")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
AGENT_PENDENTE_DIR = os.path.join(CONTEXT_DIR, "pending")
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

def carregar_provider_identity():
    return HOST.provider_identity()

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


def _conversation_id(*, rotate: bool = False) -> str:
    """Return the physical identity of the current visible conversation."""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    caminho = os.path.join(MEMORY_DIR, "conversation_state.json")
    with lock_para(caminho):
        state = _carregar_json(caminho, {})
        cid = str(state.get("conversation_id") or "").strip() if isinstance(state, dict) else ""
        if rotate or not cid:
            cid = "conv-" + secrets.token_hex(12)
            _salvar_json(caminho, {"conversation_id": cid, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return cid


def _persist_chat_memory(mensagem):
    """Persist the exact message as Runtime-authored Chat Memory.

    Conversation JSON remains the UI/job snapshot authority. The Graph write is
    idempotent and uses the same physical message identity; it never summarizes
    or decides relevance.
    """
    if not isinstance(mensagem, dict):
        return None
    provider_context = carregar_provider_context()
    memory_ctx = (provider_context or {}).get("core_memory") or {}
    storage = MEMORY_DIR
    scope_id = memory_ctx.get("world_scope_id")
    if not scope_id:
        return None
    return ingest_chat_message(
        storage,
        world_scope_value=world_scope(str(scope_id)),
        conversation_id=str(mensagem.get("conversation_id") or _conversation_id()),
        message_id=int(mensagem.get("id")),
        role=str(mensagem.get("role") or ""),
        content=str(mensagem.get("text") or ""),
        timestamp=str(mensagem.get("timestamp") or "") or None,
        reply_to_message_id=mensagem.get("reply_to_message_id"),
        source_job_id=mensagem.get("source_job_id"),
    )


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
        _conversation_id(rotate=True)

    # Uma conversa visual limpa nao deve carregar uma confirmacao pendente de
    # um transcript que acabou de ser descartado. Isto nao altera Memory Graph.
    limpar_todas_pendencias()
    return {
        "status": "ok",
        "removed_messages": removidas,
        "memory_graph_preserved": True,
    }

def registrar_mensagem_com_snapshot(role, texto, metadata=None):
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
            "conversation_id": _conversation_id(),
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
        if role == "user":
            # A cognitive choice is not a Runtime continuation gate. It is a
            # structured assistant affordance; the next user message resolves
            # or supersedes it exactly like ordinary conversation.
            for anterior in reversed(mensagens):
                interaction = anterior.get("interaction") if isinstance(anterior, dict) else None
                if not isinstance(interaction, dict) or interaction.get("kind") != "choice":
                    continue
                if not interaction.get("resolved"):
                    interaction = dict(interaction)
                    interaction["resolved"] = True
                    interaction["selected_text"] = str(texto or "")
                    anterior["interaction"] = interaction
                break
        if isinstance(metadata, dict):
            for chave, valor in metadata.items():
                if chave not in {"id", "role", "text", "timestamp"}:
                    mensagem[chave] = valor
        mensagens.append(mensagem)
        salvar_conversa(mensagens)
        try:
            mensagem["chat_memory_id"] = _persist_chat_memory(mensagem)
            salvar_conversa(mensagens)
        except (OSError, ValueError):
            # Chat continuity is still backed by the atomic conversation snapshot;
            # Graph ingestion is idempotent and can be retried without semantic loss.
            mensagem["chat_memory_ingest_pending"] = True
            salvar_conversa(mensagens)
        historico = _historico_sem_erros_llm(mensagens)
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


def _hash_provider_identity(provider_identity):
    """Hash the stable Host-owned environment identity opaquely.

    Mutable provider context is deliberately excluded. Resource revisions bind
    live continuations to source state separately.
    """
    if not isinstance(provider_identity, dict) or not provider_identity:
        return None
    encoded = json.dumps(provider_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def _recovery_dir():
    return os.path.join(AGENT_PENDENTE_DIR, "recovery")

def _pending_files():
    os.makedirs(AGENT_PENDENTE_DIR, exist_ok=True)
    recovery_dir = _recovery_dir()
    os.makedirs(recovery_dir, exist_ok=True)
    files = [
        os.path.join(AGENT_PENDENTE_DIR, name)
        for name in os.listdir(AGENT_PENDENTE_DIR)
        if name.endswith(".json")
    ]
    files.extend(
        os.path.join(recovery_dir, name)
        for name in os.listdir(recovery_dir)
        if name.endswith(".json")
    )
    return files


def _pending_execution_id(pending):
    pending = pending or {}
    execution_state = pending.get("execution_state") or {}
    value = str(execution_state.get("execution_id") or "").strip()
    return value or None


def _pending_storage_path(pending):
    pending = pending or {}
    pending_id = str(pending.get("id") or "").upper()
    execution_id = _pending_execution_id(pending) or "interactive"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", execution_id).strip("._-")[:48] or "execution"
    digest = hashlib.sha256(execution_id.encode("utf-8")).hexdigest()[:12]
    if pending.get("continuation_kind") == "recoverable_execution":
        return os.path.join(_recovery_dir(), f"{safe}-{digest}.json")
    return os.path.join(AGENT_PENDENTE_DIR, f"{safe}-{digest}-{pending_id}.json")


def _load_pending_file(path):
    pending = _carregar_json(path, None)
    if pending is None:
        return None
    try:
        validate_pending_continuation(pending, persisted=True)
    except ValueError:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return None
    return dict(pending)


def listar_agent_pendentes():
    out = []
    for path in _pending_files():
        pending = _load_pending_file(path)
        if pending is not None:
            out.append(pending)
    out.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return out


def _novo_id_pendencia():
    existentes = {str(item.get("id") or "").upper() for item in listar_agent_pendentes()}
    while True:
        candidato = secrets.token_hex(2).upper()
        if candidato not in existentes:
            return candidato

def _preparar_pendencia(data, provider_identity, config=None, *, checkpoint_generation=None):
    data = dict(data or {})
    validate_pending_continuation(data)
    cfg = (config or {}).get("confirmacoes") or {}
    ttl = max(60, int(cfg.get("expiracao_segundos", _TTL_PENDENCIA_DEFAULT)))
    now = _agora_utc()
    expires_at = (
        _formatar_data_utc(now + timedelta(seconds=ttl))
        if data.get("continuation_kind") in {"capability_confirmation", "semantic_choice"} else None
    )
    data.update({
        "id": _novo_id_pendencia(),
        "created_at": _formatar_data_utc(now),
        "expires_at": expires_at,
        "provider_identity_hash": _hash_provider_identity(provider_identity),
    })
    if data.get("continuation_kind") == "recoverable_execution":
        generation = int(checkpoint_generation or 0)
        if generation < 1:
            raise ValueError("PENDING_CHECKPOINT_GENERATION_INVALID")
        data["checkpoint_generation"] = generation
    validate_pending_continuation(data, persisted=True)
    return data


def _validar_pendencia(pending, provider_identity, now=None):
    try:
        validate_pending_continuation(pending, persisted=True)
    except ValueError as error:
        return False, str(error)
    if re.fullmatch(r"[0-9A-F]{4}", str(pending.get("id") or "").upper()) is None:
        return False, "PENDING_ID_INVALID"
    expiration = _parse_data_utc(pending.get("expires_at"))
    if pending.get("continuation_kind") in {"capability_confirmation", "semantic_choice"}:
        if expiration is None or (now or _agora_utc()) >= expiration:
            return False, "PENDING_EXPIRED"
    elif pending.get("expires_at") is not None:
        return False, "PENDING_SCHEMA_INVALID"
    if pending.get("provider_identity_hash") != _hash_provider_identity(provider_identity):
        return False, "PENDING_PROVIDER_IDENTITY_MISMATCH"
    return True, None


def carregar_agent_pendente(pergunta=None, execution_id=None, continuation_kinds=None):
    pendentes = listar_agent_pendentes()
    if continuation_kinds is not None:
        allowed = {str(v) for v in continuation_kinds}
        pendentes = [item for item in pendentes if str(item.get("continuation_kind") or "") in allowed]
    if not pendentes:
        return None
    refs = [item.upper() for item in re.findall(r"\b[0-9A-Fa-f]{4}\b", str(pergunta or ""))]
    if refs:
        for pending in pendentes:
            if str(pending.get("id") or "").upper() in refs:
                return pending
        return None
    wanted_execution = str(execution_id or "").strip()
    if wanted_execution:
        matches = [item for item in pendentes if _pending_execution_id(item) == wanted_execution]
        if matches:
            return matches[0]
    # Free-text semantic choices remain ergonomic when there is exactly one
    # outstanding gate. With several gates Runtime refuses to guess ownership.
    if len(pendentes) == 1:
        return pendentes[0]
    return None


def salvar_agent_pendente(estado_pendente, provider_context=None, config=None, provider_identity=None):
    identity = provider_identity if isinstance(provider_identity, dict) else carregar_provider_identity()
    raw = dict(estado_pendente or {})
    if raw.get("continuation_kind") == "recoverable_execution":
        path = _pending_storage_path(raw)
        with lock_para(path):
            previous = _carregar_json(path, None)
            generation = 1
            if isinstance(previous, dict):
                try:
                    validate_pending_continuation(previous, persisted=True)
                except ValueError:
                    previous = None
                else:
                    if _pending_execution_id(previous) == _pending_execution_id(raw):
                        generation = int(previous.get("checkpoint_generation") or 0) + 1
            data = _preparar_pendencia(
                raw, identity, config, checkpoint_generation=generation,
            )
            data["question"] = str(data["question"]).rstrip()
            validate_pending_continuation(data, persisted=True)
            _salvar_json(path, data)
        try:
            telemetry.record(
                "recovery", "checkpoint", "replaced" if generation > 1 else "created", 0.0,
                execution_id=_pending_execution_id(data),
                job_id=_JOB_ATUAL_ID.get(),
                metadata={
                    "checkpoint_reason": data.get("checkpoint_reason"),
                    "checkpoint_generation": generation,
                    "resume_count": int((data.get("execution_state") or {}).get("resume_count") or 0),
                },
            )
        except Exception:
            pass
        return dict(data)

    data = _preparar_pendencia(raw, identity, config)
    data["question"] = str(data["question"]).rstrip()
    validate_pending_continuation(data, persisted=True)
    path = _pending_storage_path(data)
    with lock_para(path):
        _salvar_json(path, data)
    return dict(data)


def limpar_agent_pendente(pendente=None, *, pending_id=None, execution_id=None):
    target_id = str(pending_id or ((pendente or {}).get("id") if isinstance(pendente, dict) else "") or "").upper()
    target_execution = str(execution_id or (_pending_execution_id(pendente) if isinstance(pendente, dict) else "") or "").strip()
    removed = 0
    for item in listar_agent_pendentes():
        if target_id and str(item.get("id") or "").upper() != target_id:
            continue
        if target_execution and _pending_execution_id(item) != target_execution:
            continue
        if not target_id and not target_execution:
            continue
        path = _pending_storage_path(item)
        with lock_para(path):
            try:
                os.remove(path)
                removed += 1
            except FileNotFoundError:
                pass
    return removed


def limpar_todas_pendencias():
    removed = 0
    for item in listar_agent_pendentes():
        path = _pending_storage_path(item)
        with lock_para(path):
            try:
                os.remove(path)
                removed += 1
            except FileNotFoundError:
                pass
    return removed


def gc_navegacao_memoria_orfa():
    """Collect crash-orphaned recall cursors without any age/TTL policy."""
    provider_context = carregar_provider_context()
    memory = provider_context.get("core_memory") if isinstance(provider_context, dict) else None
    storage = str((memory or {}).get("storage_dir") or "").strip() if isinstance(memory, dict) else ""
    if not storage:
        return {"removed": 0, "snapshot_ids": []}
    live_execution_ids = set()
    preserve_snapshot_ids = set()

    def scan(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key) == "snapshot_id" and isinstance(item, str) and item.strip():
                    preserve_snapshot_ids.add(item.strip())
                else:
                    scan(item)
        elif isinstance(value, list):
            for item in value:
                scan(item)

    for pending in listar_agent_pendentes():
        execution_id = _pending_execution_id(pending)
        if execution_id:
            live_execution_ids.add(execution_id)
        scan(pending.get("session"))
    return gc_orphan_recall_snapshots(
        storage, live_execution_ids=live_execution_ids, preserve_snapshot_ids=preserve_snapshot_ids,
    )



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




def _public_interaction(pending):
    if not isinstance(pending, dict):
        return None
    pending_id = str(pending.get("id") or "")
    kind = pending.get("continuation_kind")
    if kind == "capability_confirmation":
        operation = str((pending.get("session") or {}).get("pending_operation", {}).get("operation") or "")
        return {
            "id": pending_id,
            "kind": "confirmation",
            "title": "Confirmar alteração?",
            "description": "",
            "operation": operation,
            "options": [
                {"id": "accept", "label": "Aceitar", "submit_text": f"confirmar {pending_id}"},
                {"id": "reject", "label": "Recusar", "submit_text": f"cancelar {pending_id}"},
            ],
            "allow_free_text": False,
            "resolved": False,
        }
    if kind == "semantic_choice":
        return {
            "id": pending_id,
            "kind": "choice",
            "title": "Escolha como continuar",
            "description": "",
            "options": [
                {"id": f"choice-{index+1}", "label": str(label), "submit_text": f"{label} [{pending_id}]"}
                for index, label in enumerate(pending.get("options") or [])
            ],
            "allow_free_text": bool(pending.get("allow_free_text")),
            "resolved": False,
        }
    return None



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
    interaction = _public_interaction(pending)
    if interaction:
        metadata["interaction"] = interaction
    elif isinstance(detalhes.get("interaction"), dict):
        metadata["interaction"] = dict(detalhes["interaction"])
    return metadata


def carregar_interacao_publica():
    """Return safe UI metadata for the active Runtime-owned user gate."""
    pendings = [
        item for item in listar_agent_pendentes()
        if item.get("continuation_kind") in {"capability_confirmation", "semantic_choice"}
    ]
    # Never choose a global winner when several executions are awaiting input.
    # Per-message metadata carries the exact pending ID to the UI.
    if len(pendings) != 1:
        return None
    pending = pendings[0]
    provider_context = carregar_provider_context()
    valid, _ = _validar_pendencia(pending, carregar_provider_identity())
    if not valid:
        return None
    return _public_interaction(pending)

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
    if status == "recoverable_checkpoint" and estado_pendente:
        persisted = salvar_agent_pendente(
            estado_pendente, provider_context=provider_context, config=config_execucao,
        )
        return _retomar_agente_pendente(
            persisted, config_execucao, source_job_id=source_job_id,
            execution_id=execution_id, automatic=True,
        )
    if status in {"confirmation_required", "choice_required"} and estado_pendente:
        estado_pendente = salvar_agent_pendente(estado_pendente, provider_context=provider_context, config=config_execucao)
        texto = estado_pendente["question"]
    if source_job_id is not None:
        job_progress.publicar_job(source_job_id, "finalizing", "Montando a resposta final", partial_text=texto[-16000:] if isinstance(texto, str) else None)
    registrar_mensagem("assistant", texto, metadata=_metadata_resposta_agente(status, detalhes, estado_pendente))
    return _resultado_agente(status, texto, detalhes)


def _retomar_agente_pendente(
    pendente, config, resposta_usuario=None, source_job_id=None,
    execution_id=None, automatic=False,
):
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
    if status == "recoverable_checkpoint" and nova_pendencia:
        nova_pendencia = salvar_agent_pendente(
            nova_pendencia, provider_context=provider_context, config=config,
        )
        limpar_agent_pendente(pendente)
        return _retomar_agente_pendente(
            nova_pendencia, config, source_job_id=source_job_id,
            execution_id=execution_id or _pending_execution_id(nova_pendencia),
            automatic=True,
        )
    if status in {"confirmation_required", "choice_required"} and nova_pendencia:
        nova_pendencia = salvar_agent_pendente(nova_pendencia, provider_context=provider_context, config=config)
        texto = nova_pendencia["question"]
        limpar_agent_pendente(pendente)
    else:
        limpar_agent_pendente(pendente)
    if pendente.get("continuation_kind") == "recoverable_execution":
        try:
            telemetry.record(
                "recovery", "resume",
                "success" if status not in {"failed", "recoverable_checkpoint"} else "failed",
                0.0,
                execution_id=_pending_execution_id(pendente),
                job_id=source_job_id,
                metadata={
                    "checkpoint_reason": pendente.get("checkpoint_reason"),
                    "result_status": status,
                    "resume_count": int((detalhes.get("llm_usage") or {}).get("execution_resume_count") or 0),
                },
            )
        except Exception:
            pass
    registrar_mensagem("assistant", texto, metadata=_metadata_resposta_agente(status, detalhes, nova_pendencia))
    return _resultado_agente(status, texto, detalhes)


def _cancelar_agente_pendente(pendente):
    limpar_agent_pendente(pendente)
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

    # Recoverable checkpoints are Runtime-owned and never compete with user
    # confirmation/choice routing. A restarted worker reuses the same execution
    # id and resumes the persisted logical AgentSession before doing fresh work.
    recovery = carregar_agent_pendente(
        execution_id=execution_id, continuation_kinds={"recoverable_execution"},
    ) if execution_id else None
    if recovery:
        valida, _motivo = _validar_pendencia(recovery, carregar_provider_identity())
        if valida:
            return _retomar_agente_pendente(
                recovery, config, source_job_id=source_job_id,
                execution_id=execution_id, automatic=True,
            )
        limpar_agent_pendente(recovery)

    pendente = carregar_agent_pendente(
        pergunta=pergunta,
        continuation_kinds={"capability_confirmation", "semantic_choice"},
    )
    if pendente:
        valida, motivo = _validar_pendencia(pendente, carregar_provider_identity())
        if not valida:
            limpar_agent_pendente(pendente)
            pendente = None

    controle = confirmation_control(pergunta) if pendente and pendente.get("continuation_kind") == "capability_confirmation" else None
    if not pendente and is_explicit_confirmation_control(pergunta):
        return _resultado_controle_pendencia("Não existe capability aguardando confirmação.")

    if pendente and pendente.get("continuation_kind") == "semantic_choice":
        if confirmation_control(pergunta) == "cancelar":
            return _retomar_agente_pendente(
                pendente, config, resposta_usuario="cancelar",
                source_job_id=source_job_id, execution_id=execution_id,
            )
        choice_text = re.sub(r"\s*\[[0-9A-Fa-f]{4}\]\s*$", "", str(pergunta or "")).strip()
        resolved = resolve_semantic_choice(choice_text, pendente)
        if resolved is None:
            public = _public_interaction(pendente) or {}
            labels = [str(item.get("label") or "") for item in public.get("options") or []]
            return _resultado_controle_pendencia("Escolha uma das opções disponíveis" + (" ou escreva outra opção." if pendente.get("allow_free_text") else ": " + "; ".join(labels)))
        return _retomar_agente_pendente(
            pendente, config, resposta_usuario=resolved,
            source_job_id=source_job_id, execution_id=execution_id,
        )

    if pendente and controle == "cancelar":
        selecionada, erro = _selecionar_pendencia(pergunta, pendente)
        if erro:
            return _resultado_controle_pendencia(erro)
        return _retomar_agente_pendente(
            selecionada, config, resposta_usuario="cancelar",
            source_job_id=source_job_id, execution_id=execution_id,
        )

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
        # A new natural-language request supersedes an unapplied physical proposal.
        limpar_agent_pendente(pendente)

    origem = carregar_conversa() if historico_snapshot is None else historico_snapshot
    historico = _historico_sem_mensagem_atual(_historico_sem_erros_llm(origem), pergunta)
    recent_messages = []
    for item in historico:
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
    conversation_context = {
        "conversation_id": str((origem[-1] if origem else {}).get("conversation_id") or _conversation_id()),
        "recent_messages": recent_messages,
        "total_messages": len(recent_messages),
    }
    return _processar_agente(
        pergunta, config, provider_context, execution_id=execution_id,
        conversation_context=conversation_context, source_job_id=source_job_id,
    )

