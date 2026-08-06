import json
from pathlib import Path

import engine.agent as agent_mod
from engine.information_preservation import (
    build_target_coverage_ledger,
    compare_release_coverage,
    evaluate_must_preserve_manifest,
    load_manifest,
)
from engine.structured_claims import normalize_structured_claims
from engine.task_contract import (
    build_task_contract,
    evaluate_intent_coverage,
    evaluate_target_coverage,
    render_claims_with_segments,
)

FIXTURE = Path(__file__).parent / "fixtures" / "information_preservation" / "flask_status"


def _evidence():
    content = (FIXTURE / "app.py").read_text(encoding="utf-8")
    return [{
        "id": "ev-0001",
        "arquivo": "app.py",
        "linha_inicio": 1,
        "linha_fim": len(content.splitlines()),
        "conteudo_raw": content,
        "conteudo": content,
        "estado": "fresh",
        "leitura_completa": True,
    }]


def _claims():
    raw = json.loads((FIXTURE / "expected_claims.json").read_text(encoding="utf-8"))
    claims, error = normalize_structured_claims(raw)
    assert error is None
    return claims


def _ledger(claims=None, rejected=None):
    contract = build_task_contract(
        "Analise o projeto e explique todas as rotas, os retornos, a porta e o modo debug.",
        "project_audit",
    )
    claims = claims or _claims()
    rendered = render_claims_with_segments(contract, claims)
    intent = evaluate_intent_coverage(contract, claims, limitations=[])
    target = evaluate_target_coverage(contract, claims, _evidence(), rendered["text"])
    return contract, rendered, build_target_coverage_ledger(
        contract, claims, _evidence(), rendered["segments"],
        target_coverage=target, intent_coverage=intent,
        rejected_claims=rejected or [],
    )


def test_claim_ids_are_stable_for_same_structured_information():
    first = _claims()
    second = _claims()
    assert [item["claim_id"] for item in first] == [item["claim_id"] for item in second]
    assert all(item["claim_id"].startswith("claim-") for item in first)


def test_renderer_keeps_claim_to_segment_mapping():
    contract, rendered, ledger = _ledger()
    assert rendered["text"].startswith("Este projeto é uma aplicação web Flask")
    assert all(item["claim_ids"] for item in rendered["segments"])
    assert ledger["gate"]["ok"] is True
    assert ledger["summary"]["claims_rendered"] == len(_claims())
    assert ledger["summary"]["silent_discards"] == 0


def test_required_and_essential_information_cannot_disappear():
    claims = [item for item in _claims() if item.get("output") != "main_behavior"]
    contract = build_task_contract("Faça a análise do projeto", "project_audit")
    rendered = render_claims_with_segments(contract, claims)
    intent = evaluate_intent_coverage(contract, claims, limitations=[])
    ledger = build_target_coverage_ledger(
        contract, claims, _evidence(), rendered["segments"],
        intent_coverage=intent,
    )
    assert ledger["gate"]["ok"] is False
    assert any(item["id"] == "output:main_behavior" for item in ledger["gate"]["blocking_targets"])


def test_optional_rejection_is_recorded_without_silent_failure():
    claims = _claims()[:2]
    rejected = [{
        "claim_id": "claim-rejected",
        "type": "fact",
        "text": "O arquivo app.py contém os componentes centrais.",
        "evidence_ids": ["ev-0001"],
        "output": "important_components",
        "errors": ["unsupported_objective_anchor"],
        "status": "rejected",
    }]
    contract = build_task_contract("Faça a análise do projeto", "project_audit")
    rendered = render_claims_with_segments(contract, claims)
    intent = evaluate_intent_coverage(contract, claims, limitations=[])
    ledger = build_target_coverage_ledger(
        contract, claims, _evidence(), rendered["segments"],
        intent_coverage=intent, rejected_claims=rejected,
    )
    assert ledger["gate"]["ok"] is True
    assert ledger["summary"]["claims_rejected"] == 1
    assert ledger["rejected_claims"][0]["importance"] == "optional"
    assert any(item["id"] == "output:important_components" for item in ledger["gate"]["optional_missing"])


def test_manifest_must_preserve_is_fully_covered():
    _, rendered, ledger = _ledger()
    manifest = load_manifest(str(FIXTURE / "manifest.json"))
    result = evaluate_must_preserve_manifest(manifest, ledger, rendered["text"])
    assert result["ok"] is True
    assert result["covered_count"] == result["total"] == 5


def test_release_comparison_blocks_coverage_regression():
    _, _, ledger = _ledger()
    baseline = {"runs": [{"papel": "principal", "resultados": [{
        "id": "fixture", "information_preservation": ledger,
    }]}]}
    regressed = json.loads(json.dumps(ledger))
    regressed["gate"]["ok"] = False
    regressed["summary"]["essential_covered"] = 1
    regressed["summary"]["silent_discards"] = 1
    candidate = {"runs": [{"papel": "principal", "resultados": [{
        "id": "fixture", "information_preservation": regressed,
    }]}]}
    result = compare_release_coverage(baseline, candidate)
    assert result["ok"] is False
    assert "preservation_gate_regressed" in result["regressions"][0]["reasons"]
    assert "silent_discards_increased" in result["regressions"][0]["reasons"]


def test_agent_exposes_information_ledger_in_details(tmp_path, monkeypatch):
    from tests.test_project_audit_55_17 import _config

    (tmp_path / "app.py").write_text((FIXTURE / "app.py").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", lambda prompt, config: json.dumps({
        "final": {
            "answer": "plan",
            "selected_paths": ["app.py"] if "SCOUT PHASE: initial" in prompt else [],
            "risk_hypotheses": [], "gaps": [], "rationale": "read app",
        }
    }))
    raw_claims = json.loads((FIXTURE / "expected_claims.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", lambda prompt, config: json.dumps({
        "final": {"claims": raw_claims, "verification": "app.py read", "limitations": []}
    }))
    cfg = _config()
    cfg["agent"].update({
        "intent_output_gate_enabled": True,
        "semantic_grounding": {"enabled": False},
    })
    status, text, _, details = agent_mod.executar_agente(
        "Faça a análise do projeto", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert text.startswith("Este projeto é uma aplicação web Flask")
    ledger = details["information_preservation"]
    assert ledger["gate"]["ok"] is True
    assert ledger["summary"]["essential_covered"] == ledger["summary"]["essential_total"]
    assert ledger["summary"]["silent_discards"] == 0


def test_rejected_essential_claim_without_replacement_is_a_silent_discard():
    claims = [_claims()[0]]
    rejected = [{
        "claim_id": "claim-main-behavior-rejected",
        "type": "fact",
        "text": "A aplicação responde pelas rotas registradas.",
        "evidence_ids": ["ev-0001"],
        "output": "main_behavior",
        "errors": ["unsupported_objective_anchor"],
        "status": "rejected",
    }]
    contract = build_task_contract("Faça a análise do projeto", "project_audit")
    rendered = render_claims_with_segments(contract, claims)
    intent = evaluate_intent_coverage(contract, claims, limitations=[])
    ledger = build_target_coverage_ledger(
        contract, claims, _evidence(), rendered["segments"],
        intent_coverage=intent, rejected_claims=rejected,
    )
    assert ledger["gate"]["ok"] is False
    assert ledger["summary"]["silent_discards"] == 1
    assert ledger["gate"]["rejected_essential_without_replacement"][0]["claim_id"] == "claim-main-behavior-rejected"
