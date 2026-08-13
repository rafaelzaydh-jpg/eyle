from __future__ import annotations

import json

import eyle.core.agent as core_agent
from eyle.core.session import AgentSession
from eyle.core.tasks import task_state_view
from eyle.core.token_budget import estimate_tokens
from llm import executar as llm_mod
from llm.structured import schema_for_profile
from tests.canonical import agent_final, base_config, issue, review, task_item


def test_fresh_claim_inherits_main_transport_but_not_external_verifier_identity():
    cfg = base_config(claims_mode="fresh")
    cfg["llm"].update({
        "base_url": "http://main.invalid/v1",
        "model": "same-model",
        "openai_compatible": True,
        "temperature": 0.7,
    })
    resolved = core_agent._claim_llm_config(cfg, "fresh")
    assert resolved["llm"]["base_url"] == cfg["llm"]["base_url"]
    assert resolved["llm"]["model"] == cfg["llm"]["model"]
    assert resolved["llm"]["openai_compatible"] is True
    assert resolved["llm"]["temperature"] == 0.0


def test_fresh_claim_mode_rejects_transport_override_in_claim_config():
    from eyle.core.claim_review import ClaimConfigError, claim_config

    cfg = base_config(claims_mode="fresh")
    cfg["agent"]["claims"]["verifier"]["model"] = "other-model"
    try:
        claim_config(cfg)
    except ClaimConfigError as error:
        assert "UNKNOWN_CONFIG_FIELD" in str(error)
    else:
        raise AssertionError("fresh Claim must use Main transport/model")


def test_empty_task_state_is_not_sent_to_main_prompt():
    session = AgentSession("hello")
    cfg = base_config(claims_mode="off")
    call_cfg = core_agent._agent_config(cfg, session, {})
    prompt, _ = core_agent._compile_prompt(session, call_cfg, {}, None, "")
    payload = json.loads(prompt)
    assert "task_state" not in payload


def test_nonempty_task_state_is_sent_to_main_prompt():
    session = AgentSession("work")
    session.tasks = [task_item("root", description="Do work", status="open", result="")]
    cfg = base_config(claims_mode="off")
    call_cfg = core_agent._agent_config(cfg, session, {})
    prompt, _ = core_agent._compile_prompt(session, call_cfg, {}, None, "")
    payload = json.loads(prompt)
    assert payload["task_state"] == task_state_view(session.tasks)


def test_agent_schema_deduplicates_investigation_and_task_variants():
    schema = schema_for_profile("agent")
    investigation = schema["properties"]["investigation_updates"]["items"]
    task = schema["properties"]["task_updates"]["items"]
    assert "anyOf" not in investigation
    assert "anyOf" not in task
    assert investigation["properties"]["status"]["enum"] == ["open", "established", "dismissed"]
    assert task["properties"]["status"]["enum"] == ["open", "completed", "dropped"]


def test_rev134_fixed_prompt_and_schema_are_materially_smaller():
    agent_schema = json.dumps(schema_for_profile("agent"), ensure_ascii=False, separators=(",", ":"))
    assert estimate_tokens(llm_mod.PROMPT_AGENTE, 3) <= 750
    assert estimate_tokens(agent_schema, 3) <= 1000
    assert "operational_feedback" not in llm_mod.PROMPT_AGENTE
    assert "claim_reserve_tokens" not in llm_mod.PROMPT_AGENTE


def test_second_claim_challenge_fails_closed_instead_of_looping(monkeypatch, tmp_path):
    agent_calls = []
    claim_calls = []

    def fake_agent(prompt, _cfg):
        payload = json.loads(prompt)
        agent_calls.append(payload)
        return agent_final("candidate one" if len(agent_calls) == 1 else "candidate two")

    def fake_claim(prompt, _cfg):
        claim_calls.append(json.loads(prompt))
        return review(issues=[issue(kind="inconsistent", grounding_refs=[], reason="Candidate still has a material defect.")])

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_claim)
    status, _, _, details = core_agent.executar_agente(
        "Answer carefully.",
        base_config(claims_mode="fresh"),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] == "CLAIM_CHALLENGE_UNRESOLVED"
    assert len(agent_calls) == 2
    assert len(claim_calls) == 2
    assert all(set(packet) == {"request", "candidate_answer", "observed_material"} for packet in claim_calls)
