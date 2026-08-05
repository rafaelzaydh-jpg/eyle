#!/usr/bin/env python3
"""Revision 55.14: unified response recovery pipeline."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import agent as agent_mod
from engine.config_schema import ConfigError, validar_config
from engine.evidence_registry import EvidenceRegistry
from engine.grounding import build_safe_grounded_answer, verify_conclusion
from engine.response_recovery import build_deterministic_analysis
from engine.utility_gate import validate_response_utility
from engine.work_summary import construir_resumo_trabalho
from llm.executar import ErroLLM
from llm.response_adapter import normalize_model_response


def _agent_config(max_steps=3):
    return {
        "agent": {
            "rollout_mode": "full",
            "trusted_project_paths": ["/tmp"],
            "enabled_modes": ["analyze", "suggest", "edit"],
            "max_steps": max_steps,
            "max_no_progress_decisions": 2,
            "max_tentativas_parse": 1,
            "max_erros_consecutivos": 3,
            "max_chars_por_observacao": 2000,
            "max_fatos_importantes": 10,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
            "exigir_run_tests_apos_escrita": True,
            "response_recovery": {
                "llm_enabled": False,
                "unstructured_retry": True,
                "evidence_short_generation": True,
                "deterministic_fallback": True,
            },
        }
    }


def _evidence(content="valor = 1\n"):
    return [{
        "id": "ev-0001",
        "source_tool": "read_range",
        "arquivo": "app.py",
        "linha_inicio": 1,
        "linha_fim": 1,
        "total_linhas_arquivo": 1,
        "leitura_completa": True,
        "truncado": False,
        "conteudo_raw": content,
        "conteudo": "     1 | " + content.strip(),
        "content_hash": "a" * 64,
        "file_hash": "b" * 64,
        "estado": "fresh",
    }]


def test_adapter_normaliza_content_reasoning_stream_partial_e_texto_puro():
    assert normalize_model_response({"content": "ok"}).content == "ok"
    assert normalize_model_response({"reasoning_content": "pensando"}).reasoning_content == "pensando"

    stream = "\n".join([
        'data: {"choices":[{"delta":{"content":"Oi"}}]}',
        'data: {"choices":[{"delta":{"content":" mundo"}}]}',
        "data: [DONE]",
    ])
    normalized = normalize_model_response(stream)
    assert normalized.streaming is True
    assert normalized.usable_text() == "Oi mundo"

    partial = normalize_model_response('{"message":{"content":"resposta incompleta')
    assert partial.partial_json is True
    assert partial.content == "resposta incompleta"

    plain = normalize_model_response("texto puro")
    assert plain.usable_text() == "texto puro"


def test_gate_rejeita_recibo_e_aceita_conclusao_real():
    receipt = "Evidências verificadas: app.py:1-9"
    assert validate_response_utility(receipt, "analise app.py", evidence=_evidence())["ok"] is False

    useful = (
        "O projeto é uma aplicação Flask simples. "
        "Ela cria o servidor e usa uma porta configurável por variável de ambiente."
    )
    assert validate_response_utility(
        useful,
        "analise app.py",
        evidence=_evidence("from flask import Flask\napp = Flask(__name__)\n"),
    )["ok"] is True


def test_grounding_sem_claim_suportada_nao_fabrica_recibo():
    answer = "A função chama `os.remove` (app.py:1)."
    result = verify_conclusion(answer, _evidence())
    assert result["ok"] is False
    assert build_safe_grounded_answer(answer, result, _evidence()) == ""


def test_fallback_deterministico_produz_analise_util_e_grounded():
    evidence = _evidence("valor = 1\n")
    answer = build_deterministic_analysis("analise app.py", evidence)
    assert "define `valor`" in answer
    assert validate_response_utility(answer, "analise app.py", evidence=evidence)["ok"] is True
    assert verify_conclusion(answer, evidence)["ok"] is True


def test_empty_model_response_depois_da_leitura_recupera_sem_success_vazio(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("valor = 1\n", encoding="utf-8")
    calls = {"n": 0}

    def fake_llm(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({
                "tool": "read_range",
                "arguments": {"caminho_relativo": "app.py", "linha_inicio": 1, "linha_fim": 1},
            })
        raise ErroLLM("empty", error_code="EMPTY_MODEL_RESPONSE", transient=False)

    monkeypatch.setattr(agent_mod, "executar_agente_llm", fake_llm)
    status, text, pending, details = agent_mod.executar_agente(
        "analise app.py",
        _agent_config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )

    assert status == "success"
    assert pending is None
    assert "define `valor`" in text
    assert details["recovery_layer"] == "deterministic_analysis"
    assert details["utility_gate"]["ok"] is True


def test_sem_conteudo_e_sem_evidencia_termina_failed(monkeypatch):
    monkeypatch.setattr(
        agent_mod,
        "executar_agente_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ErroLLM("empty", error_code="EMPTY_MODEL_RESPONSE", transient=False)
        ),
    )
    status, text, pending, details = agent_mod.executar_agente(
        "analise o projeto",
        _agent_config(max_steps=1),
        retornar_detalhes=True,
    )
    assert status == "failed"
    assert pending is None
    assert text.strip()
    assert details["completion_gate"]["passed"] is False


def test_registry_unico_alimenta_resumo_sem_evidencias_contraditorias():
    registry = EvidenceRegistry()
    registry.register(_evidence()[0], evidence_id="ev-0001", source_tool="read_range")
    snapshot = registry.public_snapshot()
    summary = construir_resumo_trabalho(
        {"tipo": "pergunta", "texto": "analise app.py"},
        {
            "agente_status": "success",
            "roteador": {"tipo": "agente", "modo": "analyze"},
            "agente_conclusao": {
                "tools_called": ["read_range"],
                "evidence_ids": ["ev-0001"],
                "evidence_registry": snapshot,
                "completion_gate": {"passed": True},
            },
        },
        0.1,
    )
    analysis_step = next(step for step in summary["steps"] if step["number"] == 3)
    labels = [field["label"] for field in analysis_step["fields"]]
    evidence_field = next(field for field in analysis_step["fields"] if field["label"] == "Evidências")
    assert labels.count("Evidências") == 1
    assert "ev-0001 = app.py:1" in evidence_field["value"]


def test_schema_valida_flags_da_recuperacao():
    validar_config({"agent": {"response_recovery": {"llm_enabled": True}}})
    with pytest.raises(ConfigError, match="response_recovery.llm_enabled"):
        validar_config({"agent": {"response_recovery": {"llm_enabled": "sim"}}})
