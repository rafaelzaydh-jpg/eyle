from eyle.runtime import service as service_mod
from eyle.runtime import queue
from eyle.runtime import worker


def _usar_fila_temporaria(monkeypatch, tmp_path):
    banco = tmp_path / "fila.sqlite3"
    monkeypatch.setattr(queue, "DB_PATH", str(banco))
    queue._evento_disponivel.clear()
    return banco


def test_fila_persistente_preserva_fifo_resultado_e_status(monkeypatch, tmp_path):
    banco = _usar_fila_temporaria(monkeypatch, tmp_path)

    primeiro = queue.adicionar({"type": "pergunta", "texto": "A"})
    segundo = queue.adicionar({"type": "pergunta", "texto": "B"})

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
    job_id = queue.adicionar({"type": "pergunta", "texto": "sobrevivo"})

    reservado = queue.proximo(timeout=0)
    assert reservado["_job_id"] == job_id
    assert queue.obter(job_id)["status"] == "processing"

    assert queue.recuperar_interrompidos() == 1
    recuperado = queue.proximo(timeout=0)
    assert recuperado["_job_id"] == job_id
    assert recuperado["_job_tentativa"] == 2


def test_falha_do_worker_fica_registrada(monkeypatch, tmp_path):
    _usar_fila_temporaria(monkeypatch, tmp_path)
    job_id = queue.adicionar({"type": "quebrar"})
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

    monkeypatch.setattr(service_mod, "carregar_config", lambda: {
        "agent": {"task_deadline_seconds": 30, "max_total_tokens": 34000},
    })
    monkeypatch.setattr(service_mod, "carregar_projeto", lambda: {})
    monkeypatch.setattr(service_mod, "carregar_conversa", lambda: conversa_futura)
    monkeypatch.setattr(service_mod, "registrar_mensagem", lambda *args, **kwargs: None)

    def fake_agent(pergunta, config, **kwargs):
        recebido["contexto"] = kwargs.get("conversation_context")
        return "success", "resposta A", None, {
            "status": "success", "limitations": [],
        }

    monkeypatch.setattr(service_mod, "executar_agente", fake_agent)
    resultado = service_mod.processar(
        "A", registrar_pergunta=False, historico_snapshot=snapshot_a,
    )

    assert resultado["resposta"] == "resposta A"
    assert recebido["contexto"]["recent_messages"] == []



def test_worker_repassa_snapshot_do_job(monkeypatch):
    snapshot = [{"id": 7, "role": "user", "text": "antes"}]
    chamada = {}

    def fake_processar(texto, **kwargs):
        chamada.update({"texto": texto, **kwargs})
        return {"confianca": None}

    monkeypatch.setattr(worker.eyle_service, "processar", fake_processar)
    worker.processar_evento({
        "type": "pergunta", "texto": "agora", "historico_snapshot": snapshot,
    })

    assert chamada["registrar_pergunta"] is False
    assert chamada["historico_snapshot"] == snapshot


def test_falha_estruturada_do_engine_nao_vira_completed(monkeypatch, tmp_path):
    _usar_fila_temporaria(monkeypatch, tmp_path)
    job_id = queue.adicionar({"type": "pergunta", "texto": "oi"})
    resultado_falha = {
        "status": "failed",
        "error_code": "TRANSPORT_ERROR",
        "resposta": "Nao foi possivel acessar a LLM local.",
    }
    monkeypatch.setattr(worker, "processar_evento", lambda evento: resultado_falha)

    assert worker.processar_proximo(timeout=0) is True

    salvo = queue.obter(job_id)
    assert salvo["status"] == "failed"
    assert salvo["resultado"] == resultado_falha
    assert "Nao foi possivel acessar" in salvo["erro"]


def test_queue_falhar_pode_preservar_resultado_estruturado(monkeypatch, tmp_path):
    _usar_fila_temporaria(monkeypatch, tmp_path)
    job_id = queue.adicionar({"type": "pergunta", "texto": "A"})
    queue.proximo(timeout=0)
    resultado = {"status": "failed", "error_code": "EMPTY_RESPONSE"}

    assert queue.falhar(job_id, "sem resposta", resultado=resultado) is True

    salvo = queue.obter(job_id)
    assert salvo["status"] == "failed"
    assert salvo["resultado"] == resultado
    assert salvo["erro"] == "sem resposta"


def test_service_result_has_no_router_layer(monkeypatch):
    monkeypatch.setattr(service_mod, "carregar_config", lambda: {
        "agent": {"task_deadline_seconds": 30,
                  "max_total_tokens": 12000},
    })
    monkeypatch.setattr(service_mod, "carregar_projeto", lambda: {})
    monkeypatch.setattr(service_mod, "carregar_conversa", lambda: [])
    monkeypatch.setattr(service_mod, "registrar_mensagem", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service_mod, "executar_agente",
        lambda *args, **kwargs: ("success", "resposta", None, {
            "status": "success",
        }),
    )
    result = service_mod.processar("oi", registrar_pergunta=False)
    assert result["resposta"] == "resposta"
    assert "roteador" not in result
    assert "agente_status" not in result and "agente_conclusao" not in result
    assert result["details"]["status"] == "success"


def test_natural_request_with_nao_is_not_treated_as_cancel(monkeypatch):
    monkeypatch.setattr(service_mod, "carregar_config", lambda: {
        "agent": {"task_deadline_seconds": 30,
                  "max_total_tokens": 12000},
    })
    monkeypatch.setattr(service_mod, "carregar_projeto", lambda: {})
    monkeypatch.setattr(service_mod, "carregar_conversa", lambda: [])
    monkeypatch.setattr(service_mod, "registrar_mensagem", lambda *args, **kwargs: None)
    monkeypatch.setattr(service_mod, "carregar_agent_pendente", lambda: {
        "continuation_kind": "write_confirmation", "id": "ABCD",
    })
    cleared = []
    monkeypatch.setattr(service_mod, "limpar_agent_pendente", lambda: cleared.append(True))
    monkeypatch.setattr(
        service_mod, "executar_agente",
        lambda *args, **kwargs: ("success", "novo pedido entendido", None, {
            "status": "success",
        }),
    )
    result = service_mod.processar(
        "não use JavaScript; deixe o HTML responsivo", registrar_pergunta=False,
    )
    assert result["resposta"] == "novo pedido entendido"
    assert cleared == [True]


def test_runtime_assigns_confirmation_metadata_once(monkeypatch, tmp_path):
    pending_path = tmp_path / "agent_pendente.json"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(service_mod, "AGENT_PENDENTE_PATH", str(pending_path))
    monkeypatch.setattr(service_mod.secrets, "token_hex", lambda size: "a1b2")

    core_pending = {
        "pending_schema_version": "1",
        "continuation_kind": "write_confirmation",
        "question": "Proposal ready.",
        "session": {"request": "change the file"},
        "transaction_id": "tx-1",
    }
    saved = service_mod.salvar_agent_pendente(
        core_pending,
        projeto={"caminho_origem": str(project)},
        config={"confirmacoes": {"expiracao_segundos": 600}},
    )

    assert saved["id"] == "A1B2"
    assert saved["question"].count("Pending ID: A1B2") == 1
    assert saved["question"].count("confirmar A1B2") == 1
    assert saved["project_hash"] == service_mod._hash_projeto(
        {"caminho_origem": str(project)}
    )
    assert "id" not in core_pending
