#!/usr/bin/env python3
"""Worker persistente, concorrente e com isolamento por processo.

Cada job pode rodar em um processo filho. O processo supervisor publica
heartbeat, aplica deadline de parede e termina o filho caso uma biblioteca
nativa ignore timeouts cooperativos.
"""
from __future__ import annotations

import multiprocessing
import os
import queue as queue_std
import sys
import threading
import time
import traceback
import uuid

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from engine import queue
from engine import telemetry
from engine import progress
from engine import engine as eyle_engine


class JobDeadlineExceeded(TimeoutError):
    error_code = "JOB_DEADLINE_EXCEEDED"


class RemoteJobError(RuntimeError):
    def __init__(self, remote_type, message, remote_traceback=None):
        super().__init__(f"{remote_type}: {message}")
        self.remote_type = remote_type
        self.remote_traceback = remote_traceback


def _resultado_indica_falha(resultado):
    return (
        isinstance(resultado, dict)
        and str(resultado.get("status") or "").strip().lower() == "failed"
    )


def _detalhe_falha_resultado(resultado):
    if not isinstance(resultado, dict):
        return "pipeline retornou falha sem diagnostico"
    for chave in ("resposta", "erro", "motivo"):
        valor = resultado.get(chave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    codigo = resultado.get("error_code")
    if codigo:
        return f"pipeline retornou status failed ({codigo})"
    return "pipeline retornou status failed"


def processar_evento(evento):
    tipo = evento.get("tipo")

    if tipo == "pergunta":
        if evento.get("_job_id") is not None:
            progress.publicar_job(
                evento["_job_id"], "routing", "Entendendo o pedido e escolhendo o fluxo",
            )
        resultado = eyle_engine.processar(
            evento["texto"],
            registrar_pergunta=False,
            historico_snapshot=evento.get("historico_snapshot"),
            task_id=(
                f"job-{evento.get('_job_id')}"
                if evento.get("_job_id") is not None else None
            ),
            source_job_id=evento.get("_job_id"),
        )
        print(
            f"[worker] processado: {evento['texto'][:60]!r} -> "
            f"grounding={resultado.get('grounding')}, "
            f"agente_status={resultado.get('agente_status')}"
        )
        return resultado

    if tipo == "remover":
        removeu = eyle_engine.remover_mensagem(evento["mensagem_id"])
        print(f"[worker] remocao da mensagem {evento['mensagem_id']}: {removeu}")
        return {"removeu": removeu}

    print(f"[worker][aviso] evento desconhecido ignorado: {evento}")
    return {"ignored": True, "event_type": tipo}


def _child_entry(evento, connection):
    """Entrada picklable do processo filho."""
    try:
        result = processar_evento(evento)
        connection.send(("ok", result))
    except BaseException as error:  # processo filho precisa reportar ate SystemExit
        try:
            connection.send((
                "error",
                {
                    "type": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(limit=30),
                },
            ))
        except Exception:
            pass
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _terminate_process(process, grace_seconds=1.0):
    if not process.is_alive():
        process.join(timeout=0.2)
        return
    process.terminate()
    process.join(timeout=max(0.0, float(grace_seconds)))
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=1.0)


def executar_evento_isolado(
    evento, deadline_seconds, *, heartbeat=None, heartbeat_interval=5,
    mp_context="spawn", target=None,
):
    """Executa um evento em filho terminavel e devolve seu resultado."""
    deadline_seconds = max(0.1, float(deadline_seconds))
    context = multiprocessing.get_context(mp_context)
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=target or _child_entry,
        args=(evento, child),
        daemon=False,
    )
    started = time.monotonic()
    next_heartbeat = started
    process.start()
    child.close()
    try:
        while True:
            now = time.monotonic()
            if heartbeat is not None and now >= next_heartbeat:
                heartbeat(process.pid)
                next_heartbeat = now + max(0.5, float(heartbeat_interval))
            remaining = deadline_seconds - (now - started)
            if remaining <= 0:
                _terminate_process(process)
                raise JobDeadlineExceeded(
                    f"job excedeu o deadline de {deadline_seconds:.3f}s e o processo filho foi encerrado"
                )
            if parent.poll(min(0.2, remaining)):
                status, payload = parent.recv()
                process.join(timeout=1.0)
                if status == "ok":
                    return payload
                raise RemoteJobError(
                    payload.get("type", "RemoteError"),
                    payload.get("message", "erro remoto sem mensagem"),
                    payload.get("traceback"),
                )
            if not process.is_alive():
                process.join(timeout=0.2)
                if parent.poll():
                    status, payload = parent.recv()
                    if status == "ok":
                        return payload
                    raise RemoteJobError(
                        payload.get("type", "RemoteError"),
                        payload.get("message", "erro remoto sem mensagem"),
                        payload.get("traceback"),
                    )
                raise RemoteJobError(
                    "WorkerChildExited",
                    f"processo filho terminou sem resultado (exitcode={process.exitcode})",
                )
    finally:
        try:
            parent.close()
        except Exception:
            pass
        if process.is_alive():
            _terminate_process(process)


