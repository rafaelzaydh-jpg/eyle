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
    scouts = []

    def scout(prompt, config):
        scouts.append(prompt)
        return json.dumps({
            "final": {
                "answer": "plano",
                "selected_paths": ["audio.py"],
                "risk_hypotheses": [],
                "gaps": [],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", scout)
    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", lambda *args: json.dumps({
        "final": {
            "claims": [{
                "type": "fact",
                "text": "audio.py:1 define `valor` com o valor inteiro 1.",
                "evidence_ids": ["ev-0001"],
                "basis": "",
            }],
            "verification": "codigo lido do disco",
            "limitations": [],
        }
    }))
    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        lambda *args: (_ for _ in ()).throw(AssertionError("project_audit nao deve pedir contexto ao usuario")),
    )
    status, texto, pendente, detalhes = agent_mod.executar_agente(
        "Faca uma analise do projeto",
        _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
        modo="analyze",
    )

    assert status == "success"
    assert "define `valor`" in texto
    assert pendente is None
    assert detalhes["tools_called"] == ["list_tree", "read_file"]
    assert detalhes["read_status"] == "read"
    assert len(scouts) == 2

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
    assert "define `valor`" in texto
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
