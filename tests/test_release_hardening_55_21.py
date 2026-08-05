#!/usr/bin/env python3
"""Revisao 55.21: health global, latest tests, recovery e metricas honestas."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod
from engine.analysis_coverage import (
    detect_response_language,
    evaluate_project_audit_coverage,
    render_coverage_disclosure,
)
from engine.response_recovery import recover_structured_audit_claims
from engine.roteador import pede_auditoria_projeto
from engine.structured_claims import validate_health_claims
from engine.test_execution import latest_test_execution


def _coverage(passed=True):
    return {"criteria": {
        "inventory_complete": passed,
        "entrypoint_read": passed,
        "core_logic_read": passed,
        "error_paths_read": passed,
        "tests_or_test_config_checked": passed,
        "coverage_reported": False,
        "grounded_answer": False,
    }}


def _evidence(path="app.py", content="value = 1\n", evidence_id="ev-0001"):
    return {
        "id": evidence_id,
        "estado": "fresh",
        "arquivo": path,
        "linha_inicio": 1,
        "linha_fim": max(1, len(content.splitlines())),
        "leitura_completa": True,
        "conteudo_raw": content,
        "conteudo": content,
    }


def _inventory(paths):
    return {
        "varredura_completa": True,
        "truncado": False,
        "inventory_hash": "inv-55-21",
        "entradas": [
            {"tipo": "arquivo", "caminho": path}
            for path in paths
        ],
    }


def _config():
    return {
        "agent": {
            "enabled": True,
            "rollout_mode": "read_only",
            "enabled_modes": ["analyze", "suggest"],
            "max_steps": 8,
            "max_no_progress_decisions": 3,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "audit_candidate_limit": 48,
            "audit_initial_read_limit": 6,
            "audit_gap_read_limit": 1,
            "audit_health_claim_required_score": 1.0,
            "semantic_grounding": {"enabled": True},
            "response_recovery": {"llm_enabled": False},
        },
        "llm": {
            "context_window_tokens": 8192,
            "max_tokens": 1500,
            "agent_max_tokens": 512,
            "audit_scout_max_tokens": 700,
            "audit_finalizer_max_tokens": 1400,
        },
        "context_engine": {
            "chars_per_token_fallback": 3,
            "safety_margin_tokens": 100,
            "max_recent_observations": 4,
        },
    }


def test_health_global_continua_bloqueado_com_cobertura_e_testes():
    claims = [{
        "type": "fact",
        "text": "Não existem problemas críticos no sistema.",
        "evidence_ids": ["ev-0001"],
        "basis": "",
    }]
    result = validate_health_claims(claims, _coverage(), [{
        "tool": "run_tests", "executed": True, "ok": True,
    }])
    assert result["failure_code"] == "UNSUPPORTED_HEALTH_CLAIM"
    assert result["reason"] == "global_health_claim_not_allowed"


def test_health_limitado_ao_escopo_revisado_pode_passar():
    claims = [{
        "type": "fact",
        "text": "Não foram identificados problemas críticos nos componentes revisados.",
        "evidence_ids": ["ev-0001"],
        "basis": "",
    }]
    assert validate_health_claims(claims, _coverage(), [])["ok"] is True


def test_ultima_execucao_de_testes_e_a_fonte_de_verdade():
    actions = [
        {"tool": "run_tests", "executed": True, "ok": True},
        {"tool": "run_tests", "executed": True, "ok": False, "error_code": "TEST_FAILED"},
    ]
    execution = latest_test_execution(actions)
    assert execution["executed"] is True
    assert execution["passed"] is False
    assert execution["attempts"] == 2

    claims = [{
        "type": "fact",
        "text": "Todos os testes passaram.",
        "evidence_ids": ["ev-0001"],
        "basis": "",
    }]
    result = validate_health_claims(claims, _coverage(), actions)
    assert result["failure_code"] == "TEST_STATUS_NOT_VERIFIED"
    assert result["tests_executed"] is True


def test_recovery_de_auditoria_devolve_claims_estruturadas():
    recovered = recover_structured_audit_claims(
        "Faça a análise do projeto",
        [_evidence(content="value = 1\n")],
        {}, cause="utility_gate_failed", allow_llm=False,
    )
    assert recovered["ok"] is True
    assert recovered["layer"] == "deterministic_structured_claims"
    assert recovered["claims"]
    assert recovered["claims"][0]["evidence_ids"] == ["ev-0001"]
    assert "claim_annotations" not in recovered


def test_pipeline_integrado_recupera_claim_inutil_sem_contrato_antigo(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", lambda *args: json.dumps({
        "final": {"selected_paths": ["app.py"], "risk_hypotheses": [], "gaps": []}
    }))
    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", lambda *args: json.dumps({
        "final": {
            "claims": [{
                "type": "fact",
                "text": "Há detalhes relevantes observados.",
                "evidence_ids": ["ev-0001"],
                "basis": "",
            }],
            "verification": "arquivo lido",
            "limitations": [],
        }
    }))
    status, text, _, details = agent_mod.executar_agente(
        "Faça a análise do projeto", _config(), entendimento={},
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "`value`" in text
    assert details["recovery_layer"] == "deterministic_structured_claims"
    assert details["claims"]


def test_ingles_e_detectado_sem_artigo_a_fingir_portugues():
    assert detect_response_language("Analyze a project") == "en"
    disclosure = render_coverage_disclosure({"coverage": {
        "level": "targeted",
        "critical_components_read": 2,
        "code_files_read": 2,
        "code_files_total": 10,
        "tests_executed": False,
        "tests_passed": False,
    }}, "Analyze a project")
    assert disclosure.startswith("Targeted analysis completed.")
    assert pede_auditoria_projeto("Analyze the project") is True
    assert pede_auditoria_projeto("Review the project") is True


def test_componentes_auxiliares_nao_inflam_metrica_critica():
    inventory = _inventory([
        "main.py", "engine/agent.py", "utils/colors.py", "utils/format.py", "pytest.ini",
    ])
    evidence = [
        _evidence("main.py", "from engine.agent import run\n", "ev-1"),
        _evidence("engine/agent.py", "def run():\n    try:\n        return 1\n    except Exception:\n        return 0\n", "ev-2"),
        _evidence("utils/colors.py", "RED = 1\n", "ev-3"),
        _evidence("utils/format.py", "def fmt():\n    return 'x'\n", "ev-4"),
        _evidence("pytest.ini", "[pytest]\n", "ev-5"),
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
                {"path": "utils/colors.py", "roles": []},
                {"path": "utils/format.py", "roles": []},
            ],
        },
        "initial_scout": {"selected_paths": [
            "main.py", "engine/agent.py", "utils/colors.py", "utils/format.py",
        ]},
        "gap_scout": {"selected_paths": []},
    }
    coverage = evaluate_project_audit_coverage(
        inventory, evidence, coverage_reported=True, grounded_answer=True,
        audit_pipeline=pipeline, selected_evidence_ids=["ev-1", "ev-2"],
    )
    assert coverage["coverage"]["code_files_read"] == 4
    assert coverage["coverage"]["critical_components_read"] == 2


def test_documentacao_ativa_nao_se_apresenta_como_revisao_53():
    root = Path(__file__).resolve().parents[1]
    technical = (root / "docs" / "technical-overview.md").read_text(encoding="utf-8")
    benchmark = (root / "docs" / "benchmark.md").read_text(encoding="utf-8")
    assert "Revisão:** 53.0-speed-cycle-hardening" not in technical
    assert "Packaging result for release 2.7.3 revision 53" not in benchmark
