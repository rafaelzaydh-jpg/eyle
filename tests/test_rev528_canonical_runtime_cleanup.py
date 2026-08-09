from __future__ import annotations
import json

import eyle.core.agent as core_agent
import eyle.core.request_policy as request_policy
from eyle.core.session import AgentSession
from eyle.core.tools import TOOLS, validar_chamada_tool
from tests.canonical import base_config, investigation_target, workspace_scope


def test_objective_progress_ignores_reason_and_status_churn_but_tracks_evidence_binding():
    session = AgentSession("audit")
    session.investigation = [
        investigation_target("T1", goal="Establish usage", status="open", evidence_ids=[], reason="first thought")
    ]
    before = core_agent._runtime_progress_fingerprint(session)

    session.investigation[0]["reason"] = "same reality, different wording"
    session.investigation[0]["status"] = "established"
    assert core_agent._runtime_progress_fingerprint(session) == before

    session.evidence["ev-1"] = {"arquivo": "a.py", "content_hash": "c", "file_hash": "f"}
    session.investigation[0]["evidence_ids"] = ["ev-1"]
    assert core_agent._runtime_progress_fingerprint(session) != before


def test_decision_ledger_distinguishes_new_observation_and_authority_state():
    session = AgentSession("audit")
    session.workspace_scope = workspace_scope("read")
    session.investigation = [investigation_target("T1", goal="Establish usage")]
    payload = [{"tool": "search_code", "arguments": {"query": "X"}}]

    first = core_agent._record_rejected_decision(
        session, "TOOL_BATCH_EXCEEDS_AUTHORIZED_BUDGET", payload,
        objective_context={"tool_calls_used": 11, "remaining_tool_calls": 1, "effective_tool_limit": 12},
    )
    assert first == 1

    session.observation_ledger["w0:search:Y"] = {"result_fingerprint": "new-result"}
    changed_reality = core_agent._record_rejected_decision(
        session, "TOOL_BATCH_EXCEEDS_AUTHORIZED_BUDGET", payload,
        objective_context={"tool_calls_used": 11, "remaining_tool_calls": 1, "effective_tool_limit": 12},
    )
    assert changed_reality == 1

    exact_repeat = core_agent._record_rejected_decision(
        session, "TOOL_BATCH_EXCEEDS_AUTHORIZED_BUDGET", payload,
        objective_context={"tool_calls_used": 11, "remaining_tool_calls": 1, "effective_tool_limit": 12},
    )
    assert exact_repeat == 2

    changed_authority = core_agent._record_rejected_decision(
        session, "TOOL_BATCH_EXCEEDS_AUTHORIZED_BUDGET", payload,
        objective_context={"tool_calls_used": 12, "remaining_tool_calls": 0, "effective_tool_limit": 12},
    )
    assert changed_authority == 1


def test_invalid_batch_is_rejected_before_tool_authority(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("ALPHA = 1\n", encoding="utf-8")
    cfg = base_config(claims_mode="off")
    cfg["agent"]["max_tool_calls"] = 1
    cfg["agent"]["max_llm_turns"] = 4
    turns = {"n": 0}

    def fake(prompt, _cfg):
        turns["n"] += 1
        payload = json.loads(prompt)
        if turns["n"] == 1:
            return {
                "tool_calls": [{"tool": "search_code", "arguments": {"query": "ALPHA"}}],
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [investigation_target("T1", goal="Establish alpha")],
            }
        if turns["n"] == 2:
            # One novel physical call plus one legacy/malformed read_file call.
            # Validation must win even though physical authority is exhausted.
            return {
                "tool_calls": [
                    {"tool": "search_code", "arguments": {"query": "BETA"}},
                    {"tool": "read_file", "arguments": {"caminho_relativo": "a.py"}},
                ],
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [],
            }
        feedback = payload["runtime_feedback"]
        if isinstance(feedback, str):
            feedback = json.loads(feedback)
        assert feedback["code"] == "TOOL_BATCH_VALIDATION_FAILED"
        return {
            "final": {"answer": "ALPHA foi observado.", "evidence_ids": ["ev-0001"], "limitations": []},
            "workspace_scope": workspace_scope("read"),
            "investigation_updates": [
                investigation_target("T1", goal="Establish alpha", status="established", evidence_ids=["ev-0001"], reason="Observed")
            ],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _text, _pending, details = core_agent.executar_agente(
        "Audit", cfg, projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert details["tool_calls"] == 1
    assert any(
        item.get("decision") == "tool_preflight" and item.get("outcome") == "batch_rejected"
        for item in details["decision_history"]
    )
    assert not any(
        item.get("decision") == "tool_authority" and item.get("outcome") == "batch_rejected"
        for item in details["decision_history"]
    )


def test_public_tool_abi_is_canonical_and_legacy_aliases_are_rejected():
    assert set(TOOLS["read_file"]["input_schema"]["properties"]) == {"path"}
    assert set(TOOLS["read_range"]["input_schema"]["properties"]) == {"path", "line_start", "line_end"}
    assert set(TOOLS["find_symbol"]["input_schema"]["properties"]) == {"path", "symbol"}
    assert set(TOOLS["list_tree"]["input_schema"]["properties"]) == {"limit", "depth", "filter"}
    assert "path" in TOOLS["count_tokens"]["input_schema"]["properties"]

    normalized, error = validar_chamada_tool("read_file", {"path": "a.py"})
    assert error is None and normalized == {"path": "a.py"}
    _normalized, legacy_error = validar_chamada_tool("read_file", {"caminho_relativo": "a.py"})
    assert legacy_error["error_code"] == "INVALID_ARGUMENT"


def test_legacy_runtime_authority_helpers_and_signature_wrapper_are_gone():
    assert not hasattr(request_policy, "request_needs_project_evidence")
    assert not hasattr(request_policy, "request_requires_write")
    assert not hasattr(core_agent, "_semantic_read_signature")
    assert hasattr(core_agent, "_source_already_visible")


def test_agent_prompt_requires_incremental_evidence_binding_without_runtime_semantics():
    from llm.executar import PROMPT_AGENTE
    assert "Open targets may accumulate Evidence" in PROMPT_AGENTE
    assert "attach it immediately" in PROMPT_AGENTE
    assert "Runtime never chooses relevance" in PROMPT_AGENTE
