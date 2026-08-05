#!/usr/bin/env python3
"""Regressoes 55.10: resumo operacional expansivel por job."""
from pathlib import Path

from engine import queue
from engine import worker
from engine.work_summary import construir_resumo_trabalho


def _valor(resumo, etapa_numero, rotulo):
    etapa = next(item for item in resumo["steps"] if item["number"] == etapa_numero)
    campo = next(item for item in etapa["fields"] if item["label"] == rotulo)
    return campo["value"]


def test_resumo_operacional_reflete_trabalho_real_do_agente():
    resumo = construir_resumo_trabalho(
        {"tipo": "pergunta", "texto": "verifique o projeto e sugira melhorias"},
        {
            "agente_status": "success",
            "verificacao_aprovada": True,
            "roteador": {"tipo": "agente", "modo": "analyze"},
            "agente_conclusao": {
                "mode": "analyze",
                "tools_called": ["list_tree", "read_file"],
                "evidence_ids": ["E1", "E2"],
                "evidencias_usadas": [{
                    "arquivo": "app.py",
                    "linha_inicio": 1,
                    "linha_fim": 10,
                    "total_linhas_arquivo": 10,
                    "leitura_completa": True,
                }],
                "completion_gate": {"passed": True},
                "fallback_cause": None,
                "limitacoes": [],
            },
        },
        124.1,
        projeto={"arquivos": 1},
    )

    assert resumo["title"] == "Trabalho concluído"
    assert resumo["duration_seconds"] == 124.1
    assert _valor(resumo, 1, "Objetivo") == "verifique o projeto e sugira melhorias"
    assert _valor(resumo, 2, "Arquivo lido") == "app.py"
    assert _valor(resumo, 2, "Linhas") == "1–10"
    assert _valor(resumo, 2, "Leitura completa") == "sim"
    assert _valor(resumo, 3, "Modo") == "analyze"
    assert _valor(resumo, 3, "Ferramentas utilizadas") == "list_tree, read_file"
    assert _valor(resumo, 3, "Evidências coletadas") == "E1, E2"
    assert _valor(resumo, 4, "Status") == "success"
    assert _valor(resumo, 4, "Fallback utilizado") == "não"
    assert _valor(resumo, 4, "Validação") == "aprovada"
    assert _valor(resumo, 4, "Limitações") == "projeto contém apenas um arquivo"


def test_resumo_declara_fallback_e_leitura_parcial_sem_inventar():
    resumo = construir_resumo_trabalho(
        {"tipo": "pergunta", "texto": "analise o projeto"},
        {
            "agente_status": "success",
            "roteador": {
                "tipo": "agente_fallback_leitura",
                "fallback_cause": "invalid_agent_json",
            },
            "trabalho_contexto": {
                "modo": "analyze",
                "ferramentas": ["list_tree", "read_file"],
                "arquivos_lidos": [{
                    "arquivo": "app.py",
                    "linha_inicio": 1,
                    "linha_fim": 8,
                    "total_linhas_arquivo": 10,
                    "truncado": True,
                    "leitura_completa": False,
                }],
                "limitacoes": ["leitura integral limitada pelo orçamento de contexto"],
            },
        },
        4,
        projeto={"arquivos": 1},
    )

    assert _valor(resumo, 2, "Leitura completa") == "não"
    assert _valor(resumo, 4, "Fallback utilizado") == "sim — invalid_agent_json"
    assert "leitura integral limitada" in _valor(resumo, 4, "Limitações")


def test_queue_persiste_resumo_separado_do_resultado(monkeypatch, tmp_path):
    banco = tmp_path / "fila.sqlite3"
    monkeypatch.setattr(queue, "DB_PATH", str(banco))
    queue._evento_disponivel.clear()

    job_id = queue.adicionar({"tipo": "pergunta", "texto": "A"})
    queue.proximo(timeout=0)
    resumo = {
        "schema_version": 1,
        "title": "Trabalho concluído",
        "duration_seconds": 2.5,
        "steps": [{
            "number": 1,
            "title": "Entendimento",
            "fields": [{"label": "Objetivo", "value": "A"}],
        }],
    }

    assert queue.concluir(
        job_id, {"resposta": "ok"},
        resumo_trabalho=resumo, duracao_segundos=2.5,
    ) is True
    salvo = queue.obter(job_id)

    assert salvo["resultado"] == {"resposta": "ok"}
    assert salvo["progresso"]["work_summary"] == resumo
    assert salvo["progresso"]["elapsed_seconds"] == 2.5



def test_worker_fecha_job_com_resumo_operacional(monkeypatch, tmp_path):
    banco = tmp_path / "fila.sqlite3"
    monkeypatch.setattr(queue, "DB_PATH", str(banco))
    queue._evento_disponivel.clear()
    job_id = queue.adicionar({
        "tipo": "pergunta",
        "texto": "analise app.py",
        "mensagem_id": 7,
    })
    resultado = {
        "agente_status": "success",
        "verificacao_aprovada": True,
        "roteador": {"tipo": "agente", "modo": "analyze"},
        "agente_conclusao": {
            "mode": "analyze",
            "tools_called": ["read_file"],
            "evidence_ids": ["ev-0001"],
            "evidencias_usadas": [{
                "arquivo": "app.py",
                "linha_inicio": 1,
                "linha_fim": 3,
                "total_linhas_arquivo": 3,
                "leitura_completa": True,
            }],
            "completion_gate": {"passed": True},
            "limitacoes": [],
        },
    }
    monkeypatch.setattr(worker, "processar_evento", lambda evento: resultado)
    monkeypatch.setattr(worker.eyle_engine, "carregar_projeto", lambda: {"arquivos": 1})

    assert worker.processar_proximo(timeout=0) is True
    salvo = queue.obter(job_id)

    assert salvo["status"] == "completed"
    assert salvo["progresso"]["work_summary"]["title"] == "Trabalho concluído"
    assert _valor(salvo["progresso"]["work_summary"], 2, "Arquivo lido") == "app.py"

def test_interface_contem_details_expansivel_e_formatacao_de_duracao():
    raiz = Path(__file__).resolve().parents[1]
    js = (raiz / "web" / "static" / "app.js").read_text(encoding="utf-8")
    css = (raiz / "web" / "static" / "style.css").read_text(encoding="utf-8")

    assert 'document.createElement("details")' in js
    assert 'document.createElement("summary")' in js
    assert "Trabalho concluído" in js
    assert 'padStart(2, "0")' in js
    assert ".work-summary-body" in css