def _heartbeat_durante_job(worker_id, job_id, intervalo, parar):
    while not parar.wait(max(1.0, float(intervalo))):
        try:
            queue.registrar_heartbeat(worker_id, "processing", job_id=job_id)
        except Exception as erro:
            print(f"[worker][aviso] heartbeat falhou: {erro}")


def processar_proximo(
    timeout=1.0, *, worker_id=None, heartbeat_interval=5,
    max_invalid_jobs=100, isolate_job=False, job_deadline_seconds=300,
    mp_context="spawn",
):
    """Processa no maximo um job e persiste conclusao, falha ou timeout."""
    evento = queue.proximo(
        timeout=timeout, max_invalid_jobs=max_invalid_jobs, worker_id=worker_id,
    )
    if evento is None:
        return False

    job_id = evento.get("_job_id")
    started = time.monotonic()
    parar_heartbeat = threading.Event()
    thread_heartbeat = None
    if worker_id:
        queue.registrar_heartbeat(worker_id, "processing", job_id=job_id)

    try:
        if isolate_job:
            def heartbeat(child_pid):
                if worker_id:
                    queue.registrar_heartbeat(
                        worker_id, "processing", job_id=job_id,
                        detalhe=f"child_pid={child_pid}",
                    )

            resultado = executar_evento_isolado(
                evento, job_deadline_seconds,
                heartbeat=heartbeat,
                heartbeat_interval=heartbeat_interval,
                mp_context=mp_context,
            )
        else:
            if worker_id:
                thread_heartbeat = threading.Thread(
                    target=_heartbeat_durante_job,
                    args=(worker_id, job_id, heartbeat_interval, parar_heartbeat),
                    daemon=True,
                )
                thread_heartbeat.start()
            resultado = processar_evento(evento)
    except Exception as error:
        if job_id is not None:
            queue.falhar(job_id, error)
        if worker_id:
            queue.registrar_heartbeat(worker_id, "error", job_id=job_id, detalhe=error)
        telemetry.record(
            "job", str(evento.get("tipo") or "unknown"), "timeout" if isinstance(error, JobDeadlineExceeded) else "failed",
            (time.monotonic() - started) * 1000,
            task_id=f"job-{job_id}" if job_id is not None else None,
            job_id=job_id,
            metadata={
                "error_code": getattr(error, "error_code", None),
                "exception": type(error).__name__,
                "detail": str(error)[:500],
            },
        )
        print(f"[worker][erro] falha processando job {job_id}: {error}")
        return True
    finally:
        parar_heartbeat.set()
        if thread_heartbeat is not None:
            thread_heartbeat.join(timeout=1.0)

    if _resultado_indica_falha(resultado):
        detalhe = _detalhe_falha_resultado(resultado)
        if job_id is not None:
            queue.falhar(job_id, detalhe, resultado=resultado)
        if worker_id:
            queue.registrar_heartbeat(worker_id, "error", job_id=job_id, detalhe=detalhe)
        telemetry.record(
            "job", str(evento.get("tipo") or "unknown"), "failed",
            (time.monotonic() - started) * 1000,
            task_id=f"job-{job_id}" if job_id is not None else None,
            job_id=job_id,
            metadata={
                "isolated": bool(isolate_job),
                "error_code": resultado.get("error_code"),
                "structured_failure": True,
            },
        )
        print(f"[worker][erro] job {job_id} terminou com falha estruturada: {detalhe}")
        return True

    if job_id is not None:
        queue.concluir(job_id, resultado)
    if worker_id:
        queue.registrar_heartbeat(worker_id, "idle")
    telemetry.record(
        "job", str(evento.get("tipo") or "unknown"), "ok",
        (time.monotonic() - started) * 1000,
        task_id=f"job-{job_id}" if job_id is not None else None,
        job_id=job_id,
        metadata={"isolated": bool(isolate_job)},
    )
    return True


