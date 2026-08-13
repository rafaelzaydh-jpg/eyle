from __future__ import annotations

import json
import pytest

import eyle.core.agent as core_agent
from eyle.core.workspace_io import listar_arvore_projeto
from llm.executar import ErroLLM, PROMPT_CLAIM_VERIFIER
from llm.structured import StructuredResponseError, parse_agent_response, schema_for_profile
from tests.canonical import agent_final, base_config, review


def _agent(action):
    return {"action": action, "investigation_updates": [], "task_updates": []}


def test_agent_schema_and_parser_share_one_discriminated_decision_contract():
    schema = schema_for_profile("agent")
    assert schema["required"] == ["action", "investigation_updates", "task_updates"]
    assert set(schema["properties"]) == {"action", "investigation_updates", "task_updates"}
    valid = [
        _agent({"kind": "tool_calls", "calls": [{"tool": "search_code", "arguments": {"query": "x"}}]}),
        _agent({"kind": "patches", "patches": [{"operation": "create", "path": "a.py", "content": "x=1\n"}]}),
        _agent({"kind": "needs_user", "question": "Which port?", "missing_information": "port"}),
        _agent({"kind": "final", "answer": "done", "limitations": [], "grounding_ids": []}),
    ]
    for payload in valid:
        assert parse_agent_response(payload)["action"]["kind"] == payload["action"]["kind"]
    with pytest.raises(StructuredResponseError):
        parse_agent_response({"tool_calls": [], "final": {}, "investigation_updates": [], "task_updates": []})
    with pytest.raises(StructuredResponseError):
        parse_agent_response({"action": {"kind": "final", "answer": "done", "limitations": [], "grounding_ids": []}, "investigation_updates": []})


def test_agent_protocol_gets_one_fresh_retry_without_new_semantic_turn(monkeypatch, tmp_path):
    calls = []
    def fake_call(session, config, project, conversation_context, feedback=""):
        calls.append(feedback)
        if len(calls) == 1:
            raise ErroLLM("bad", transient=False, error_code="STRUCTURED_RESPONSE_INVALID:agent:AGENT_ACTION_KIND_INVALID", structured_observed={"action": {"kind": "invalid"}, "investigation_updates": [], "task_updates": []})
        return agent_final("ok"), set()
    monkeypatch.setattr(core_agent, "_call_agent", fake_call)
    status, text, _, details = core_agent.executar_agente("Say ok", base_config(claims_mode="off"), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True)
    assert (status, text) == ("success", "ok")
    assert len(calls) == 2 and details["turns"] == 1


def test_agent_protocol_second_invalid_decision_fails_closed(monkeypatch, tmp_path):
    count = 0
    def fake_call(session, config, project, conversation_context, feedback=""):
        nonlocal count
        count += 1
        raise ErroLLM("bad", transient=False, error_code="STRUCTURED_RESPONSE_INVALID:agent:AGENT_ACTION_KIND_INVALID", structured_observed={"action": {"kind": "invalid"}, "investigation_updates": [], "task_updates": []})
    monkeypatch.setattr(core_agent, "_call_agent", fake_call)
    status, _, _, details = core_agent.executar_agente("Do something", base_config(claims_mode="off"), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True)
    assert status == "failed" and count == 2
    assert details["failure_code"] == "AGENT_STRUCTURED_PROTOCOL_INVALID"


def test_claim_protocol_gets_one_fresh_retry(monkeypatch):
    calls = []
    def fake_claim(prompt, config):
        calls.append(json.loads(prompt))
        if len(calls) == 1:
            raise ErroLLM("bad", transient=False, error_code="STRUCTURED_RESPONSE_INVALID:claim_verifier:CLAIM_REVIEW_MISSING_KEYS", structured_observed={"claims": []})
        return review()
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_claim)
    from eyle.core.session import AgentSession
    session = AgentSession("Say hi")
    ok, reason, normalized, _ = core_agent._run_claim_verification(session, base_config(claims_mode="fresh"), "Hi", [])
    assert ok is True and reason == "ok"
    assert normalized["verdict"] == "accept"
    assert normalized["issues"] == []
    assert len(calls) == 2


def test_claim_prompt_declares_fresh_isolation_and_no_main_state():
    lower = PROMPT_CLAIM_VERIFIER.lower()
    assert "fresh call" in lower
    assert "no main history" in lower
    assert "request, candidate_answer and observed_material" in PROMPT_CLAIM_VERIFIER
    for forbidden in ("runtime_facts", "request_anchors", "answer_anchors", "investigation state", "task_state"):
        assert forbidden not in lower


def test_output_truncation_is_only_provider_ceiling_after_cumulative_completion_budget_removal():
    import llm.executar as llm_mod
    result=llm_mod._classify_output_truncation()
    assert result["error_code"]=="MODEL_OUTPUT_TRUNCATED"
    assert result["cause"]=="provider_output_ceiling"

def test_tree_keeps_nonignored_hidden_directories_structurally_visible(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    (tmp_path / ".aws").mkdir()
    (tmp_path / ".aws" / "credentials").write_text("secret\n", encoding="utf-8")
    entries = {item["path"]: item for item in listar_arvore_projeto(str(tmp_path), limite=50, profundidade=4)["entries"]}
    assert ".github/workflows/ci.yml" in entries
    assert entries[".aws/credentials"]["content_access"] == "protected"
