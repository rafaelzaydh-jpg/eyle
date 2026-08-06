#!/usr/bin/env python3
"""Revisao 55.17: cobertura minima obrigatoria para analise geral."""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod
from engine.analysis_coverage import (
    PROJECT_AUDIT_CRITERIA,
    evaluate_project_audit_coverage,
)
from engine.agent import classificar_tarefa_agente
from engine.agent_state import AgentState
from engine.compiler import montar_prompt_agente
from engine.work_summary import construir_resumo_trabalho


def _config(max_no_progress=3):
    return {
        "agent": {
            "max_steps": 8,
            "max_tentativas_parse": 2,
            "max_no_progress_decisions": max_no_progress,
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
        },
        "dicas": {"max_chars_por_arquivo": 20000},
        "context_engine": {
            "chars_per_token_fallback": 3,
            "safety_margin_tokens": 100,
            "max_recent_observations": 4,
        },
        "llm": {
            "context_window_tokens": 8192,
            "max_tokens": 1500,
            "agent_max_tokens": 512,
        },
    }


def _evidence(path, content, evidence_id="ev-0001", complete=True):
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    lines = content.splitlines() or [""]
    return {
        "id": evidence_id,
        "source_tool": "read_file",
        "arquivo": path,
        "linha_inicio": 1,
        "linha_fim": len(lines),
        "total_linhas_arquivo": len(lines),
        "truncado": not complete,
        "leitura_completa": complete,
        "conteudo": content,
        "conteudo_raw": content,
        "content_hash": digest,
        "file_hash": digest,
        "estado": "fresh",
    }


def _inventory(paths, complete=True):
    return {
        "inventory_hash": "a" * 64,
        "entradas": [
            {"caminho": path, "tipo": "arquivo", "profundidade": path.count("/") + 1}
            for path in paths
        ],
        "total_retornado": len(paths),
        "total_arquivos": len(paths),
        "total_diretorios": 0,
        "truncado": not complete,
        "varredura_completa": complete,
    }


def test_analise_geral_vira_project_audit_mas_arquivo_explicito_nao():
    projeto = {"caminho_origem": "/tmp/projeto"}
    assert classificar_tarefa_agente(
        "Faça a análise do projeto", projeto=projeto, modo="analyze"
    ) == "project_audit"
    assert classificar_tarefa_agente(
        "Analise app.py", projeto=projeto, modo="analyze"
    ) == "project_read"


def test_documentacao_nao_satisfaz_leitura_de_codigo():
    coverage = evaluate_project_audit_coverage(
        _inventory(["README.md", "docs/architecture.md", "app.py"]),
        [_evidence("README.md", "# Projeto\n")],
    )
    assert coverage["evidence_only_contains_docs"] is True
    assert coverage["failure_code"] == "SOURCE_CODE_NOT_ANALYZED"
    assert coverage["criteria"]["entrypoint_read"] is False
    assert coverage["criteria"]["core_logic_read"] is False
    assert coverage["reads"]["source"] == []


def test_multiarquivo_exige_entrypoint_e_nucleo_distintos_e_teste():
    inventory = _inventory(["app.py", "engine/core.py", "tests/test_core.py", "README.md"])
    entry_only = evaluate_project_audit_coverage(
        inventory, [_evidence("app.py", "from engine.core import run\n")]
    )
    assert entry_only["criteria"]["entrypoint_read"] is True
    assert entry_only["criteria"]["core_logic_read"] is False
    assert entry_only["minimum_code_files_required"] == 2
    assert "engine/core.py" in entry_only["next_read_candidates"]

    core_too = evaluate_project_audit_coverage(
        inventory,
        [
            _evidence("app.py", "from engine.core import run\n", "ev-0001"),
            _evidence("engine/core.py", "def run():\n    return 1\n", "ev-0002"),
        ],
    )
    assert core_too["criteria"]["core_logic_read"] is True
    assert core_too["criteria"]["error_paths_read"] is True
    assert core_too["criteria"]["tests_or_test_config_checked"] is False
    assert "tests/test_core.py" in core_too["next_read_candidates"]


def test_goal_state_declara_os_sete_criterios():
    estado = AgentState(config={})
    estado.definir_objetivo("Analise o projeto", "project_audit", modo="analyze")
    assert estado.goal_state["success_criteria"] == PROJECT_AUDIT_CRITERIA
    assert "documentacao_nao_conta_como_codigo" in estado.goal_state["constraints"]
    assert len(estado.goal_state["plan"]) == 5