def _consumer_loop(worker_id, cfg):
    queue.registrar_heartbeat(worker_id, "idle")
    while True:
        try:
            processed = processar_proximo(
                timeout=1.0,
                worker_id=worker_id,
                heartbeat_interval=cfg["heartbeat_interval"],
                max_invalid_jobs=cfg["max_invalid_jobs"],
                isolate_job=cfg["isolate_jobs"],
                job_deadline_seconds=cfg["job_deadline_seconds"],
                mp_context=cfg["mp_context"],
            )
            if not processed:
                queue.registrar_heartbeat(worker_id, "idle")
        except Exception as error:
            print(f"[worker][erro] ciclo da fila falhou: {type(error).__name__}: {error}")
            try:
                queue.registrar_heartbeat(worker_id, "error", detalhe=error)
            except Exception:
                pass
            time.sleep(cfg["queue_error_backoff"])


def _resolver_parallelismo(config):
    """Limita consumidores ao numero de chamadas LLM realmente simultaneas.

    Dois consumidores com uma unica vaga de LLM deixam um job antigo vivo
    enquanto outro termina. Alem de disputar o mesmo prazo global, isso pode
    publicar a falha antiga depois de uma resposta nova. O limite efetivo
    preserva a ordem observavel da conversa no backend local serial.
    """
    config = config if isinstance(config, dict) else {}
    cfg_worker = config.get("worker") or {}
    cfg_llm = config.get("llm") or {}
    solicitado = max(1, int(cfg_worker.get("max_parallel_jobs", 1)))
    capacidade_llm = max(1, int(cfg_llm.get("max_concurrent_requests", 1)))
    return solicitado, min(solicitado, capacidade_llm)


def loop():
    config = eyle_engine.carregar_config()
    cfg_worker = config.get("worker", {})
    cfg = {
        "heartbeat_interval": max(1, int(cfg_worker.get("heartbeat_interval_seconds", 5))),
        "queue_error_backoff": max(0.1, float(cfg_worker.get("queue_error_backoff_seconds", 1))),
        "max_invalid_jobs": max(1, int(cfg_worker.get("max_invalid_jobs_per_reservation", 100))),
        "job_deadline_seconds": max(1, int(cfg_worker.get(
            "job_deadline_seconds",
            config.get("engine", {}).get("task_deadline_seconds", 300) + 15,
        ))),
        "isolate_jobs": bool(cfg_worker.get("isolate_jobs", True)),
        "mp_context": str(cfg_worker.get("multiprocessing_context", "spawn")),
    }
    parallel_configurado, parallel = _resolver_parallelismo(config)
    stale_after = max(1, int(cfg_worker.get("stale_worker_seconds", 30)))
    recovered = queue.recuperar_interrompidos(stale_after_seconds=stale_after)
    agent_recovered = queue.recuperar_tarefas_agente_interrompidas()
    root_id = f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    print(
        f"[worker] iniciado: consumidores={parallel} "
        f"(configurados={parallel_configurado}), isolamento={cfg['isolate_jobs']}, "
        f"deadline={cfg['job_deadline_seconds']}s, recuperados={recovered}, "
        f"agent_tasks={agent_recovered}"
    )
    consumers = []
    for index in range(parallel):
        worker_id = f"{root_id}-{index + 1}"
        thread = threading.Thread(
            target=_consumer_loop, args=(worker_id, cfg),
            daemon=False, name=worker_id,
        )
        thread.start()
        consumers.append(thread)
    for thread in consumers:
        thread.join()


def iniciar_em_thread():
    """Usado por ``main.py serve`` para supervisionar o pool junto do Flask."""
    thread = threading.Thread(target=loop, daemon=True, name="eyle-worker-supervisor")
    thread.start()
    return thread


if __name__ == "__main__":
    loop()
