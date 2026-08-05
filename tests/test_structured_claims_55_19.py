#!/usr/bin/env python3
"""Revisao 55.19: claims estruturadas, health gates e memoria como pista."""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod
from engine.agent_state import AgentState
from engine.compiler import bloco_entendimento, montar_prompt_finalizer_auditoria
from engine.structured_claims import (
    claims_to_annotations,
    normalize_structured_claims,
    render_claims,
    validate_health_claims,
)


def _coverage(value=True):
    return {
        "criteria": {
            "inventory_complete": value,
            "entrypoint_read": value,
            "core_logic_read": value,
            "error_paths_read": value,
            "tests_or_test_config_checked": value,
            "coverage_reported": False,
            "grounded_answer": False,
        }
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
        },
        "context_engine": {
            "chars_per_token_fallback": 3,
            "safety_margin_tokens": 100,
            "max_recent_observations": 4,
        },
        "llm": {
            "context_window_tokens": 8192,
            "max_tokens": 1200,
            "agent_max_tokens": 512,
            "audit_scout_max_tokens": 700,
            "audit_finalizer_max_tokens": 1400,
        },
    }


def test_claims_nascem_com_evidencia_e_sistema_renderiza_texto():
    claims, error = normalize_structured_claims([
        {
            "type": "fact",
            "text": "O Worker registra falhas de execução.",
            "evidence_ids": ["ev-0004"],
            "basis": "",
        },
        {
            "type": "risk",
            "text": "Esse fluxo pode mascarar a causa original da falha.",
            "evidence_ids": ["ev-0004", "ev-0007"],
            "basis": "A exceção é convertida em um status genérico.",
        },
    ])
    assert error is None
    assert render_claims(claims) == (
        "O Worker registra falhas de execução.\n"
        "Esse fluxo pode mascarar a causa original da falha."
    )
    annotations = claims_to_annotations(claims)
    assert annotations[0]["type"] == "fact"
    assert annotations[1]["type"] == "inference"
    assert annotations[1]["evidence_ids"] == ["ev-0004", "ev-0007"]


def test_claim_atomica_rejeita_paragrafo_com_duas_afirmacoes():
    claims, error = normalize_structured_claims([{
        "type": "fact",
        "text": "O Worker inicia. O Worker conclui.",
        "evidence_ids": ["ev-0001"],
        "basis": "",
    }])
    assert claims is None
    assert "mais de uma afirmacao" in error


def test_project_audit_rejeita_contrato_antigo_answer_annotations():
    state = AgentState(config=_config())
    conclusion, error = agent_mod._normalizar_conclusao({
        "final": {
            "answer": "Texto livre antigo.",
            "evidence_ids": ["ev-0001"],
            "claim_annotations": [],
        }
    }, state, "project_audit")
    assert conclusion is None
    assert "claims" in error


def test_testes_passando_exige_run_tests_executado_com_sucesso():
    claim = [{
        "type": "fact",
        "text": "Todos os testes estão passando.",
        "evidence_ids": ["ev-0001"],
        "basis": "",
    }]
    blocked = validate_health_claims(claim, _coverage(), [])
    assert blocked["failure_code"] == "TEST_STATUS_NOT_VERIFIED"

    allowed = validate_health_claims(claim, _coverage(), [{
        "tool": "run_tests", "executed": True, "ok": True,
    }])
    assert allowed["ok"] is True


def test_saude_geral_exige_cobertura_e_prova_operacional():
    claim = [{
        "type": "fact",
        "text": "Não existem problemas críticos no sistema.",
        "evidence_ids": ["ev-0001"],
        "basis": "",
    }]
    low_coverage = validate_health_claims(claim, _coverage(False), [])
    assert low_coverage["failure_code"] == "UNSUPPORTED_HEALTH_CLAIM"
    assert low_coverage["coverage_score"] == 0

    no_run = validate_health_claims(claim, _coverage(True), [])
    assert no_run["failure_code"] == "UNSUPPORTED_HEALTH_CLAIM"

    allowed = validate_health_claims(claim, _coverage(True), [{
        "tool": "run_tests", "executed": True, "ok": True,
    }])
    assert allowed["ok"] is True


def test_memoria_indexada_e_pista_e_hash_define_selo(tmp_path):
    target = tmp_path / "engine.py"
    content = "def run():\n    return 1\n"
    target.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    understanding = {
        "arquivos": {
            "engine.py": {
                "responsabilidade": "orquestra o fluxo",
                "hash": digest,
            }
        }
    }
    trusted = "\n".join(bloco_entendimento(
        understanding, projeto={"caminho_origem": str(tmp_path)},
    ))
    assert "UNTRUSTED NAVIGATION HINT" in trusted
    assert "HASH_VERIFIED_NAVIGATION_FACT" in trusted
    assert "final claims still require fresh Evidence Registry IDs" in trusted

    target.write_text("def run():\n    return 2\n", encoding="utf-8")
    stale = "\n".join(bloco_entendimento(
        understanding, projeto={"caminho_origem": str(tmp_path)},
    ))
    assert "[UNTRUSTED_NAVIGATION_HINT] engine.py" in stale
    assert "[HASH_VERIFIED_NAVIGATION_FACT] engine.py" not in stale


def test_prompt_finalizer_pede_claims_e_proibe_answer():
    prompt = montar_prompt_finalizer_auditoria(
        "Analise o projeto",
        analysis_coverage=_coverage(),
        project_inventory={"varredura_completa": True, "truncado": False},
        evidencias=[],
        config=_config(),
    )
    assert "atomic claims" in prompt
    assert "Do not return answer or claim_annotations" in prompt
    assert "JSON claims envelope" in prompt


def test_fluxo_bloqueia_health_claim_sem_run_tests(monkeypatch, tmp_path):
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
                "text": "Não existem problemas críticos no sistema.",
                "evidence_ids": ["ev-0001"],
                "basis": "",
            }],
            "verification": "arquivo lido",
            "limitations": [],
        }
    }))

    status, _, _, details = agent_mod.executar_agente(
        "Faça uma análise do projeto",
        _config(),
        entendimento={},
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] == "UNSUPPORTED_HEALTH_CLAIM"
    assert details["health_claim_gate"]["tests_executed"] is False
