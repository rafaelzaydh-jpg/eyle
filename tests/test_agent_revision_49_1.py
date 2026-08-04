#!/usr/bin/env python3
"""Regressoes da revisao corretiva 49.1."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod  # noqa: E402


def _config(max_steps=5):
    return {
        "agent": {
            "rollout_mode": "full",
            "trusted_project_paths": ["/tmp"],
            "enabled_modes": ["analyze", "suggest", "edit"],
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


def _llm(*decisoes):
    respostas = iter(decisoes)
    return lambda *args, **kwargs: next(respostas)


def test_analise_geral_nao_aceita_sem_contexto_e_le_o_projeto(tmp_path, monkeypatch):
    (tmp_path / "audio.py").write_text("valor = 1\n", encoding="utf-8")
    chamadas = []
    respostas = iter([
        '{"needs_user":"Nenhum contexto do projeto esta disponivel."}',
        '{"needs_user":"Qual arquivo devo analisar?"}',
        json.dumps({
            "tool": "read_range",
            "arguments": {
                "caminho_relativo": "audio.py", "linha_inicio": 1, "linha_fim": 1,
            },
        }),
        json.dumps({
            "final": {
                "resposta": "audio.py:1 foi analisado",
                "evidence_ids": ["ev-0001"],
                "verificacao": "codigo lido do disco",
                "limitacoes": [],
            },
        }),
    ])

    def fake_llm(*args, **kwargs):
        chamadas.append(args[0])
        return next(respostas)

    monkeypatch.setattr(agent_mod, "executar_agente_llm", fake_llm)
    status, texto, pendente, detalhes = agent_mod.executar_agente(
        "Faca uma analise do projeto",
        _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
        modo="analyze",
    )

    assert status == "success"
    assert texto == "audio.py:1 foi analisado"
    assert pendente is None
    assert detalhes["tools_called"] == ["list_tree", "read_range"]
    assert detalhes["read_status"] == "read"
    assert len(chamadas) == 4
    assert "PREMATURE_NEEDS_USER" in chamadas[2]


def test_arquivo_explicito_tambem_exige_tentativa_de_leitura(tmp_path, monkeypatch):
    (tmp_path / "audio.py").write_text("valor = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        agent_mod,
        "executar_agente_llm",
        _llm(
            '{"needs_user":"nao recebi o codigo"}',
            json.dumps({
                "tool": "read_range",
                "arguments": {
                    "caminho_relativo": "audio.py", "linha_inicio": 1, "linha_fim": 1,
                },
            }),
            '{"final":{"resposta":"ok audio.py:1","evidence_ids":["ev-0001"],"verificacao":"lido","limitacoes":[]}}',
        ),
    )

    status, texto, _, detalhes = agent_mod.executar_agente(
        "analise audio.py",
        _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
        modo="analyze",
    )

    assert status == "success"
    assert texto == "ok audio.py:1"
    assert detalhes["tools_called"] == ["read_range"]


def test_bloqueio_real_de_leitura_ainda_pode_pedir_ajuda(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent_mod,
        "executar_agente_llm",
        _llm(
            json.dumps({
                "tool": "read_range",
                "arguments": {
                    "caminho_relativo": "ausente.py", "linha_inicio": 1, "linha_fim": 1,
                },
            }),
            '{"needs_user":"O arquivo ausente.py nao existe; informe o alvo correto."}',
        ),
    )

    status, texto, pendente, detalhes = agent_mod.executar_agente(
        "analise ausente.py",
        _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
        modo="analyze",
    )

    assert status == "needs_user"
    assert "nao existe" in texto
    assert pendente["continuation_kind"] == "user_input"
    assert detalhes["tools_called"] == ["read_range"]
    assert detalhes["read_status"] == "read_failed"
