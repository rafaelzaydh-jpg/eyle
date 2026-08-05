#!/usr/bin/env python3
"""Revisao 55.20: cobertura real, divulgacao honesta e regressao completa."""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod
from engine.analysis_coverage import (
    evaluate_project_audit_coverage,
    render_coverage_disclosure,
)
from engine.agent_state import AgentState
from engine.compiler import montar_prompt_agente
from engine.structured_claims import validate_health_claims


def _inventory(paths, complete=True):
    entries = []
    directories = set()
    for path in paths:
        parts = path.split("/")
        for index in range(1, len(parts)):
            directories.add("/".join(parts[:index]))
        entries.append({
            "caminho": path,
            "tipo": "arquivo",
            "profundidade": path.count("/") + 1,
        })
    entries.extend({
        "caminho": path,
        "tipo": "diretorio",
        "profundidade": path.count("/") + 1,
    } for path in sorted(directories))
    entries.sort(key=lambda item: (item["caminho"].count("/"), item["caminho"], item["tipo"]))
    return {
        "schema_version": 1,
        "inventory_hash": "a" * 64,
        "entradas": entries,
        "total_retornado": len(entries),
        "total_arquivos": len(paths),
        "total_diretorios": len(directories),
        "truncado": not complete,
        "varredura_completa": complete,
    }


def _evidence(path, content, evidence_id, complete=True):
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


def _config():
    return {
        "agent": {
            "max_steps": 8,
            "max_tentativas_parse": 2,
            "max_no_progress_decisions": 2,
            "max_erros_consecutivos": 3,
            "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
            "exigir_run_tests_apos_escrita": True,
            "enabled_modes": ["analyze", "suggest", "edit"],
            "rollout_mode": "read_only",
            "task_deadline_seconds": 30,
            "max_llm_calls": 12,
            "max_total_generated_tokens": 12000,
            "semantic_grounding": {"enabled": False},
            "audit_candidate_limit": 48,
            "audit_initial_read_limit": 6,
            "audit_gap_read_limit": 1,
            "audit_health_claim_required_score": 1.0,
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
            "audit_scout_max_tokens": 700,
            "audit_finalizer_max_tokens": 1400,
        },
    }


def test_arvore_grande_preserva_engine_llm_e_tests_no_prompt():
    paths = ["engine/agent.py", "llm/executar.py", "tests/test_agent.py"]
    paths.extend(f"pkg/mod_{index:03d}.py" for index in range(140))
    inventory = _inventory(paths)
    prompt = montar_prompt_agente(
        "Faça a análise do projeto",
        project_inventory=inventory,
        config={
            "llm": {"context_window_tokens": 65536, "max_tokens": 1500},
            "context_engine": {"chars_per_token_fallback": 3, "safety_margin_tokens": 256},
        },
    )
    assert "F engine/agent.py" in prompt
    assert "F llm/executar.py" in prompt
    assert "F tests/test_agent.py" in prompt
    assert "F pkg/mod_139.py" in prompt


def test_readme_sozinho_nao_conclui_auditoria_geral():
    coverage = evaluate_project_audit_coverage(
        _inventory(["README.md", "main.py", "engine/agent.py", "tests/test_agent.py"]),
        [_evidence("README.md", "# Projeto\n", "ev-0001")],
    )
    assert coverage["failure_code"] == "SOURCE_CODE_NOT_ANALYZED"
    assert coverage["coverage"]["code_files_read"] == 0
    assert coverage["coverage"]["level"] == "none"


def test_uma_evidencia_nao_satisfaz_projeto_grande():
    inventory = _inventory([
        "main.py", "engine/agent.py", "engine/worker.py", "engine/grounding.py",
        "tests/test_agent.py", "pytest.ini",
    ])
    coverage = evaluate_project_audit_coverage(
        inventory,
        [_evidence("main.py", "from engine.agent import run\n", "ev-0001")],
        coverage_reported=True,
        grounded_answer=True,
    )
    assert coverage["passed"] is False
    assert coverage["criteria"]["entrypoint_read"] is True
    assert coverage["criteria"]["core_logic_read"] is False
    assert coverage["coverage"]["code_files_read"] == 1
    assert coverage["coverage"]["level"] == "partial"


def test_nenhum_problema_critico_e_bloqueado_sem_prova():
    claims = [{
        "type": "fact",
        "text": "Não existem problemas críticos no sistema.",
        "evidence_ids": ["ev-0001"],
        "basis": "",
    }]
    coverage = {"criteria": {
        "inventory_complete": True,
        "entrypoint_read": True,
        "core_logic_read": True,
        "error_paths_read": True,
        "tests_or_test_config_checked": True,
        "coverage_reported": False,
        "grounded_answer": False,
    }}
    result = validate_health_claims(claims, coverage, [])
    assert result["failure_code"] == "UNSUPPORTED_HEALTH_CLAIM"


