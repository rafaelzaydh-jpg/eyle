#!/usr/bin/env python3
"""Revisao 55.6: retries curtos e fallback textual de leitura."""
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import engine as engine_mod
from llm import executar as llm_mod


def _config_llm():
    return {
        "llm": {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "teste",
            "openai_compatible": False,
            "temperature": 0.2,
            "timeout_seconds": 2,
            "connect_timeout_seconds": 1,
            "read_timeout_seconds": 2,
            "agent_timeout_seconds": 2,
            "executor_timeout_seconds": 2,
            "max_tokens": 100,
            "agent_max_tokens": 50,
            "retry_max_attempts": 3,
            "agent_retry_max_attempts": 1,
            "retry_base_delay_seconds": 0,
            "retry_max_delay_seconds": 0,
            "retry_jitter_seconds": 0,
            "retry_read_timeouts": False,
            "max_concurrent_requests": 1,
            "cache": {"ativado": False},
        }
    }


def test_agente_nao_aninha_retries_de_transporte(monkeypatch):
    chamadas = []

    def falhar(req, timeout=None):
        chamadas.append(req.full_url)
        raise urllib.error.URLError(ConnectionRefusedError("recusado"))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", falhar)
    with pytest.raises(llm_mod.ErroLLM) as erro:
        llm_mod._chamar_llm("s", "u", _config_llm(), perfil="agent")

    assert erro.value.error_code == "TRANSPORT_ERROR"
    assert len(chamadas) == 1


def test_fallback_de_leitura_usa_visao_geral_sem_json(monkeypatch):
    atualizacoes = []
    monkeypatch.setattr(engine_mod, "carregar_estrutura", lambda: {})
    monkeypatch.setattr(
        engine_mod,
        "_processar_visao_geral",
        lambda *args, **kwargs: {"resposta": "analise pronta", "roteador": {}},
    )
    monkeypatch.setattr(
        engine_mod.fila_persistente,
        "atualizar_tarefa_agente",
        lambda *args, **kwargs: atualizacoes.append((args, kwargs)) or True,
    )

    resultado = engine_mod._fallback_leitura_legado(
        "faça uma analise do projeto",
        {},
        {"caminho_origem": "/tmp/projeto"},
        {"componentes": {}},
        "analise geral encaminhada ao agente",
        "task-1",
        "invalid_agent_json",
    )

    assert resultado["resposta"] == "A recuperação textual terminou sem uma conclusão útil validada."
    assert resultado["agente_status"] == "failed"
    assert resultado["roteador"]["tipo"] == "agente_fallback_leitura"
    assert resultado["roteador"]["fallback_pipeline"] == "visao_geral"
    assert atualizacoes[-1][1]["status"] == "failed"


def test_fallback_de_leitura_nunca_assume_edicao(monkeypatch):
    monkeypatch.setattr(engine_mod, "carregar_estrutura", lambda: {})
    assert engine_mod._fallback_leitura_legado(
        "corrija o projeto",
        {},
        {"caminho_origem": "/tmp/projeto"},
        {"componentes": {}},
        "pedido edit",
        "task-2",
        "invalid_agent_json",
    ) is None


def _config_agente():
    return {
        "agent": {
            "rollout_mode": "read_only",
            "enabled_modes": ["analyze", "suggest", "edit"],
        }
    }


def test_processar_agente_cai_no_fallback_apos_json_invalido(monkeypatch):
    monkeypatch.setattr(
        engine_mod.fila_persistente,
        "criar_tarefa_agente",
        lambda *args, **kwargs: {
            "task_id": "task-3", "status": "running", "continuacao": None,
        },
    )
    monkeypatch.setattr(
        engine_mod,
        "executar_agente",
        lambda *args, **kwargs: (
            "failed",
            "formato invalido",
            None,
            {
                "task_type": "project_read",
                "fallback_cause": "invalid_agent_json",
                "evidencias_usadas": [],
            },
        ),
    )
    esperado = {"resposta": "fallback ok"}
    monkeypatch.setattr(
        engine_mod, "_fallback_leitura_legado", lambda *args, **kwargs: esperado,
    )

    resultado = engine_mod._processar_agente(
        "faça uma analise do projeto",
        _config_agente(),
        {"caminho_origem": "/tmp/projeto"},
        {"componentes": {}},
        "analise geral encaminhada ao agente",
    )
    assert resultado is esperado


def test_processar_agente_cai_no_fallback_apos_timeout(monkeypatch):
    monkeypatch.setattr(
        engine_mod.fila_persistente,
        "criar_tarefa_agente",
        lambda *args, **kwargs: {
            "task_id": "task-4", "status": "running", "continuacao": None,
        },
    )

    def falhar(*args, **kwargs):
        raise llm_mod.ErroLLM(
            "timeout", error_code="TASK_DEADLINE_EXCEEDED", transient=False,
        )

    monkeypatch.setattr(engine_mod, "executar_agente", falhar)
    capturado = {}

    def fallback(*args, **kwargs):
        capturado["causa"] = args[-1]
        return {"resposta": "fallback timeout ok"}

    monkeypatch.setattr(engine_mod, "_fallback_leitura_legado", fallback)
    resultado = engine_mod._processar_agente(
        "faça uma analise do projeto",
        _config_agente(),
        {"caminho_origem": "/tmp/projeto"},
        {"componentes": {}},
        "analise geral encaminhada ao agente",
    )

    assert resultado["resposta"] == "fallback timeout ok"
    assert capturado["causa"] == "agent_llm_task_deadline_exceeded"
