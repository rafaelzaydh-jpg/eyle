#!/usr/bin/env python3
"""Progresso publico e seguro para jobs web.

Esta camada publica somente etapas operacionais, metricas e, quando permitido,
um rascunho da resposta final. Nunca publica prompts, chain-of-thought, JSON
interno do Agente, observacoes privadas ou conteudo bruto de ferramentas.
"""
from __future__ import annotations

import threading
import time

from eyle.runtime import queue

_LOCK = threading.Lock()
_ULTIMA_PUBLICACAO = {}


def _runtime(config):
    runtime = (config or {}).get("_runtime_agent_budget") or {}
    return runtime if isinstance(runtime, dict) else {}


def job_id_de(config):
    valor = _runtime(config).get("source_job_id")
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def tempo_decorrido(config):
    inicio = _runtime(config).get("started_monotonic")
    if inicio is None:
        return None
    try:
        return max(0.0, time.monotonic() - float(inicio))
    except (TypeError, ValueError):
        return None


def publicar(config, phase, message, *, force=True, min_interval=0.2, **campos):
    """Atualiza o resumo publico do job associado a ``config``.

    ``force=False`` ativa throttle por job, util para chunks de streaming.
    """
    job_id = job_id_de(config)
    if job_id is None:
        return False
    agora = time.monotonic()
    if not force:
        with _LOCK:
            anterior = _ULTIMA_PUBLICACAO.get(job_id, 0.0)
            if agora - anterior < max(0.05, float(min_interval)):
                return False
            _ULTIMA_PUBLICACAO[job_id] = agora

    payload = {
        "phase": str(phase or "processing")[:80],
        "message": str(message or "Processando")[:500],
    }
    decorrido = tempo_decorrido(config)
    if decorrido is not None:
        payload["elapsed_seconds"] = round(decorrido, 2)
    for chave, valor in campos.items():
        if valor is not None:
            payload[chave] = valor
    try:
        return queue.atualizar_progresso(job_id, payload)
    except Exception:
        # Observabilidade nunca pode derrubar a tarefa principal.
        return False


def publicar_job(job_id, phase, message, **campos):
    try:
        return queue.atualizar_progresso(
            int(job_id), {"phase": phase, "message": message, **campos},
        )
    except Exception:
        return False
