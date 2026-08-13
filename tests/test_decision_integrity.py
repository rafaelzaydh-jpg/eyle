from __future__ import annotations

import json
import pytest

import eyle.core.agent as core_agent
from eyle.core.workspace_io import listar_arvore_projeto
from llm.executar import ErroLLM
from llm.structured import StructuredResponseError, parse_agent_response, schema_for_profile
from tests.canonical import agent_final, base_config


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
    status, text, _, details = core_agent.executar_agente("Say ok", base_config(), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True)
    assert (status, text) == ("success", "ok")
    assert len(calls) == 2 and details["turns"] == 1


def test_agent_protocol_second_invalid_decision_fails_closed(monkeypatch, tmp_path):
    count = 0
    def fake_call(session, config, project, conversation_context, feedback=""):
        nonlocal count
        count += 1
        raise ErroLLM("bad", transient=False, error_code="STRUCTURED_RESPONSE_INVALID:agent:AGENT_ACTION_KIND_INVALID", structured_observed={"action": {"kind": "invalid"}, "investigation_updates": [], "task_updates": []})
    monkeypatch.setattr(core_agent, "_call_agent", fake_call)
    status, _, _, details = core_agent.executar_agente("Do something", base_config(), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True)
    assert status == "failed" and count == 2
    assert details["failure_code"] == "AGENT_STRUCTURED_PROTOCOL_INVALID"






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
