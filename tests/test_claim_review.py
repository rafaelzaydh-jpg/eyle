from __future__ import annotations

import json

import eyle.core.agent as core_agent
from tests.canonical import base_config, review, claim


def test_one_global_claim_review_can_accept_grounded_final_without_investigation(monkeypatch, tmp_path):
    agent_calls = []
    claim_calls = []

    def fake_agent(prompt, _config):
        payload = json.loads(prompt)
        agent_calls.append(payload)
        if len(agent_calls) == 1:
            return {
                "tool_calls": [{"tool": "count_tokens", "arguments": {}}],
                "investigation_updates": [],
            }
        evidence_ids = list((payload.get("latest_tool_results") or [{}])[0].get("source_record_ids") or [])
        return {
            "final": {"answer": "O projeto foi medido.", "limitations": [], "evidence_ids": evidence_ids},
            "investigation_updates": [],
        }

    def fake_claim(prompt, _config):
        payload = json.loads(prompt)
        claim_calls.append(payload)
        evidence_ref = payload["evidence"][0]["ref"]; evidence_id = evidence_ref.split(":", 1)[1]
        return review(claims=[claim(evidence_ids=[evidence_id], reason="The measurement supports the answer.")])

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_claim)
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    status, _, _, details = core_agent.executar_agente(
        "Meça o projeto.", base_config(claims_mode="self_check"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert len(agent_calls) == 2
    assert len(claim_calls) == 1
    assert details["investigation"] == []
    assert claim_calls[0]["investigation"] == []
    assert len(claim_calls[0]["evidence"]) == 1


def test_scope_gap_with_null_target_never_creates_runtime_target(monkeypatch, tmp_path):
    agent_calls = []
    claim_calls = []

    def fake_agent(prompt, _config):
        payload = json.loads(prompt)
        agent_calls.append(payload)
        if len(agent_calls) == 1:
            return {
                "tool_calls": [{"tool": "project_stats", "arguments": {}}],
                "investigation_updates": [],
            }
        if len(agent_calls) == 2:
            return {
                "final": {"answer": "Conclusão ampla prematura.", "limitations": [], "evidence_ids": []},
                "investigation_updates": [],
            }
        # Main LLM, not Runtime, chooses to declare the debt after Claim feedback.
        return {
            "needs_user": {
                "question": "A investigação material precisa continuar. Qual informação devo considerar?",
                "missing_information": "A concrete user-supplied fact required to continue the material investigation",
            },
            "investigation_updates": [{
                "id": "T1", "goal": "Establish active reachability", "status": "open", "evidence_ids": [], "reason": ""
            }],
        }

    def fake_claim(prompt, _config):
        claim_calls.append(json.loads(prompt))
        return review(
            semantic_gaps=[{"type": "scope_gap", "target_id": None, "grounding_refs": ["request"], "required_property": "Active-flow reachability", "reason": "Active-flow reachability was not established."}],
            material_status="gap", material_reason="Material scope is missing.",
        )

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_claim)
    cfg = base_config(claims_mode="self_check")
    cfg["agent"]["max_llm_turns"] = 4
    status, _, _, details = core_agent.executar_agente(
        "Isso participa do runtime?", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert len(claim_calls) == 1
    assert details["investigation"] == [{
        "id": "T1", "goal": "Establish active reachability", "status": "open", "evidence_ids": [], "reason": ""
    }]
    claim_events = [
        item for item in details["decision_history"]
        if item.get("decision") == "claim_review" and item.get("outcome") == "insufficient"
    ]
    assert claim_events[-1]["required_properties"] == ["Active-flow reachability"]