def test_294_testes_passando_e_bloqueado_sem_run_tests():
    claims = [{
        "type": "fact",
        "text": "294 testes estão passando.",
        "evidence_ids": ["ev-0001"],
        "basis": "",
    }]
    result = validate_health_claims(claims, {"criteria": {}}, [])
    assert result["failure_code"] == "TEST_STATUS_NOT_VERIFIED"


def test_release_antiga_pode_ser_citada_como_historico_documental():
    claims = [{
        "type": "fact",
        "text": "A revisão 55.13 registrava 294 testes passando.",
        "evidence_ids": ["ev-doc"],
        "basis": "",
    }]
    evidence = [_evidence(
        "docs/releases/55.13.md",
        "A revisão 55.13 registrava 294 testes passando.\n",
        "ev-doc",
    )]
    result = validate_health_claims(
        claims, {"criteria": {}}, [], evidence=evidence,
    )
    assert result["ok"] is True
    assert result["tests_executed"] is False


def test_cobertura_real_targeted_conta_componentes_testes_e_docs_usados():
    inventory = _inventory([
        "main.py", "engine/agent.py", "engine/worker.py", "engine/grounding.py",
        "engine/queue.py", "tests/test_agent.py", "README.md", "pytest.ini",
    ])
    evidence = [
        _evidence("main.py", "from engine.agent import run\n", "ev-1"),
        _evidence("engine/agent.py", "def run():\n    try:\n        return 1\n    except Exception:\n        return 0\n", "ev-2"),
        _evidence("engine/worker.py", "def work():\n    return True\n", "ev-3"),
        _evidence("tests/test_agent.py", "def test_run():\n    assert True\n", "ev-4"),
        _evidence("README.md", "# histórico\n", "ev-5"),
    ]
    pipeline = {
        "catalog": {
            "required_slots": [
                {"role": "entrypoints", "path": "main.py"},
                {"role": "orchestrators", "path": "engine/agent.py"},
            ],
            "candidates": [
                {"path": "main.py", "roles": ["entrypoints"]},
                {"path": "engine/agent.py", "roles": ["orchestrators", "core_logic"]},
                {"path": "engine/worker.py", "roles": ["orchestrators", "grounding_recovery_validation"]},
            ],
        },
        "initial_scout": {
            "selected_paths": ["main.py", "engine/agent.py", "engine/worker.py"]
        },
        "gap_scout": {"selected_paths": []},
    }
    coverage = evaluate_project_audit_coverage(
        inventory,
        evidence,
        coverage_reported=True,
        grounded_answer=True,
        audit_pipeline=pipeline,
        actions=[],
        selected_evidence_ids=["ev-1", "ev-2", "ev-5"],
    )
    metrics = coverage["coverage"]
    assert metrics == {
        "inventory_complete": True,
        "code_files_total": 5,
        "code_files_read": 3,
        "code_files_fully_read": 3,
        "critical_components_total": 3,
        "critical_components_read": 3,
        "tests_executed": False,
        "tests_passed": False,
        "test_run_attempts": 0,
        "docs_used": 1,
        "level": "targeted",
    }
    disclosure = render_coverage_disclosure(coverage, "Faça a análise do projeto")
    assert disclosure.startswith("Análise direcionada concluída.")
    assert "Foram revisados 3 componentes críticos." in disclosure
    assert "Os testes não foram executados." in disclosure
    assert "Não é possível afirmar ausência total de bugs." in disclosure


def test_projeto_pequeno_e_medido_como_cobertura_integral():
    coverage = evaluate_project_audit_coverage(
        _inventory(["app.py"]),
        [_evidence("app.py", "value = 1\n", "ev-1")],
        coverage_reported=True,
        grounded_answer=True,
        selected_evidence_ids=["ev-1"],
    )
    assert coverage["passed"] is True
    assert coverage["coverage"]["level"] == "complete"
    assert coverage["coverage"]["code_files_total"] == 1
    assert coverage["coverage"]["code_files_read"] == 1
    assert coverage["coverage"]["critical_components_read"] == 1


def test_resposta_integrada_recebe_divulgacao_de_cobertura(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")

    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", lambda *args: json.dumps({
        "final": {
            "answer": "plano",
            "selected_paths": ["app.py"],
            "risk_hypotheses": [],
            "gaps": [],
        }
    }))
    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", lambda *args: json.dumps({
        "final": {
            "claims": [{
                "type": "fact",
                "text": "app.py define a variável value como 1.",
                "evidence_ids": ["ev-0001"],
                "basis": "",
            }],
            "verification": "arquivo lido",
            "limitations": [],
        }
    }))

    status, text, _, details = agent_mod.executar_agente(
        "Faça a análise do projeto",
        _config(),
        entendimento={},
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert text.startswith("Cobertura integral dos arquivos de código inventariados concluída.")
    assert "Os testes não foram executados." in text
    assert text.rstrip().endswith("app.py define a variável value como 1.")
    assert details["analysis_coverage"]["coverage"]["level"] == "complete"
    assert details["coverage"]["level"] == "complete"
    assert details["coverage_disclosure"] in text
