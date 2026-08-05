#!/usr/bin/env python3
"""Regressoes da revisao 55.8: formato local e fallback de leitura."""

from engine import agent as agent_mod
from engine import engine as engine_mod
from engine.roteador import classificar_pergunta


def _config_agente():
    return {
        "agent": {
            "rollout_mode": "read_only",
            "enabled_modes": ["analyze", "suggest", "edit"],
        }
    }


def _tarefa_running(*args, **kwargs):
    return {"task_id": "task-55-8", "status": "running", "continuacao": None}


def test_parser_normaliza_tool_call_openai_com_arguments_string():
    resposta = (
        '{"tool_calls":[{"function":{"name":"read_file",'
        '"arguments":"{\\"caminho_relativo\\":\\"main.py\\"}"}}]}'
    )
    assert agent_mod._parse_decisao_agente(resposta) == {
        "tool": "read_file",
        "arguments": {"caminho_relativo": "main.py"},
    }


def test_parser_normaliza_alias_plano_action_input():
    assert agent_mod._parse_decisao_agente(
        '{"action":"list_tree","action_input":{}}'
    ) == {"tool": "list_tree", "arguments": {}}


def test_parser_normaliza_resposta_final_alias():
    assert agent_mod._parse_decisao_agente(
        '{"answer":"Analise concluida."}'
    ) == {"final": "Analise concluida."}


def test_parser_nao_escolhe_ramo_quando_objeto_mistura_tool_e_final():
    assert agent_mod._parse_decisao_agente(
        '{"tool":"list_tree","arguments":{},"final":"pronto"}'
    ) is None


def test_fallback_read_usa_texto_quando_metadata_especifica_sumiu(monkeypatch):
    monkeypatch.setattr(
        engine_mod.fila_persistente, "criar_tarefa_agente", _tarefa_running,
    )
    monkeypatch.setattr(
        engine_mod,
        "executar_agente",
        lambda *args, **kwargs: (
            "failed",
            "O agente nao conseguiu decidir o proximo passo "
            "(formato invalido apos as tentativas configuradas).",
            None,
            {
                "task_type": "project_read",
                "fallback_cause": "failed",
                "evidencias_usadas": [],
            },
        ),
    )
    esperado = {"resposta": "fallback textual ok"}
    monkeypatch.setattr(
        engine_mod, "_fallback_leitura_legado", lambda *args, **kwargs: esperado,
    )

    resultado = engine_mod._processar_agente(
        "faça a analise do projeto",
        _config_agente(),
        {"caminho_origem": "/tmp/projeto"},
        {"componentes": {}},
        "analise geral encaminhada ao agente",
    )
    assert resultado is esperado


def test_fallback_read_usa_failure_code_sem_depender_do_texto(monkeypatch):
    monkeypatch.setattr(
        engine_mod.fila_persistente, "criar_tarefa_agente", _tarefa_running,
    )
    monkeypatch.setattr(
        engine_mod,
        "executar_agente",
        lambda *args, **kwargs: (
            "failed",
            "falha generica",
            None,
            {
                "task_type": "project_read",
                "failure_code": "AGENT_INVALID_DECISION_FORMAT",
                "evidencias_usadas": [],
            },
        ),
    )
    esperado = {"resposta": "fallback por codigo ok"}
    monkeypatch.setattr(
        engine_mod, "_fallback_leitura_legado", lambda *args, **kwargs: esperado,
    )

    assert engine_mod._processar_agente(
        "faça a analise do projeto",
        _config_agente(),
        {"caminho_origem": "/tmp/projeto"},
        {"componentes": {}},
        "analise geral encaminhada ao agente",
    ) is esperado


def test_falha_de_formato_nunca_vira_fallback_de_edicao():
    causa = engine_mod._causa_fallback_leitura_agente(
        "failed",
        "formato invalido",
        {
            "task_type": "project_write",
            "failure_code": "AGENT_INVALID_DECISION_FORMAT",
        },
        "edit",
    )
    assert causa is None


def test_pedido_curto_de_analise_usa_contexto_do_projeto():
    assert classificar_pergunta(
        "Faça a analise", {}, {}, agent_habilitado=True,
    )[0] == "agente"
    assert classificar_pergunta(
        "Faça a analise", {}, {}, agent_habilitado=False,
    )[0] == "visao_geral"


def test_fallback_read_nao_depende_do_roteador_legado_reconhecer_a_frase(monkeypatch):
    monkeypatch.setattr(engine_mod, "carregar_estrutura", lambda: {})
    monkeypatch.setattr(
        engine_mod, "classificar_pergunta",
        lambda *args, **kwargs: ("chat", "frase curta demais"),
    )
    esperado = {"resposta": "panorama seguro"}
    monkeypatch.setattr(
        engine_mod, "_processar_visao_geral", lambda *args, **kwargs: esperado,
    )
    monkeypatch.setattr(
        engine_mod.fila_persistente, "atualizar_tarefa_agente",
        lambda *args, **kwargs: None,
    )

    resultado = engine_mod._fallback_leitura_legado(
        "analise",
        _config_agente(),
        {"caminho_origem": "/tmp/projeto"},
        {"componentes": {}},
        "agente read",
        "task-55-8",
        "invalid_agent_json",
    )
    assert resultado["resposta"] == "A recuperação textual terminou sem uma conclusão útil validada."
    assert resultado["agente_status"] == "failed"
    assert resultado["roteador"]["fallback_pipeline"] == "visao_geral"
