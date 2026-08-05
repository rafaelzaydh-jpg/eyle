#!/usr/bin/env python3
"""Criterios de aceite das Atualizacoes 42 e 43."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod  # noqa: E402
from engine.agent_state import AgentState  # noqa: E402
from engine.agent_tools import TOOLS, gerar_catalogo_tools  # noqa: E402
from engine.compiler import montar_prompt_agente  # noqa: E402
from engine.context_engine import estimar_tokens  # noqa: E402
from llm.executar import PROMPT_AGENTE  # noqa: E402


def _config(max_steps=8):
    return {
        "llm": {
            "context_window_tokens": 4080,
            "max_tokens": 700,
        },
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "max_recent_observations": 4,
        },
        "agent": {
            "max_steps": max_steps,
            "max_tentativas_parse": 1,
            "max_chars_por_observacao": 500,
            "max_erros_consecutivos": 3,
            "max_fatos_importantes": 10,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
            "exigir_run_tests_apos_escrita": True,
        },
    }


def _resultado_leitura(conteudo="     1 | valor = 1", content_hash="a" * 64):
    return {
        "status": "success",
        "ok": True,
        "executed": True,
        "changed": False,
        "error_code": None,
        "detail": {
            "arquivo": "audio.py",
            "linha_inicio": 1,
            "linha_fim": 1,
            "trecho_numerado": conteudo,
            "content_hash": content_hash,
        },
    }


def test_prompt_com_evidencia_respeita_janela_4080():
    config = _config()
    estado = AgentState(config)
    estado.definir_objetivo("analise audio.py", "project_read")
    conteudo = "\n".join(f"{i:>6} | valor_{i} = {i}" for i in range(1, 700))
    estado.registrar_acao("read_range", {
        "caminho_relativo": "audio.py", "linha_inicio": 1, "linha_fim": 699,
    }, _resultado_leitura(conteudo=conteudo))

    prompt = montar_prompt_agente(
        "analise audio.py",
        goal_state=estado.goal_state,
        evidencias=estado.evidence,
        actions=estado.actions,
        catalogo_tools=gerar_catalogo_tools(TOOLS, config),
        config=config,
        system_prompt=PROMPT_AGENTE,
    )
    total = (
        estimar_tokens(PROMPT_AGENTE, 3)
        + estimar_tokens(prompt, 3)
        + config["llm"]["max_tokens"]
        + config["context_engine"]["safety_margin_tokens"]
    )
    assert total <= 4080
    assert "EVIDENCE ev-0001" in prompt
    assert len(estado.evidence[0]["conteudo"]) > 500


def test_evidencia_do_passo_1_sobrevive_ao_passo_6_e_a_persistencia():
    config = _config()
    estado = AgentState(config)
    estado.definir_objetivo("analise audio.py", "project_read")
    acao = estado.registrar_acao(
        "read_range",
        {"caminho_relativo": "audio.py", "linha_inicio": 1, "linha_fim": 1},
        _resultado_leitura(),
    )
    for numero in range(6):
        estado.observar("list_tree", {"passo": numero})
    reidratado = AgentState.from_dict(json.loads(json.dumps(estado.to_dict())), config)

    prompt = montar_prompt_agente(
        "analise audio.py",
        observacoes=reidratado.observacoes,
        goal_state=reidratado.goal_state,
        evidencias=reidratado.evidence,
        actions=reidratado.actions,
        config=config,
        system_prompt=PROMPT_AGENTE,
    )
    assert acao["evidence_ids"] == ["ev-0001"]
    assert reidratado.evidence[0]["content_hash"] == "a" * 64
    assert "EVIDENCE ev-0001" in prompt
    assert len(reidratado.observacoes) == 6


def test_final_de_projeto_no_primeiro_passo_e_recusado_ate_ler_codigo(tmp_path, monkeypatch):
    (tmp_path / "audio.py").write_text("valor = 1\n", encoding="utf-8")
    respostas = iter([
        '{"final":"resposta sem abrir arquivo"}',
        json.dumps({
            "tool": "read_range",
            "arguments": {"caminho_relativo": "audio.py", "linha_inicio": 1, "linha_fim": 1},
        }),
        json.dumps({
            "final": {
                "resposta": "audio.py:1 foi analisado",
                "evidence_ids": ["ev-0001"],
                "verificacao": "faixa lida",
                "limitacoes": [],
            }
        }),
    ])
    chamadas = []

    def fake_llm(prompt, config):
        chamadas.append(prompt)
        return next(respostas)

    monkeypatch.setattr(agent_mod, "executar_agente_llm", fake_llm)
    status, texto, pendente, detalhes = agent_mod.executar_agente(
        "analise audio.py", _config(max_steps=3),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )

    assert status == "success"
    assert "audio.py:1" in texto
    assert pendente is None
    assert detalhes["task_type"] == "project_read"
    assert detalhes["evidence_ids"] == ["ev-0001"]
    assert len(chamadas) == 3
    assert "final recusado sem grounding" in chamadas[1]


def test_metadados_sozinhos_nao_passam_como_analise_de_codigo(tmp_path, monkeypatch):
    (tmp_path / "audio.py").write_text("valor = 1\n", encoding="utf-8")
    respostas = iter([
        json.dumps({"tool": "read_metadata", "arguments": {"caminho_relativo": "audio.py"}}),
        '{"final":"conclui usando so metadados"}',
        json.dumps({
            "tool": "read_range",
            "arguments": {"caminho_relativo": "audio.py", "linha_inicio": 1, "linha_fim": 1},
        }),
        '{"final":{"resposta":"Em audio.py:1, o trecho define `valor` com o valor `1`.","evidence_ids":["ev-0001"],"verificacao":"lido","limitacoes":[]}}',
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))

    status, texto, _, detalhes = agent_mod.executar_agente(
        "explique audio.py", _config(max_steps=4),
        entendimento={"arquivos": {"audio.py": {"responsabilidade": "audio"}}},
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )

    assert status == "success"
    assert texto == "Em audio.py:1, o trecho define `valor` com o valor `1`."
    assert detalhes["evidence_ids"] == ["ev-0001"]


def test_hash_antigo_e_rejeitado_e_a_mesma_faixa_pode_ser_relida(tmp_path, monkeypatch):
    arquivo = tmp_path / "audio.py"
    arquivo.write_text("valor = 1\n", encoding="utf-8")
    respostas = iter([
        json.dumps({
            "tool": "read_range",
            "arguments": {"caminho_relativo": "audio.py", "linha_inicio": 1, "linha_fim": 1},
        }),
        json.dumps({
            "final": {
                "resposta": "Em audio.py:1, o trecho define `valor` com o valor `1`.",
                "evidence_ids": ["ev-0001"],
                "verificacao": "nenhuma",
                "limitacoes": [],
            }
        }),
        json.dumps({
            "tool": "read_range",
            "arguments": {"caminho_relativo": "audio.py", "linha_inicio": 1, "linha_fim": 1},
        }),
        json.dumps({
            "final": {
                "resposta": "Em audio.py:1, o trecho define `valor` com o valor `2`.",
                "evidence_ids": ["ev-0002"],
                "verificacao": "hash fresco",
                "limitacoes": [],
            }
        }),
    ])
    chamada = {"numero": 0}

    def fake_llm(prompt, config):
        chamada["numero"] += 1
        if chamada["numero"] == 2:
            arquivo.write_text("valor = 2\n", encoding="utf-8")
        return next(respostas)

    monkeypatch.setattr(agent_mod, "executar_agente_llm", fake_llm)
    status, texto, _, detalhes = agent_mod.executar_agente(
        "analise audio.py", _config(max_steps=4),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )

    assert status == "success"
    assert texto == "Em audio.py:1, o trecho define `valor` com o valor `2`."
    assert detalhes["evidence_ids"] == ["ev-0002"]
    assert chamada["numero"] == 4


def test_evidence_id_inexistente_e_rejeitada(tmp_path, monkeypatch):
    (tmp_path / "audio.py").write_text("valor = 1\n", encoding="utf-8")
    respostas = iter([
        json.dumps({
            "tool": "read_range",
            "arguments": {"caminho_relativo": "audio.py", "linha_inicio": 1, "linha_fim": 1},
        }),
        json.dumps({
            "final": {
                "resposta": "Em audio.py:1, o trecho define `valor` com o valor `1`.",
                "evidence_ids": ["ev-9999"],
                "verificacao": "nenhuma",
                "limitacoes": [],
            }
        }),
        json.dumps({
            "final": {
                "resposta": "audio.py:1 com ID valido",
                "evidence_ids": ["ev-0001"],
                "verificacao": "hash fresco",
                "limitacoes": [],
            }
        }),
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))
    status, texto, _, detalhes = agent_mod.executar_agente(
        "analise audio.py", _config(max_steps=3),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert "define `valor`" in texto
    assert detalhes["evidence_ids"] == ["ev-0001"]
    assert detalhes["recovery_layer"] == "deterministic_analysis"


def test_citacao_fora_da_faixa_da_evidencia_e_rejeitada(tmp_path, monkeypatch):
    (tmp_path / "audio.py").write_text("valor = 1\n", encoding="utf-8")
    respostas = iter([
        json.dumps({
            "tool": "read_range",
            "arguments": {"caminho_relativo": "audio.py", "linha_inicio": 1, "linha_fim": 1},
        }),
        json.dumps({
            "final": {
                "resposta": "Em audio.py:99, o trecho define `valor` com o valor `1`.",
                "evidence_ids": ["ev-0001"],
                "verificacao": "hash fresco",
                "limitacoes": [],
            }
        }),
        json.dumps({
            "final": {
                "resposta": "a resposta esta em audio.py:1",
                "evidence_ids": ["ev-0001"],
                "verificacao": "hash e faixa frescos",
                "limitacoes": [],
            }
        }),
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))
    status, texto, _, detalhes = agent_mod.executar_agente(
        "analise audio.py", _config(max_steps=3),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert "Em audio.py:1" in texto
    assert detalhes["evidence_ids"] == ["ev-0001"]
    assert detalhes["recovery_layer"] == "deterministic_analysis"
