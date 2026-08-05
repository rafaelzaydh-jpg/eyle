"""Regressoes: remover mensagem cancela somente o job correto."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import engine as engine_mod
from engine import queue
from engine import worker


def _ambiente_temporario(monkeypatch, tmp_path):
    memory = tmp_path / "memory"
    context = tmp_path / "context"
    memory.mkdir()
    context.mkdir()
    monkeypatch.setattr(engine_mod, "MEMORY_DIR", str(memory))
    monkeypatch.setattr(queue, "DB_PATH", str(context / "fila.sqlite3"))
    queue._evento_disponivel.clear()
    return memory, context


def test_remover_mensagem_de_origem_cancela_seu_job(monkeypatch, tmp_path):
    _ambiente_temporario(monkeypatch, tmp_path)
    mensagem_id, snapshot = engine_mod.registrar_mensagem_com_snapshot("user", "analise")
    job_id = queue.adicionar({
        "tipo": "pergunta",
        "texto": "analise",
        "mensagem_id": mensagem_id,
        "historico_snapshot": snapshot,
    })
    queue.proximo(timeout=0, worker_id="worker-test")

    resultado = engine_mod.solicitar_remocao_mensagem(mensagem_id)

    assert resultado["cancelled_jobs"] == [job_id]
    assert resultado["waiting_jobs"] == []
    assert resultado["removed"] is True
    assert queue.cancelamento_solicitado(job_id) == "mensagem de origem removida pelo usuario"
    assert engine_mod.carregar_conversa() == []


def test_remover_mensagem_de_contexto_espera_job_sem_cancela_lo(monkeypatch, tmp_path):
    _ambiente_temporario(monkeypatch, tmp_path)
    mensagem_a, _ = engine_mod.registrar_mensagem_com_snapshot("user", "contexto antigo")
    mensagem_b, snapshot_b = engine_mod.registrar_mensagem_com_snapshot("user", "pergunta atual")
    job_b = queue.adicionar({
        "tipo": "pergunta",
        "texto": "pergunta atual",
        "mensagem_id": mensagem_b,
        "historico_snapshot": snapshot_b,
    })
    queue.proximo(timeout=0, worker_id="worker-test")

    resultado = engine_mod.solicitar_remocao_mensagem(mensagem_a)

    assert resultado["status"] == "deferred"
    assert resultado["cancelled_jobs"] == []
    assert resultado["waiting_jobs"] == [job_b]
    assert queue.cancelamento_solicitado(job_b) is None
    pendente = next(m for m in engine_mod.carregar_conversa() if m["id"] == mensagem_a)
    assert pendente["pending_delete"] is True

    # Um job novo nao pode herdar a mensagem que o usuario ja mandou apagar.
    _, snapshot_novo = engine_mod.registrar_mensagem_com_snapshot("user", "nova pergunta")
    assert mensagem_a not in {m["id"] for m in snapshot_novo}

    assert queue.concluir(job_b, {"resposta": "ok"}) is True
    assert engine_mod.finalizar_remocoes_pendentes() == [mensagem_a]
    assert mensagem_a not in {m["id"] for m in engine_mod.carregar_conversa()}


def test_resposta_gravada_por_job_cancelado_pode_ser_purgada(monkeypatch, tmp_path):
    _ambiente_temporario(monkeypatch, tmp_path)
    engine_mod._JOB_ATUAL_ID.set(77)
    engine_mod._MENSAGEM_ORIGEM_ATUAL_ID.set(5)
    resposta_id = engine_mod.registrar_mensagem("assistant", "resposta tardia")

    salva = engine_mod.carregar_conversa()[0]
    assert salva["id"] == resposta_id
    assert salva["source_job_id"] == 77
    assert salva["reply_to_message_id"] == 5
    assert engine_mod.remover_respostas_do_job(77) is True
    assert engine_mod.carregar_conversa() == []


def test_pergunta_web_forca_processo_terminavel_mesmo_com_isolamento_desligado(
    monkeypatch, tmp_path,
):
    _ambiente_temporario(monkeypatch, tmp_path)
    job_id = queue.adicionar({"tipo": "pergunta", "texto": "oi", "mensagem_id": 1})
    chamadas = []

    def fake_isolado(evento, deadline, **kwargs):
        chamadas.append((evento["_job_id"], kwargs.get("cancel_check")))
        return {"resposta": "ok"}

    monkeypatch.setattr(worker, "executar_evento_isolado", fake_isolado)
    monkeypatch.setattr(worker, "_resumo_publico", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "_limpar_remocoes_pendentes_seguro", lambda: [])

    assert worker.processar_proximo(
        timeout=0, worker_id="worker-web", isolate_job=False,
    ) is True
    assert chamadas and chamadas[0][0] == job_id
    assert callable(chamadas[0][1])
    assert queue.obter(job_id)["status"] == "completed"
