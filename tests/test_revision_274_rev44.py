import json

import engine.agent as agent_mod
from engine.task_contract import build_task_contract, evaluate_intent_coverage


def _claim(index, *, errors=None):
    return {
        "claim_index": index,
        "errors": list(errors or []),
        "warnings": [],
    }


def test_code_analysis_optional_sections_do_not_block_adherence():
    contract = build_task_contract("Faça a análise do projeto", "project_audit")
    claims = [
        {
            "type": "fact",
            "text": "Este projeto é uma aplicação web Flask.",
            "evidence_ids": ["ev-1"],
            "output": "plain_language_summary",
        },
        {
            "type": "fact",
            "text": "Ao executar, o servidor responde às rotas registradas.",
            "evidence_ids": ["ev-1"],
            "output": "main_behavior",
        },
    ]
    result = evaluate_intent_coverage(contract, claims, limitations=[])
    assert result["ok"] is True
    assert result["missing_outputs"] == []
    assert "important_components" in result["missing_optional_outputs"]
    assert "component_relationships" in result["missing_optional_outputs"]


def test_code_analysis_still_blocks_when_main_behavior_is_missing():
    contract = build_task_contract("Faça a análise do projeto", "project_audit")
    claims = [{
        "type": "fact",
        "text": "Este projeto é uma aplicação web Flask.",
        "evidence_ids": ["ev-1"],
        "output": "plain_language_summary",
    }]
    result = evaluate_intent_coverage(contract, claims, limitations=[])
    assert result["ok"] is False
    assert result["failure_code"] == "INTENT_OUTPUTS_NOT_COVERED"
    assert result["missing_outputs"] == ["main_behavior"]


def test_grounding_may_remove_optional_claim_without_losing_intent(tmp_path, monkeypatch):
    from tests.test_project_audit_55_17 import _config

    (tmp_path / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/')\n"
        "def index():\n"
        "    return {'status': 'ok'}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", lambda prompt, config: json.dumps({
        "final": {
            "answer": "plano",
            "selected_paths": ["app.py"] if "SCOUT PHASE: initial" in prompt else [],
            "risk_hypotheses": [],
            "gaps": [],
            "rationale": "ler a aplicação",
        }
    }))

    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", lambda prompt, config: json.dumps({
        "final": {
            "claims": [
                {
                    "type": "fact",
                    "text": "Este projeto é uma aplicação web Flask simples.",
                    "evidence_ids": ["ev-0001"],
                    "basis": "",
                    "output": "plain_language_summary",
                },
                {
                    "type": "fact",
                    "text": "Quando executada, a aplicação responde à rota GET / com um objeto JSON de status.",
                    "evidence_ids": ["ev-0001"],
                    "basis": "",
                    "output": "main_behavior",
                },
                {
                    "type": "fact",
                    "text": "O arquivo app.py contém a instância Flask e a função index.",
                    "evidence_ids": ["ev-0001"],
                    "basis": "",
                    "output": "important_components",
                },
                {
                    "type": "inference",
                    "text": "A função index está conectada a várias camadas externas invisíveis.",
                    "evidence_ids": ["ev-0001"],
                    "basis": "suposição não sustentada",
                    "output": "component_relationships",
                },
            ],
            "verification": "app.py lido",
            "limitations": [],
        }
    }))

    calls = {"count": 0}

    def grounding(answer, evidence, config, claim_annotations=None):
        calls["count"] += 1
        annotations = list(claim_annotations or [])
        if len(annotations) >= 4:
            return {
                "ok": False,
                "typed": True,
                "errors": ["claim 4 unsupported"],
                "warnings": [],
                "claims": [
                    _claim(1), _claim(2), _claim(3),
                    _claim(4, errors=["unsupported inference"]),
                ],
            }
        return {
            "ok": True,
            "typed": True,
            "errors": [],
            "warnings": [],
            "claims": [_claim(i + 1) for i in range(len(annotations))],
        }

    monkeypatch.setattr(agent_mod, "verify_conclusion", grounding)
    cfg = _config()
    cfg["agent"].update({
        "intent_output_gate_enabled": True,
        "semantic_grounding": {"enabled": True},
    })

    status, text, _, details = agent_mod.executar_agente(
        "Faça a análise do projeto",
        cfg,
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )

    assert status == "success"
    assert text.startswith("Este projeto é uma aplicação web Flask simples.")
    assert "responde à rota GET /" in text
    assert "camadas externas invisíveis" not in text
    assert details["intent_coverage"]["ok"] is True
    assert "component_relationships" in details["intent_coverage"]["missing_optional_outputs"]
    assert details["recovery_layer"] == "structured_claim_filter"
    assert calls["count"] >= 2
