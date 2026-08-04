#!/usr/bin/env python3
"""Criterios de aceite das Atualizacoes 44 e 45."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod  # noqa: E402
import engine.engine as engine_mod  # noqa: E402
from engine.agent_state import AgentState  # noqa: E402
from engine.config_schema import ConfigError, validar_config  # noqa: E402
from engine.roteador import classificar_modo_projeto, classificar_pergunta  # noqa: E402


def _config(max_steps=8):
    return {
        "llm": {"context_window_tokens": 4080, "max_tokens": 700},
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "max_recent_observations": 4,
        },
        "agent": {
            "enabled": True,
            "enabled_modes": ["analyze", "suggest"],
            "max_steps": max_steps,
            "max_no_progress_decisions": 3,
            "max_tentativas_parse": 1,
            "max_erros_consecutivos": 3,
            "max_chars_por_observacao": 500,
            "max_fatos_importantes": 10,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
            "exigir_run_tests_apos_escrita": True,
        },
    }


def test_roteador_unifica_todo_pedido_de_projeto_quando_agente_ativo():
    estrutura = {"audio.py": {"funcoes_classes": ["tocar"]}}
    casos = [
        ("Analise o projeto", "analyze"),
        ("O que faz audio.py?", "analyze"),
        ("Sugira melhorias no projeto", "suggest"),
        ("Corrija audio.py", "edit"),
    ]
    for pergunta, modo in casos:
        tipo, _ = classificar_pergunta(
            pergunta, estrutura=estrutura, entendimento={}, agent_habilitado=True,
        )
        assert tipo == "agente"
        assert classificar_modo_projeto(pergunta) == modo

    assert classificar_pergunta(
        "Oi", estrutura=estrutura, entendimento={}, agent_habilitado=True,
    )[0] == "chat"


def test_cli_e_worker_usam_o_mesmo_ponto_de_entrada_do_agente(monkeypatch):
    chamadas = []
    projeto = {"caminho_origem": "/tmp/projeto"}
    monkeypatch.setattr(engine_mod, "carregar_config", lambda: _config())
    monkeypatch.setattr(engine_mod, "carregar_projeto", lambda: projeto)
    monkeypatch.setattr(engine_mod, "carregar_estrutura", lambda: {})
    monkeypatch.setattr(engine_mod, "carregar_entendimento", lambda: {})
    monkeypatch.setattr(engine_mod, "_pendencias_existentes", lambda: [])
    monkeypatch.setattr(engine_mod, "registrar_mensagem", lambda *args: None)

    def fake_processar_agente(pergunta, config, projeto_recebido, entendimento, motivo):
        chamadas.append((pergunta, projeto_recebido, motivo))
        return {"resposta": "ok", "roteador": {"tipo": "agente"}}

    monkeypatch.setattr(engine_mod, "_processar_agente", fake_processar_agente)

    cli = engine_mod.processar("Analise o projeto", registrar_pergunta=True)
    web = engine_mod.processar(
        "Analise o projeto", registrar_pergunta=False, historico_snapshot=[],
    )
    assert cli["roteador"]["tipo"] == web["roteador"]["tipo"] == "agente"
    assert len(chamadas) == 2


def test_edit_desativado_bloqueia_sem_fallback_apos_atualizacao_46(monkeypatch):
    monkeypatch.setattr(engine_mod, "registrar_mensagem", lambda *args: None)
    monkeypatch.setattr(
        engine_mod, "executar_agente",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("loop edit nao deveria abrir")),
    )
    monkeypatch.setattr(
        engine_mod, "_processar_engenharia",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback nao deveria abrir")),
    )

    resultado = engine_mod._processar_agente(
        "corrija a.py", _config(), {"caminho_origem": "/tmp/projeto"}, {}, "agente",
    )
    assert resultado["roteador"]["tipo"] == "agente"
    assert resultado["roteador"]["modo"] == "edit"
    assert "fallback" not in resultado["roteador"]
    assert resultado["agente_status"] == "blocked"


def test_goal_state_simples_tem_contrato_completo_e_plano_curto():
    estado = AgentState(_config())
    estado.definir_objetivo("O que faz audio.py?", "project_read", modo="analyze")
    goal = estado.goal_state

    assert goal["objective"] == "O que faz audio.py?"
    assert goal["mode"] == "analyze"
    assert goal["success_criteria"]
    assert goal["constraints"]
    assert 1 <= len(goal["plan"]) <= 2
    assert goal["current_step"] == 1
    assert goal["blockers"] == []
    assert goal["evidence_needed"] == ["codigo_fresco_relevante"]


def test_modo_analyze_rejeita_write_antes_da_execucao():
    estado = AgentState(_config())
    estado.definir_objetivo("Analise a.py", "project_read", modo="analyze")
    valido, motivo = estado.validar_transicao("apply_patch", "WRITE")
    assert valido is False
    assert "somente" in motivo

    geral = AgentState(_config())
    geral.definir_objetivo("Analise o projeto", "project_read", modo="analyze")
    assert "list_tree" in geral.goal_state["plan"][0]["description"]
    valido, motivo = geral.validar_transicao("read_range", "READ")
    assert valido is False
    assert "list_tree" in motivo


def test_max_steps_conta_acao_real_e_ainda_permite_final(tmp_path, monkeypatch):
    (tmp_path / "audio.py").write_text("valor = 1\n", encoding="utf-8")
    respostas = iter([
        '{"final":"cedo demais"}',
        json.dumps({
            "tool": "read_range",
            "arguments": {
                "caminho_relativo": "audio.py", "linha_inicio": 1, "linha_fim": 1,
            },
        }),
        json.dumps({
            "final": {
                "resposta": "audio.py:1 analisado",
                "evidence_ids": ["ev-0001"],
                "verificacao": "hash fresco",
                "limitacoes": [],
            },
        }),
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))

    status, _, _, detalhes = agent_mod.executar_agente(
        "Analise audio.py", _config(max_steps=1),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert detalhes["goal_state"]["actions_executed"] == 1


def test_falha_de_tool_replaneja_em_vez_de_repetir_cosmeticamente():
    estado = AgentState(_config())
    estado.definir_objetivo("Analise o projeto", "project_read", modo="analyze")
    plano_antes = json.dumps(estado.goal_state["plan"], sort_keys=True)
    estado.registrar_acao(
        "search_code", {"pergunta": "audio"},
        {
            "status": "failed", "ok": False, "executed": True,
            "changed": False, "error_code": "SEARCH_FAILED", "detail": "indice ausente",
        },
        contar_execucao=True,
    )
    assert estado.goal_state["replan_reason"] == "tool_failure"
    assert "search_code falhou" in estado.goal_state["blockers"][0]
    assert json.dumps(estado.goal_state["plan"], sort_keys=True) != plano_antes


def test_replanejamento_da_llm_exige_hipotese_negada_e_evidencia_fresca():
    estado = AgentState(_config())
    estado.definir_objetivo("Analise audio.py", "project_read", modo="analyze")
    ok, erro = estado.aplicar_replanejamento({
        "trigger": "hypothesis_denied", "detail": "nao era cache",
        "plan": ["Ler a configuracao", "Concluir"],
    })
    assert ok is False
    assert "evidencia fresca" in erro

    estado.registrar_acao(
        "read_range", {},
        {
            "status": "success", "ok": True, "executed": True,
            "changed": False, "error_code": None,
            "detail": {
                "arquivo": "audio.py", "linha_inicio": 1, "linha_fim": 1,
                "trecho_numerado": "     1 | valor = 1", "content_hash": "a" * 64,
            },
        },
    )
    ok, erro = estado.aplicar_replanejamento({
        "trigger": "hypothesis_denied", "detail": "nao era cache",
        "plan": ["Ler a configuracao", "Concluir"],
    })
    assert ok is True
    assert erro is None
    assert estado.goal_state["replan_reason"] == "hypothesis_denied"


def test_trace_carrega_objetivo_passo_e_o_que_falta(tmp_path, monkeypatch):
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setattr(agent_mod, "_TRACE_PATH", str(trace))
    estado = AgentState(_config())
    estado.definir_objetivo("Analise audio.py", "project_read", modo="analyze")
    agent_mod._registrar_trace_estado(estado, {"tipo": "teste", "step": 1})

    salvo = json.loads(trace.read_text(encoding="utf-8"))
    assert salvo["goal_state"]["objective"] == "Analise audio.py"
    assert salvo["goal_state"]["current_step"] == 1
    assert salvo["goal_state"]["evidence_needed"] == ["codigo_fresco_relevante"]


def test_goal_state_persiste_sem_perder_plano_contadores_ou_modo():
    estado = AgentState(_config())
    estado.definir_objetivo("Sugira melhorias em audio.py", "project_read", modo="suggest")
    estado.registrar_acao("list_tree", {}, {"status": "success", "ok": True}, contar_execucao=True)
    reidratado = AgentState.from_dict(json.loads(json.dumps(estado.to_dict())), _config())
    reidratado.definir_objetivo(
        "Sugira melhorias em audio.py", "project_read", modo="suggest",
    )
    assert reidratado.goal_state["mode"] == "suggest"
    assert reidratado.goal_state["plan"] == estado.goal_state["plan"]
    assert reidratado.acoes_executadas == 1


def test_config_rejeita_modo_desconhecido_ou_guarda_sem_progresso_zero():
    with pytest.raises(ConfigError):
        validar_config({"agent": {"enabled_modes": ["analyze", "hack"]}})
    with pytest.raises(ConfigError):
        validar_config({"agent": {"max_no_progress_decisions": 0}})
