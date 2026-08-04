from engine import engine as engine_mod
from engine import queue
from engine import worker


def _usar_fila_temporaria(monkeypatch, tmp_path):
    banco = tmp_path / "fila.sqlite3"
    monkeypatch.setattr(queue, "DB_PATH", str(banco))
    queue._evento_disponivel.clear()
    return banco


def test_fila_persistente_preserva_fifo_resultado_e_status(monkeypatch, tmp_path):
    banco = _usar_fila_temporaria(monkeypatch, tmp_path)

    primeiro = queue.adicionar({"tipo": "pergunta", "texto": "A"})
    segundo = queue.adicionar({"tipo": "pergunta", "texto": "B"})

    assert banco.exists()
    assert queue.tamanho() == 2
    evento = queue.proximo(timeout=0)
    assert evento["texto"] == "A"
    assert evento["_job_id"] == primeiro
    assert queue.concluir(primeiro, {"resposta": "ok"}) is True

    salvo = queue.obter(primeiro)
    assert salvo["status"] == "completed"
    assert salvo["resultado"] == {"resposta": "ok"}
    assert queue.proximo(timeout=0)["_job_id"] == segundo


def test_worker_recoloca_job_interrompido_na_fila(monkeypatch, tmp_path):
    _usar_fila_temporaria(monkeypatch, tmp_path)
    job_id = queue.adicionar({"tipo": "pergunta", "texto": "sobrevivo"})

    reservado = queue.proximo(timeout=0)
    assert reservado["_job_id"] == job_id
    assert queue.obter(job_id)["status"] == "processing"

    assert queue.recuperar_interrompidos() == 1
    recuperado = queue.proximo(timeout=0)
    assert recuperado["_job_id"] == job_id
    assert recuperado["_job_tentativa"] == 2


def test_falha_do_worker_fica_registrada(monkeypatch, tmp_path):
    _usar_fila_temporaria(monkeypatch, tmp_path)
    job_id = queue.adicionar({"tipo": "quebrar"})
    monkeypatch.setattr(
        worker,
        "processar_evento",
        lambda evento: (_ for _ in ()).throw(RuntimeError("boom persistente")),
    )

    assert worker.processar_proximo(timeout=0) is True
    salvo = queue.obter(job_id)
    assert salvo["status"] == "failed"
    assert "RuntimeError: boom persistente" in salvo["erro"]
    assert queue.estatisticas()["ultima_falha"]["id"] == job_id


def test_job_usa_snapshot_e_nao_historico_futuro(monkeypatch):
    snapshot_a = [{"id": 1, "role": "user", "text": "A"}]
    conversa_futura = snapshot_a + [{"id": 2, "role": "user", "text": "B"}]
    recebido = {}

    monkeypatch.setattr(engine_mod, "carregar_config", lambda: {})
    monkeypatch.setattr(engine_mod, "carregar_projeto", lambda: None)
    monkeypatch.setattr(engine_mod, "carregar_conversa", lambda: conversa_futura)
    monkeypatch.setattr(
        engine_mod, "classificar_pergunta", lambda *args, **kwargs: ("chat", "chat"),
    )
    monkeypatch.setattr(engine_mod, "registrar_mensagem", lambda *args: None)

    def fake_chat(pergunta, config, historico=None):
        recebido["historico"] = historico
        return "resposta A"

    monkeypatch.setattr(engine_mod, "executar_chat", fake_chat)
    resultado = engine_mod.processar(
        "A", registrar_pergunta=False, historico_snapshot=snapshot_a,
    )

    assert resultado["resposta"] == "resposta A"
    # A e' a mensagem atual: entra em MENSAGEM ATUAL, nao no historico.
    assert recebido["historico"] == []
    assert "B" not in [mensagem["text"] for mensagem in recebido["historico"]]


def test_worker_repassa_snapshot_do_job(monkeypatch):
    snapshot = [{"id": 7, "role": "user", "text": "antes"}]
    chamada = {}

    def fake_processar(texto, **kwargs):
        chamada.update({"texto": texto, **kwargs})
        return {"confianca": None}

    monkeypatch.setattr(worker.eyle_engine, "processar", fake_processar)
    worker.processar_evento({
        "tipo": "pergunta", "texto": "agora", "historico_snapshot": snapshot,
    })

    assert chamada["registrar_pergunta"] is False
    assert chamada["historico_snapshot"] == snapshot