def test_prompt_publica_cobertura_calculada_e_regra_de_documentacao():
    coverage = evaluate_project_audit_coverage(
        _inventory(["app.py", "engine/core.py"]),
        [_evidence("app.py", "from engine.core import run\n")],
    )
    prompt = montar_prompt_agente(
        "Faça a análise do projeto",
        goal_state={"task_type": "project_audit", "mode": "analyze"},
        project_inventory=_inventory(["app.py", "engine/core.py"]),
        analysis_coverage=coverage,
        config=_config(),
    )
    assert "PROJECT AUDIT COVERAGE" in prompt
    assert "README, CHANGELOG and docs/**" in prompt
    assert "core_logic_read" in prompt
    assert "engine/core.py" in prompt


def test_fluxo_auditoria_le_entrypoint_nucleo_e_testes_antes_do_final(monkeypatch, tmp_path):
    (tmp_path / "engine").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "app.py").write_text(
        "from engine.core import run\n\nif __name__ == '__main__':\n    run()\n",
        encoding="utf-8",
    )
    (tmp_path / "engine" / "core.py").write_text(
        "def run():\n    return 'ok'\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_core.py").write_text(
        "from engine.core import run\n\ndef test_run():\n    assert run() == 'ok'\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Antigo\n", encoding="utf-8")

    scout_calls = []
    finalizer_calls = []

    def scout(prompt, config):
        scout_calls.append(prompt)
        return json.dumps({
            "final": {
                "answer": "plano de auditoria",
                "selected_paths": [],
                "risk_hypotheses": [],
                "gaps": [],
                "rationale": "usar os candidatos obrigatorios",
            }
        })

    def finalizer(prompt, config):
        finalizer_calls.append(prompt)
        return json.dumps({
            "final": {
                "claims": [
                    {
                        "type": "fact",
                        "text": "O entrypoint app.py importa e executa run.",
                        "evidence_ids": ["ev-0001"],
                        "basis": "",
                    },
                    {
                        "type": "fact",
                        "text": "engine/core.py define run.",
                        "evidence_ids": ["ev-0002"],
                        "basis": "",
                    },
                    {
                        "type": "fact",
                        "text": "tests/test_core.py verifica que run retorna 'ok'.",
                        "evidence_ids": ["ev-0003"],
                        "basis": "",
                    },
                ],
                "verification": "fontes lidas",
                "limitations": [],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", scout)
    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", finalizer)
    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        lambda *args: (_ for _ in ()).throw(AssertionError("agent monolitico nao deve redigir project_audit")),
    )
    status, text, _, details = agent_mod.executar_agente(
        "Faça a análise do projeto",
        _config(),
        entendimento={},
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )

    assert status == "success"
    assert details["task_type"] == "project_audit"
    assert details["tools_called"] == [
        "list_tree", "read_file", "read_file", "read_file",
    ]
    assert details["analysis_coverage"]["passed"] is True
    assert all(details["analysis_coverage"]["criteria"].values())
    assert details["analysis_coverage"]["reads"]["documentation"] == []
    assert len(scout_calls) == 0
    assert len(finalizer_calls) == 1
    assert details["audit_pipeline"]["initial_scout"]["planner"] == "deterministic"
    assert "FINALIZER CONTRACT" in finalizer_calls[0]
    assert "engine/core.py" in text


def test_projeto_somente_documentacao_falha_fechado(monkeypatch, tmp_path):
    (tmp_path / "README.md").write_text("# Projeto sem codigo\n", encoding="utf-8")
    monkeypatch.setattr(
        agent_mod, "executar_audit_scout_llm",
        lambda *args: (_ for _ in ()).throw(AssertionError("scout nao deve rodar sem codigo-fonte")),
    )

    status, _, _, details = agent_mod.executar_agente(
        "Faça a análise do projeto",
        _config(max_no_progress=1),
        entendimento={},
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )

    assert status == "failed"
    assert details["failure_code"] == "SOURCE_CODE_NOT_ANALYZED"
    assert details["analysis_coverage"]["inventory"]["source_files"] == 0
    assert details["analysis_coverage"]["reads"]["documentation"] == []


def test_resumo_publico_mostra_cobertura_minima():
    coverage = evaluate_project_audit_coverage(
        _inventory(["app.py"]),
        [_evidence("app.py", "print('ok')\n")],
        coverage_reported=True,
        grounded_answer=True,
    )
    summary = construir_resumo_trabalho(
        {"tipo": "pergunta", "texto": "Analise o projeto"},
        {
            "agente_status": "success",
            "agente_conclusao": {
                "task_type": "project_audit",
                "mode": "analyze",
                "tools_called": ["list_tree", "read_file"],
                "analysis_coverage": coverage,
                "completion_gate": {"passed": True},
            },
        },
        1.0,
        projeto={"arquivos": 1},
    )
    analysis_fields = summary["steps"][2]["fields"]
    coverage_field = next(item for item in analysis_fields if item["label"] == "Cobertura real")
    assert "7/7 critérios" in coverage_field["value"]
    assert "código=1/1" in coverage_field["value"]
