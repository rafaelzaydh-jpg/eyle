import json

import pytest

import eyle.core.agent as core_agent
from eyle.core.session import AgentSession
from eyle.runtime import service
from llm.structured import StructuredResponseError, parse_agent_response
from tests.canonical import base_config, investigation_target, workspace_scope


def _project(tmp_path):
    return {"caminho_origem": str(tmp_path)}


def test_conversation_background_survives_tool_turns(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, _cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        assert payload["request"] == "Analise app.py"
        assert any(item.get("content") == "Ao terminar, diga abacaxi." for item in payload["conversation_background"])
        if len(prompts) == 1:
            return {"tool_calls": [{"tool": "read_file", "arguments": {"caminho_relativo": "app.py"}}], "workspace_scope": workspace_scope("read"), "investigation": [investigation_target(goal="Establish what app.py defines")]}
        return {"final": {"answer": "app.py define x como 1. abacaxi", "evidence_ids": ["ev-0001"], "limitations": []}, "workspace_scope": workspace_scope("read"), "investigation": [investigation_target(goal="Establish what app.py defines", status="established", evidence_ids=["ev-0001"], reason="app.py was read")]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, _ = core_agent.executar_agente(
        "Analise app.py", base_config(claims_mode="off"), projeto=_project(tmp_path),
        conversation_context={"recent_messages": [{"role": "user", "text": "Ao terminar, diga abacaxi."}]},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert text.endswith("abacaxi")
    assert len(prompts) == 2


def test_agent_prompt_declares_current_request_authority():
    import llm.executar as llm_exec
    prompt = llm_exec.PROMPT_AGENTE
    assert "request is the only active task" in prompt
    assert "conversation_background is context" in prompt
    assert "request is the only active task" in prompt
    assert "investigation_map" in prompt


def test_failed_assistant_job_is_not_future_conversation_background():
    messages = [
        {"id": 10, "role": "user", "text": "Analise AgentSession"},
        {"id": 11, "role": "assistant", "text": "Falha ao processar", "agent_status": "failed", "reply_to_message_id": 10},
        {"id": 12, "role": "user", "text": "Analise o projeto"},
    ]
    filtered = service._historico_sem_erros_llm(messages)
    assert filtered == [{"id": 12, "role": "user", "text": "Analise o projeto"}]


def test_investigation_map_survives_raw_followup_cleanup(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    cfg = base_config(claims_mode="self_check")
    prompts = []
    verifier_calls = {"n": 0}

    def fake_agent(prompt, _cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return {"tool_calls": [{"tool": "read_file", "arguments": {"caminho_relativo": "app.py"}}], "workspace_scope": workspace_scope("read"), "investigation": [investigation_target(goal="Establish the material scope of app.py")]}
        if len(prompts) == 2:
            return {"final": {"answer": "app.py define x como 1.", "evidence_ids": ["ev-0001"], "limitations": []}, "workspace_scope": workspace_scope("read"), "investigation": [investigation_target(goal="Establish the material scope of app.py", status="established", evidence_ids=["ev-0001"], reason="app.py was read")]}
        assert payload["latest_tool_results"] == []
        assert payload["investigation_map"]
        assert payload["investigation_map"][-1]["tool"] == "read_file"
        assert "semantic_gaps" in (payload.get("runtime_feedback") or "")
        assert payload["investigation"][0]["status"] == "open"
        assert "restante do escopo" in payload["investigation"][0]["reason"]
        return {"final": {"answer": "app.py define x como 1; o restante não foi estabelecido.", "evidence_ids": ["ev-0001"], "limitations": []}, "workspace_scope": workspace_scope("read"), "investigation": [investigation_target(goal="Establish the material scope of app.py", status="established", evidence_ids=["ev-0001"], reason="The answer is explicitly limited to the observed file")]}

    def fake_verifier(_prompt, _cfg):
        verifier_calls["n"] += 1
        if verifier_calls["n"] == 1:
            return {
                "claims": [{"id": "c1", "answer_ref": "a1", "statement": "app.py define x como 1.", "kind": "fact", "evidence_ids": ["ev-0001"], "verdict": "supported", "reason": ""}],
                "findings": [],
                "semantic_gaps": [{"id": "g1", "type": "scope_gap", "target_id": "T1", "evidence_ids": [], "reason": "restante do escopo não foi investigado"}],
            }
        return {
            "claims": [{"id": "c1", "answer_ref": "a1", "statement": "app.py define x como 1; o restante não foi estabelecido.", "kind": "fact", "evidence_ids": ["ev-0001"], "verdict": "supported", "reason": ""}],
            "findings": [], "semantic_gaps": [],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_verifier)
    status, _, _, _ = core_agent.executar_agente(
        "Analise app.py", cfg, projeto=_project(tmp_path), retornar_detalhes=True,
    )
    assert status == "success"
    assert len(prompts) == 3


def test_blocked_repeated_reads_do_not_trigger_identical_tool_loop(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    cfg = base_config(claims_mode="off")
    cfg["agent"]["max_llm_turns"] = 6
    cfg["agent"]["max_no_progress_turns"] = 4
    calls = {"n": 0}

    def fake(prompt, _cfg):
        calls["n"] += 1
        if calls["n"] <= 4:
            return {"tool_calls": [{"tool": "read_file", "arguments": {"caminho_relativo": "app.py"}}], "workspace_scope": workspace_scope("read"), "investigation": [investigation_target(goal="Establish what app.py defines")]}
        return {"final": {"answer": "x é 1.", "evidence_ids": ["ev-0001"], "limitations": []}, "workspace_scope": workspace_scope("read"), "investigation": [investigation_target(goal="Establish what app.py defines", status="established", evidence_ids=["ev-0001"], reason="app.py was read")]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, _, details = core_agent.executar_agente(
        "Analise app.py", cfg, projeto=_project(tmp_path), retornar_detalhes=True,
    )
    assert status == "success"
    assert details["tool_calls"] == 1
    assert not any(item.get("reason") == "IDENTICAL_TOOL_LOOP" for item in details["decision_history"])


def test_agent_batch_contract_rejects_more_than_four_calls():
    envelope = {
        "action": "tool_calls",
        "tool_calls": [{"tool": "read_file", "arguments": {"caminho_relativo": f"f{i}.py"}} for i in range(5)],
        "patches": None, "needs_user": None, "final": None, "workspace_scope": workspace_scope("read"), "investigation": [],
    }
    with pytest.raises(StructuredResponseError) as exc:
        parse_agent_response(envelope)
    assert exc.value.code == "AGENT_TOOL_CALL_LIMIT_EXCEEDED"


def test_local_finding_recovery_preserves_claim_and_repairs_coverage(monkeypatch):
    session = AgentSession("verifique riscos")
    session.evidence["ev-1"] = {
        "arquivo": "x.py", "linha_inicio": 1, "linha_fim": 1,
        "file_hash": "h", "content_hash": "c", "conteudo": "danger = True",
    }
    replies = [
        {
            "claims": [{"id": "c5", "answer_ref": "a1", "statement": "Há um risco.", "kind": "risk", "evidence_ids": [], "verdict": "supported", "reason": ""}],
            "findings": [{"id": "f-old", "type": "recommendation", "claim_ids": ["c5"]}],
            "semantic_gaps": [],
        },
        {
            "claims": [{"id": "c5", "answer_ref": "a1", "statement": "Há um risco.", "kind": "risk", "evidence_ids": ["ev-1"], "verdict": "supported", "reason": ""}],
            "findings": [], "semantic_gaps": [],
        },
        {
            "claims": [],
            "findings": [{"id": "f-risk", "type": "risk", "claim_ids": ["c5"]}],
            "semantic_gaps": [],
        },
    ]
    seen = []

    def fake(prompt, _cfg):
        seen.append(json.loads(prompt))
        return replies.pop(0)

    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake)
    ok, reason, review, _ = core_agent._run_claim_verification(
        session, base_config(claims_mode="self_check"), "Há um risco.", ["ev-1"], project_root=None,
    )
    assert ok is True and reason == "ok"
    assert review["claims"][0]["id"] == "c5"
    assert review["claims"][0]["evidence_ids"] == ["ev-1"]
    assert review["findings"] == [{"id": "f-risk", "type": "risk", "claim_ids": ["c5"]}]
    assert seen[-1]["task"] == "reverify_findings"
