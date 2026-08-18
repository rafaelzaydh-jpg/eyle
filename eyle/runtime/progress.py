#!/usr/bin/env python3
"""Safe public progress for web jobs, driven by ExecutionContext."""
from __future__ import annotations
import warnings
import threading, time
from eyle.runtime import queue

_LOCK = threading.Lock()
_ULTIMA_PUBLICACAO = {}


def job_id_de(execution):
    valor = getattr(execution, "source_job_id", None)
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def tempo_decorrido(execution):
    inicio = getattr(execution, "started_monotonic", None)
    if inicio is None: return None
    try: return max(0.0, time.monotonic() - float(inicio))
    except (TypeError, ValueError): return None


def publicar(execution, phase, message, *, force=True, min_interval=0.2, **campos):
    job_id = job_id_de(execution)
    if job_id is None: return False
    agora=time.monotonic()
    if not force:
        with _LOCK:
            anterior=_ULTIMA_PUBLICACAO.get(job_id,0.0)
            if agora-anterior < max(0.05,float(min_interval)): return False
            _ULTIMA_PUBLICACAO[job_id]=agora
    payload={"phase":str(phase or "processing")[:80],"message":str(message or "Processando")[:500]}
    decorrido=tempo_decorrido(execution)
    if decorrido is not None: payload["elapsed_seconds"]=round(decorrido,2)
    for chave,valor in campos.items():
        if valor is not None: payload[chave]=valor
    try:
        return queue.atualizar_progresso(job_id,payload)
    except Exception as exc:
        warnings.warn(f"JOB_PROGRESS_UPDATE_FAILED:{type(exc).__name__}:{exc}", RuntimeWarning, stacklevel=2)
        return False


def publicar_job(job_id, phase, message, **campos):
    try:
        return queue.atualizar_progresso(int(job_id), {"phase":phase,"message":message,**campos})
    except Exception as exc:
        warnings.warn(f"JOB_PROGRESS_UPDATE_FAILED:{type(exc).__name__}:{exc}", RuntimeWarning, stacklevel=2)
        return False
