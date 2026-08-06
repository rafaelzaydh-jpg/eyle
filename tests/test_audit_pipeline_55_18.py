#!/usr/bin/env python3
"""Revisao 55.18: selecao deterministica + Scout/Finalizer separados."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod
from engine.agent_state import AgentState
from engine.audit_pipeline import (
    build_audit_candidate_catalog,
    normalize_scout_selection,
)


def _inventory(paths):
    return {
        "inventory_hash": "f" * 64,
        "varredura_completa": True,
        "truncado": False,
        "entradas": [
            {"caminho": path, "tipo": "arquivo", "profundidade": path.count("/") + 1}
            for path in paths
        ],
    }


def _config():
    return {
        "agent": {
            "max_steps": 8,
            "max_tentativas_parse": 2,
            "max_no_progress_decisions": 3,
            "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
            "max_erros_consecutivos": 3,
            "exigir_run_tests_apos_escrita": True,
            "enabled_modes": ["analyze", "suggest", "edit"],
            "rollout_mode": "read_only",
            "task_deadline_seconds": 30,
            "max_llm_calls": 12,
            "max_total_generated_tokens": 12000,
            "semantic_grounding": {"enabled": False},
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "audit_candidate_limit": 48,
            "audit_initial_read_limit": 6,
            "audit_gap_read_limit": 1,
        },
        "dicas": {"max_chars_por_arquivo": 20000},
        "context_engine": {
            "chars_per_token_fallback": 3,
            "safety_margin_tokens": 100,
            "max_recent_observations": 4,
        },
        "llm": {
            "context_window_tokens": 16384,
            "max_tokens": 1500,
            "agent_max_tokens": 512,
            "audit_scout_max_tokens": 700,
            "audit_finalizer_max_tokens": 1600,
        },
    }


def test_catalogo_classifica_papeis_e_relaciona_testes():
    catalog = build_audit_candidate_catalog(_inventory([
        "main.py",
        "engine/agent.py",
        "engine/agent_state.py",
        "engine/persistencia.py",
        "engine/grounding.py",
        "engine/response_recovery.py",
        "tests/test_agent.py",
        "tests/test_grounding.py",
        "config.json",
        "README.md",
    ]))
    groups = catalog["groups"]
    assert groups["entrypoints"][0]["path"] == "main.py"
    assert groups["orchestrators"][0]["path"] == "engine/agent.py"
    assert any(item["path"] == "engine/agent_state.py" for item in groups["state_persistence"])
    assert any(item["path"] == "engine/grounding.py" for item in groups["grounding_recovery_validation"])
    assert groups["tests"][0]["path"] == "tests/test_agent.py"
    assert groups["configuration"][0]["path"] == "config.json"
    required_roles = {item["role"] for item in catalog["required_slots"]}
    assert {
        "entrypoints", "orchestrators", "tests", "configuration",
    } <= required_roles
    # Estado e grounding ficam como slots de aprofundamento do Scout.
    assert "state_persistence" not in required_roles
    assert "grounding_recovery_validation" not in required_roles


def test_scout_nao_consegue_inventar_caminho_nem_remover_slots_obrigatorios():
    catalog = build_audit_candidate_catalog(_inventory([
        "main.py", "engine/agent.py", "engine/agent_state.py",
        "engine/grounding.py", "tests/test_agent.py", "config.json",
    ]))
    selection = normalize_scout_selection({
        "final": {
            "answer": "plano",
            "selected_paths": ["README.md", "nao_existe.py", "engine/agent.py"],
        }
    }, catalog, limit=6)
    assert "README.md" in selection["rejected_paths"]
    assert "nao_existe.py" in selection["rejected_paths"]
    assert selection["selected_paths"][0] == "main.py"
    assert "engine/agent.py" in selection["selected_paths"]
    assert "tests/test_agent.py" in selection["selected_paths"]


def test_estado_persiste_fases_do_pipeline():
    state = AgentState(config=_config())
    state.audit_pipeline["phase"] = "reading_initial"
    state.audit_pipeline["pending_reads"] = ["main.py", "engine/agent.py"]
    restored = AgentState.from_dict(state.to_dict(), config=_config())
    assert restored.audit_pipeline["phase"] == "reading_initial"
    assert restored.audit_pipeline["pending_reads"] == ["main.py", "engine/agent.py"]


def test_project_audit_usa_scout_leitura_automatica_e_finalizer(monkeypatch, tmp_path):
    (tmp_path / "engine").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "main.py").write_text(
        "from engine.agent import run\n\nif __name__ == '__main__':\n    run()\n",
        encoding="utf-8",
    )
    (tmp_path / "engine" / "agent.py").write_text(
        "def run():\n    try:\n        return 'ok'\n    except Exception:\n        return 'failed'\n",
        encoding="utf-8",
    )
    (tmp_path / "engine" / "agent_state.py").write_text(
        "class State:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "engine" / "grounding.py").write_text(
        "def verify(value):\n    return bool(value)\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_agent.py").write_text(
        "from engine.agent import run\n\ndef test_run():\n    assert run() == 'ok'\n",
        encoding="utf-8",
    )
    (tmp_path / "config.json").write_text('{"enabled": true}\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("# release antiga\n", encoding="utf-8")

    scout_prompts = []
    finalizer_prompts = []

    def scout(prompt, config):
        scout_prompts.append(prompt)
        return json.dumps({
            "final": {
                "answer": "plano",
                "selected_paths": ["README.md", "engine/agent.py"],
                "risk_hypotheses": ["tratamento de excecao pode mascarar falhas"],
                "gaps": [],
                "rationale": "cobrir fluxo principal e validacao",
            }
        })

    def finalizer(prompt, config):
        finalizer_prompts.append(prompt)
        return json.dumps({
            "final": {
                "claims": [
                    {
                        "type": "fact",
                        "text": "main.py inicia o fluxo por engine/agent.py.",
                        "evidence_ids": ["ev-0001", "ev-0002"],
                        "basis": "",
                    },
                    {
                        "type": "fact",
                        "text": "O agente possui um caminho de exceção.",
                        "evidence_ids": ["ev-0002"],
                        "basis": "",
                    },
                    {
                        "type": "fact",
                        "text": "tests/test_agent.py verifica o retorno principal.",
                        "evidence_ids": ["ev-0005"],
                        "basis": "",
                    },
                ],
                "verification": "evidencias frescas selecionadas pelo pipeline",
                "limitations": ["Os testes foram lidos, mas não executados."],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", scout)
    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", finalizer)
    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        lambda *args: (_ for _ in ()).throw(AssertionError("agente monolitico nao deve ser usado")),
    )

    status, text, _, details = agent_mod.executar_agente(
        "Faça uma análise completa do projeto",
        _config(),
        entendimento={},
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )

    assert status == "success"
    assert "README.md" not in details["audit_pipeline"]["completed_reads"]
    assert details["tools_called"] == [
        "list_tree", "read_file", "read_file", "read_file",
        "read_file", "read_file", "read_file",
    ]
    assert details["audit_pipeline"]["phase"] == "completed"
    assert details["analysis_coverage"]["passed"] is True
    assert len(scout_prompts) == 0
    assert len(finalizer_prompts) == 1
    assert details["audit_pipeline"]["initial_scout"]["planner"] == "deterministic"
    assert details["audit_pipeline"]["gap_scout"]["planner"] == "deterministic"
    assert "No tools are available" in finalizer_prompts[0]
    assert "testes foram lidos" in details["limitacoes"][0]
    assert "main.py" in text


def test_finalizer_nao_pode_devolver_tool(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", lambda *args: json.dumps({
        "final": {"answer": "plano", "selected_paths": ["app.py"]}
    }))
    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", lambda *args: json.dumps({
        "tool": "read_file", "arguments": {"caminho_relativo": "app.py"}
    }))

    status, _, _, details = agent_mod.executar_agente(
        "Analise o projeto", _config(), entendimento={},
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] == "AUDIT_FINALIZER_INVALID_FORMAT"


def test_resumo_publico_mostra_pipeline_deterministico_finalizer():
    from engine.work_summary import construir_resumo_trabalho

    summary = construir_resumo_trabalho(
        {"tipo": "pergunta", "texto": "Analise o projeto"},
        {
            "agente_status": "success",
            "agente_conclusao": {
                "task_type": "project_audit",
                "mode": "analyze",
                "tools_called": ["list_tree", "read_file"],
                "audit_pipeline": {
                    "phase": "completed",
                    "initial_scout": {"selected_paths": ["main.py", "engine/agent.py"]},
                    "gap_scout": {"selected_paths": ["tests/test_agent.py"]},
                    "completed_reads": ["main.py", "engine/agent.py", "tests/test_agent.py"],
                    "failed_reads": [],
                    "finalizer_calls": 1,
                },
                "completion_gate": {"passed": True},
            },
        },
        2.0,
        projeto={"arquivos": 3},
    )
    fields = summary["steps"][2]["fields"]
    pipeline = next(item for item in fields if item["label"] == "Pipeline de auditoria")
    assert "planejamento determinístico" in pipeline["value"]
    assert "Finalizer" in pipeline["value"]
    assert "fase=completed" in pipeline["value"]
    assert "finalizer_calls=1" in pipeline["value"]
