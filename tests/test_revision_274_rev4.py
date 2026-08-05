import json
from types import SimpleNamespace

import engine.agent as agent_mod
from engine.structured_claims import normalize_structured_claims
from engine.task_contract import (
    build_task_contract,
    evaluate_intent_coverage,
)
from engine.work_summary import construir_resumo_trabalho


def test_task_intent_analysis_does_not_request_recommendations():
    contract = build_task_contract("Faça uma analise do projeto", "project_audit")
    assert contract["intent"] == "analyze"
    assert contract["response_profile"] == "code_analysis"
    assert contract["write_allowed"] is False
    assert contract["recommendations_requested"] is False
    assert contract["requested_outputs"] == [
        "plain_language_summary",
        "main_behavior",
        "important_components",
        "component_relationships",
        "verified_limitations",
    ]


def test_task_intent_review_preserves_requested_recommendation_count():
    contract = build_task_contract(
        "Analise o projeto e me diga 10 melhorias", "project_audit"
    )
    assert contract["intent"] == "review"
    assert contract["recommendations_requested"] is True
    assert contract["recommendation_count"] == 10
    assert "recommendations" in contract["requested_outputs"]


def test_intent_gate_rejects_unsolicited_recommendations():
    contract = build_task_contract("Faça uma analise do projeto", "project_audit")
    coverage = evaluate_intent_coverage(
        contract,
        [
            {
                "type": "fact",
                "text": "O projeto cria uma aplicação Flask.",
                "evidence_ids": ["ev-1"],
                "basis": "",
                "output": "analysis",
            },
            {
                "type": "recommendation",
                "text": "Recomendo adicionar novas rotas.",
                "evidence_ids": [],
                "basis": "A aplicação ainda não possui endpoints.",
                "output": "recommendations",
            },
        ],
        limitations=["Análise estática."],
    )
    assert coverage["ok"] is False
    assert coverage["failure_code"] == "UNSOLICITED_RECOMMENDATIONS"


def test_intent_gate_accepts_analysis_without_recommendations():
    contract = build_task_contract("Faça uma analise do projeto", "project_audit")
    coverage = evaluate_intent_coverage(
        contract,
        [
            {
                "type": "fact",
                "text": "Este projeto é uma aplicação web simples criada com Flask.",
                "evidence_ids": ["ev-1"],
                "basis": "",
                "output": "plain_language_summary",
            },
            {
                "type": "fact",
                "text": "Ao ser executada, a aplicação inicia um servidor HTTP e responde pelas rotas registradas.",
                "evidence_ids": ["ev-1"],
                "basis": "",
                "output": "main_behavior",
            },
            {
                "type": "fact",
                "text": "O arquivo app.py contém a instância Flask e os manipuladores de rota.",
                "evidence_ids": ["ev-1"],
                "basis": "",
                "output": "important_components",
            },
            {
                "type": "fact",
                "text": "Os manipuladores são registrados na instância Flask por decoradores de rota.",
                "evidence_ids": ["ev-1"],
                "basis": "",
                "output": "component_relationships",
            },
        ],
        limitations=["O comportamento em runtime não foi executado."],
    )
    assert coverage["ok"] is True
    assert coverage["unsolicited_recommendations"] is False


def test_absence_claim_requires_explicit_scope():
    claims, error = normalize_structured_claims([{
        "type": "absence",
        "text": "Não foram encontradas rotas Flask.",
        "evidence_ids": ["ev-1"],
        "basis": "",
    }])
    assert claims is None
    assert "exige scope" in error

    claims, error = normalize_structured_claims([{
        "type": "absence",
        "text": "Não foram encontradas rotas Flask.",
        "evidence_ids": ["ev-1"],
        "basis": "",
        "scope": "arquivos de código inventariados e lidos",
        "output": "verified_limitations",
    }])
    assert error is None
    assert claims[0]["type"] == "absence"
    assert claims[0]["scope"] == "arquivos de código inventariados e lidos"


def test_deterministic_write_receipt_uses_verified_edit_state():
    state = SimpleNamespace(edit_state={
        "status": "tests_passed",
        "arquivo": "calc.py",
        "linha_inicio": 1,
        "linha_fim_final": 2,
        "codigo_novo_preview": "def soma(a, b):\n    return a + b\n",
        "test": {"executed": True, "ok": True, "detail": "Ran 2 tests - OK"},
        "post_write_evidence_id": "ev-2",
    })
    text = agent_mod._conclusao_deterministica_edicao(state)
    assert "Alteração aplicada em calc.py" in text
    assert "Símbolo alterado: soma" in text
    assert "testes executados e aprovados" in text
    assert "arquivo relido" in text
    assert "Status final: concluído" in text


def test_expandable_summary_exposes_task_intent():
    evento = {"tipo": "pergunta", "texto": "Faça uma analise do projeto"}
    resultado = {
        "agente_status": "success",
        "agente_conclusao": {
            "mode": "analyze",
            "task_intent": {
                "intent": "analyze",
                "response_profile": "code_analysis",
                "write_allowed": False,
                "recommendations_requested": False,
                "requested_outputs": ["analysis", "verified_limitations"],
            },
        },
    }
    summary = construir_resumo_trabalho(evento, resultado, 1.2)
    fields = {item["label"]: item["value"] for item in summary["steps"][0]["fields"]}
    assert fields["Intenção detectada"] == "analyze"
    assert fields["Perfil de resposta"] == "code_analysis"
    assert fields["Escrita permitida"] == "não"
    assert fields["Recomendações solicitadas"] == "não"


def test_post_write_ready_generates_final_without_llm():
    state = SimpleNamespace(
        acoes_executadas=4,
        goal_state={"task_type": "project_write", "plan": []},
        edit_state={
            "status": "tests_passed",
            "arquivo": "calc.py",
            "linha_inicio": 1,
            "linha_fim_final": 2,
            "codigo_novo_preview": "def soma(a, b):\n    return a + b\n",
            "test": {"executed": True, "ok": True, "detail": "OK"},
            "post_write_evidence_id": "ev-2",
        },
    )
    decision = agent_mod._acao_obrigatoria_goal_state(
        state,
        "Corrija soma",
        {"agent": {"deterministic_post_write_enabled": True, "deterministic_write_receipt_enabled": True}},
    )
    assert decision["_system_deterministic_write_final"] is True
    assert decision["final"]["evidence_ids"] == ["ev-2"]
    assert "Alteração aplicada em calc.py" in decision["final"]["answer"]
