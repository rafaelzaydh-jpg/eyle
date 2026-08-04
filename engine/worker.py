#!/usr/bin/env python3
"""
worker.py
---------
Loop permanente: enquanto o processo estiver vivo, continua processando
a fila de eventos -- mesmo que ninguem esteja com o navegador aberto.
Isso e o que faz da Eyle um agente persistente em vez de so responder
dentro de uma requisicao HTTP.

    python engine/worker.py    # roda sozinho e consome a mesma fila SQLite
                                 usada pelo Flask, inclusive entre processos

Normalmente e iniciado junto com o Flask por:

    python main.py serve
"""
import os
import sys
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from engine import queue
from engine import engine as eyle_engine


def processar_evento(evento):
    tipo = evento.get("tipo")

    if tipo == "pergunta":
        # a mensagem do usuario ja foi registrada em conversa.json por quem
        # colocou o evento na fila (web/routes.py) -- aqui so processa e
        # registra a resposta do assistente.
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
    return None


def processar_proximo(timeout=1.0):
    """Processa no maximo um job e persiste conclusao ou falha."""
    evento = queue.proximo(timeout=timeout)
    if evento is None:
        return False

    job_id = evento.get("_job_id")
    try:
        resultado = processar_evento(evento)
    except Exception as e:
        if job_id is not None:
            queue.falhar(job_id, e)
        print(f"[worker][erro] falha processando job {job_id}: {e}")
        return True

    if job_id is not None:
        queue.concluir(job_id, resultado)
    return True


def loop():
    # Falha antes de reservar qualquer job se o contrato de configuracao
    # estiver invalido. Um worker parcialmente configurado e pior que um
    # startup recusado com diagnostico claro.
    eyle_engine.carregar_config()
    recuperados = queue.recuperar_interrompidos()
    agentes_recuperados = queue.recuperar_tarefas_agente_interrompidas()
    print(
        f"[worker] iniciado, aguardando eventos... recuperados={recuperados}, "
        f"agent_tasks={agentes_recuperados}"
    )
    while True:
        processar_proximo(timeout=1.0)


def iniciar_em_thread():
    """Usado por main.py serve para rodar o worker junto com o Flask, no mesmo processo."""
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    loop()
