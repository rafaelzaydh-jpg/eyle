#!/usr/bin/env python3
"""Revision 55.15: fail-closed project evidence and request identity."""
from pathlib import Path
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import engine as engine_mod
from engine import queue
from engine import worker
from engine.utility_gate import validate_response_utility


def _usar_fila_temporaria(monkeypatch, tmp_path, nome="fila.sqlite3"):
    banco = tmp_path / nome
    monkeypatch.setattr(queue, "DB_PATH", str(banco))
    queue._evento_disponivel.clear()
    queue._schemas_prontos.discard(str(banco.resolve()))
    return banco


def _config_pequeno():
    return {
        "context": {
            "token_budget": 1500,
            "chars_per_token": 4,
            "small_project_full_read_max_files": 8,
            "small_project_full_read_max_lines": 600,
            "small_project_full_read_max_chars": 16000,
        },
        "agent": {"semantic_grounding": {"require_inline_citations": False}},
    }


def test_gate_rejeita_resposta_plausivel_sem_evidencia():
    resposta = (
        "O projeto consiste em uma aplicação Flask mínima. "
        "Ele não define rotas e funciona apenas como bootstrap."
    )
    gate = validate_response_utility(
        resposta, "Faça a análise do projeto", task_type="project_read", evidence=[],
    )
    assert gate["ok"] is False
    assert gate["code"] == "project_not_read"


def test_visao_geral_sem_leitura_nao_chama_llm_nem_publica(monkeypatch):
    chamadas = {"llm": 0, "mensagem": 0}
    monkeypatch.setattr(engine_mod, "_codigos_reais_projeto_pequeno", lambda *args: {})
    monkeypatch.setattr(
        engine_mod,
        "executar_executor",
        lambda *args, **kwargs: chamadas.__setitem__("llm", chamadas["llm"] + 1),
    )
    monkeypatch.setattr(
        engine_mod,
        "registrar_mensagem",
        lambda *args, **kwargs: chamadas.__setitem__("mensagem", chamadas["mensagem"] + 1),
    )
    monkeypatch.setattr(engine_mod, "carregar_decisoes", lambda: [])

    resultado = engine_mod._processar_visao_geral(
        "Faça a análise do projeto", _config_pequeno(),
        {"caminho_origem": "/inexistente"}, {"app.py": {"linhas": 1}}, {}, "teste",
    )

    assert resultado["status"] == "failed"
    assert resultado["error_code"] == "PROJECT_NOT_READ"
    assert chamadas == {"llm": 0, "mensagem": 0}


def test_visao_geral_lida_mas_alucinada_falha_grounding(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("valor = 1\n", encoding="utf-8")
    publicado = []
    monkeypatch.setattr(engine_mod, "carregar_decisoes", lambda: [])
    monkeypatch.setattr(
        engine_mod, "executar_executor",
        lambda *args, **kwargs: (
            "O projeto é uma aplicação Flask com servidor HTTP, rotas REST e banco PostgreSQL."
        ),
    )
    monkeypatch.setattr(
        engine_mod, "registrar_mensagem", lambda *args, **kwargs: publicado.append(args),
    )

    resultado = engine_mod._processar_visao_geral(
        "Faça a análise do projeto", _config_pequeno(),
        {"caminho_origem": str(tmp_path)}, {"app.py": {"linhas": 1}}, {}, "teste",
    )

    assert resultado["status"] == "failed"
    assert resultado["error_code"] == "UNGROUNDED_PROJECT_ANALYSIS"
    assert publicado == []
    assert resultado["trabalho_contexto"]["evidence_ids"]


def test_worker_trata_agente_status_failed_como_falha(monkeypatch, tmp_path):
    _usar_fila_temporaria(monkeypatch, tmp_path)
    job_id = queue.adicionar({"tipo": "pergunta", "texto": "analise"})
    resultado = {
        "agente_status": "failed",
        "error_code": "PROJECT_NOT_READ",
        "resposta": "Nenhum arquivo foi lido.",
    }
    monkeypatch.setattr(worker, "processar_evento", lambda evento: resultado)

    assert worker.processar_proximo(timeout=0) is True
    salvo = queue.obter(job_id)
    assert salvo["status"] == "failed"
    assert salvo["resultado"] == resultado


def test_task_id_nao_pode_ser_reutilizado_por_outro_objetivo(monkeypatch, tmp_path):
    _usar_fila_temporaria(monkeypatch, tmp_path)
    primeira = queue.criar_tarefa_agente(
        "analise app.py", "analyze", projeto_hash="abc", task_id="job-1", source_job_id=1,
    )
    assert primeira["objetivo"] == "analise app.py"

    with pytest.raises(queue.AgentTaskContextMismatch) as erro:
        queue.criar_tarefa_agente(
            "oi", "analyze", projeto_hash="abc", task_id="job-1", source_job_id=1,
        )
    assert erro.value.error_code == "REQUEST_CONTEXT_MISMATCH"


def test_identidade_da_fila_e_estavel_e_muda_com_banco_novo(monkeypatch, tmp_path):
    _usar_fila_temporaria(monkeypatch, tmp_path, "a.sqlite3")
    primeira = queue.database_instance_id()
    assert primeira
    assert queue.database_instance_id() == primeira

    _usar_fila_temporaria(monkeypatch, tmp_path, "b.sqlite3")
    segunda = queue.database_instance_id()
    assert segunda
    assert segunda != primeira


def test_painel_descarta_jobs_antigos_quando_instancia_muda():
    javascript = Path("web/static/app.js").read_text(encoding="utf-8")
    assert 'INSTANCE_STORAGE_KEY = "eyleQueueInstanceId"' in javascript
    assert "sincronizarInstanciaFila(data.queue_instance_id)" in javascript
    assert "trackedJobs = []" in javascript
    assert 'status: "pending"' in javascript
    assert "...anterior" not in javascript
