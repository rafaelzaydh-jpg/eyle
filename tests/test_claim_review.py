from __future__ import annotations

import json

import eyle.core.agent as core_agent
from tests.canonical import base_config, review, issue, agent_tools, agent_final, agent_needs_user, tool_call


def test_one_global_claim_review_can_accept_grounded_final_without_investigation(monkeypatch, tmp_path):
    agent_calls = []
    claim_calls = []
    def fake_agent(prompt, _config):
        payload = json.loads(prompt); agent_calls.append(payload)
        if len(agent_calls) == 1:
            return agent_tools(tool_call("count_tokens", {}))
        grounding_ids = list((payload.get("latest_tool_results") or [{}])[0].get("grounding_ids") or [])
        return agent_final({"answer": "O projeto foi medido.", "grounding_ids": grounding_ids})
    def fake_claim(prompt, _config):
        payload = json.loads(prompt); claim_calls.append(payload)
        return review(verdict="accept")
    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_claim)
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    status, _, _, details = core_agent.executar_agente(
        "Meça o projeto.", base_config(claims_mode="self_check"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert len(agent_calls) == 2 and len(claim_calls) == 1
    assert details["investigation"] == []
    assert "investigation" not in claim_calls[0]
    assert len(claim_calls[0]["observed_material"]) == 1

def test_scope_gap_with_null_target_never_creates_runtime_target(monkeypatch, tmp_path):
    agent_calls = []
    claim_calls = []
    def fake_agent(prompt, _config):
        payload = json.loads(prompt); agent_calls.append(payload)
        if len(agent_calls) == 1:
            return agent_tools(tool_call("project_stats", {}))
        if len(agent_calls) == 2:
            return agent_final("Conclusão ampla prematura.")
        assert "CLAIM_CHALLENGE" in payload["runtime_feedback"]
        return agent_needs_user(
            "Qual informação devo considerar?",
            investigation=[{"id":"T1","goal":"Establish active reachability","status":"open","grounding_ids":[],"reason":""}],
            missing_information="A concrete user-supplied fact required to continue",
        )
    def fake_claim(prompt, _config):
        payload=json.loads(prompt); claim_calls.append(payload)
        return review(issues=[issue(kind="scope", answer_ref="answer:a1", grounding_refs=["request:r1","answer:a1"], reason="Current scope does not establish active reachability.")])
    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_claim)
    cfg=base_config(claims_mode="self_check")
    status, _, _, details=core_agent.executar_agente("Isso participa do runtime?", cfg, projeto={"caminho_origem":str(tmp_path)}, retornar_detalhes=True)
    assert status == "needs_user"
    assert len(claim_calls)==1
    assert details["investigation"][0]["id"]=="T1"
    assert any(x.get("decision")=="claim_review" and x.get("outcome")=="challenge" for x in details["decision_history"])


def test_claim_truncation_gets_one_protocol_recovery_then_accepts(monkeypatch, tmp_path):
    from llm.executar import ErroLLM

    monkeypatch.setattr(
        core_agent,
        "executar_agente_llm",
        lambda prompt, _cfg: agent_final("Quatro."),
    )
    calls = []

    def fake_claim(prompt, _cfg):
        calls.append(json.loads(prompt))
        if len(calls) == 1:
            raise ErroLLM("cut", transient=False, error_code="MODEL_OUTPUT_TRUNCATED")
        return review(verdict="accept")

    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_claim)
    status, text, _, details = core_agent.executar_agente(
        "Quanto é 2+2?", base_config(claims_mode="self_check"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert text == "Quatro."
    assert len(calls) == 2
    assert calls[1]["protocol_feedback"]["code"] == "CANONICAL_CLAIM_RECOVERY"
    decisions = [item for item in details["decision_history"] if item.get("decision") == "claim_protocol"]
    assert [item.get("outcome") for item in decisions] == ["rejected", "retry"]


def test_claim_second_truncation_remains_fail_closed(monkeypatch, tmp_path):
    from llm.executar import ErroLLM

    monkeypatch.setattr(
        core_agent,
        "executar_agente_llm",
        lambda prompt, _cfg: agent_final("Quatro."),
    )
    calls = []

    def fake_claim(prompt, _cfg):
        calls.append(json.loads(prompt))
        raise ErroLLM("cut", transient=False, error_code="MODEL_OUTPUT_TRUNCATED")

    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_claim)
    status, _, _, details = core_agent.executar_agente(
        "Quanto é 2+2?", base_config(claims_mode="self_check"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] == "MODEL_OUTPUT_TRUNCATED"
    assert len(calls) == 2
    decisions = [item for item in details["decision_history"] if item.get("decision") == "claim_protocol"]
    assert [item.get("outcome") for item in decisions] == ["rejected", "retry", "failed"]
