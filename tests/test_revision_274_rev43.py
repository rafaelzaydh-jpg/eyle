import json

import engine.agent as agent_mod
from engine.task_contract import (
    build_task_contract,
    evaluate_intent_coverage,
    render_claims_for_response,
)


def test_code_analysis_contract_requires_human_understanding_sections():
    contract = build_task_contract("Faça a análise do projeto", "project_audit")
    assert contract["response_profile"] == "code_analysis"
    assert contract["requested_outputs"] == [
        "plain_language_summary",
        "main_behavior",
        "important_components",
        "component_relationships",
        "verified_limitations",
    ]
    assert contract["required_outputs"] == [
        "plain_language_summary",
        "main_behavior",
    ]
    assert contract["optional_outputs"] == [
        "important_components",
        "component_relationships",
        "verified_limitations",
    ]
    assert contract["response_sections"] == contract["requested_outputs"]


def test_profile_renderer_places_summary_first_and_limitations_last():
    contract = build_task_contract("Faça a análise do projeto", "project_audit")
    claims = [
        {
            "type": "absence",
            "text": "Não foram encontrados testes automatizados no inventário analisado.",
            "evidence_ids": ["ev-1"],
            "basis": "",
            "scope": "inventário completo",
            "output": "verified_limitations",
        },
        {
            "type": "fact",
            "text": "As funções index e health são registradas na instância Flask por decoradores de rota.",
            "evidence_ids": ["ev-1"],
            "basis": "",
            "output": "component_relationships",
        },
        {
            "type": "fact",
            "text": "Este projeto é uma aplicação web Flask que funciona como um serviço simples de status.",
            "evidence_ids": ["ev-1"],
            "basis": "",
            "output": "plain_language_summary",
        },
        {
            "type": "fact",
            "text": "As rotas GET / e GET /health retornam o objeto JSON de status.",
            "evidence_ids": ["ev-1"],
            "basis": "",
            "output": "important_components",
        },
        {
            "type": "fact",
            "text": "Quando o servidor está ativo, outro sistema pode consultar as rotas para confirmar que o serviço responde.",
            "evidence_ids": ["ev-1"],
            "basis": "",
            "output": "main_behavior",
        },
    ]
    text = render_claims_for_response(contract, claims)
    assert text.startswith("Este projeto é uma aplicação web Flask")
    assert text.index("Quando o servidor") < text.index("As rotas GET")
    assert text.index("As rotas GET") < text.index("As funções index")
    assert text.rstrip().endswith("inventário analisado.")
    assert "\n\n" in text


def test_intent_gate_requires_only_essential_code_analysis_sections():
    contract = build_task_contract("Faça a análise do projeto", "project_audit")
    claims = [{
        "type": "fact",
        "text": "Este projeto é uma aplicação web Flask.",
        "evidence_ids": ["ev-1"],
        "basis": "",
        "output": "plain_language_summary",
    }]
    result = evaluate_intent_coverage(contract, claims, limitations=[])
    assert result["ok"] is False
    assert "main_behavior" in result["missing_outputs"]
    assert "important_components" not in result["missing_outputs"]
    assert "important_components" in result["missing_optional_outputs"]


def test_project_analysis_main_answer_is_human_and_audit_data_stays_in_details(tmp_path, monkeypatch):
    from tests.test_project_audit_55_17 import _config

    (tmp_path / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/')\n"
        "def index():\n"
        "    return {'status': 'ok'}\n\n"
        "@app.route('/health')\n"
        "def health():\n"
        "    return {'status': 'ok'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", lambda prompt, config: json.dumps({
        "final": {
            "answer": "plano",
            "selected_paths": ["app.py"] if "SCOUT PHASE: initial" in prompt else [],
            "risk_hypotheses": [],
            "gaps": [],
            "rationale": "ler o código da aplicação",
        }
    }))

    def finalizer(prompt, config):
        assert "plain_language_summary" in prompt
        assert "The first claim must not be about coverage" in agent_mod.PROMPT_AUDIT_FINALIZER
        return json.dumps({
            "final": {
                "claims": [
                    {
                        "type": "absence",
                        "text": "Não foram encontrados testes automatizados no inventário analisado.",
                        "evidence_ids": ["ev-0001"],
                        "basis": "",
                        "scope": "inventário completo do projeto",
                        "output": "verified_limitations",
                    },
                    {
                        "type": "fact",
                        "text": "As funções index e health são conectadas à instância Flask pelos decoradores de rota.",
                        "evidence_ids": ["ev-0001"],
                        "basis": "",
                        "output": "component_relationships",
                    },
                    {
                        "type": "fact",
                        "text": "Este projeto é uma pequena aplicação web Flask que atua como um serviço de status.",
                        "evidence_ids": ["ev-0001"],
                        "basis": "",
                        "output": "plain_language_summary",
                    },
                    {
                        "type": "fact",
                        "text": "O arquivo app.py contém a instância Flask e as rotas GET / e GET /health, que retornam {'status': 'ok'}.",
                        "evidence_ids": ["ev-0001"],
                        "basis": "",
                        "output": "important_components",
                    },
                    {
                        "type": "fact",
                        "text": "Enquanto o servidor está em execução, um navegador ou outro sistema pode consultar as duas rotas para verificar que o serviço responde.",
                        "evidence_ids": ["ev-0001"],
                        "basis": "",
                        "output": "main_behavior",
                    },
                ],
                "verification": "app.py lido por completo",
                "limitations": [],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", finalizer)
    cfg = _config()
    cfg["agent"].update({
        "intent_output_gate_enabled": True,
        "semantic_grounding": {"enabled": False},
    })
    status, text, _, details = agent_mod.executar_agente(
        "Faça a análise do projeto",
        cfg,
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert text.startswith("Este projeto é uma pequena aplicação web Flask")
    assert "GET /" in text and "GET /health" in text
    assert "Cobertura integral" not in text
    assert text.rstrip().endswith("inventário analisado.")
    assert details["coverage_disclosure"].startswith("Cobertura integral")
    assert details["intent_coverage"]["ok"] is True
