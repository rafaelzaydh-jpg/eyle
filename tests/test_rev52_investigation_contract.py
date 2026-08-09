import json

import pytest

import eyle.core.agent as core_agent
from eyle.core.investigation import (
    reopen_targets_from_semantic_gaps,
    validate_investigation,
)
from eyle.core.session import AgentSession
from eyle.core.tools import TOOLS
from eyle.core.validation import validate_final
from llm.structured import parse_agent_response, schema_for_profile
from tests.canonical import base_config, investigation_target, workspace_scope


def _ev(path="x.py", line=1):
    return {
        "arquivo": path,
        "linha_inicio": line,
        "linha_fim": line,
        "file_hash": f"fh-{path}-{line}",
        "content_hash": f"ch-{path}-{line}",
        "conteudo": f"value_{line} = {line}",
    }


def test_investigation_contract_rejects_duplicate_id():
    raw = [investigation_target("T1"), investigation_target("T1", goal="Another goal")]
    ok, reason, _ = validate_investigation(raw, evidence={})
    assert ok is False
    assert reason == "INVESTIGATION_TARGET_ID_DUPLICATE:T1"


def test_investigation_contract_rejects_unknown_evidence():
    raw = [investigation_target(status="established", evidence_ids=["ev-missing"], reason="done")]
    ok, reason, _ = validate_investigation(raw, evidence={})
    assert ok is False
    assert reason == "INVESTIGATION_UNKNOWN_EVIDENCE:T1:ev-missing"


def test_investigation_contract_rejects_established_without_evidence():
    raw = [investigation_target(status="established", reason="done")]
    ok, reason, _ = validate_investigation(raw, evidence={})
    assert ok is False
    assert reason == "INVESTIGATION_ESTABLISHED_REQUIRES_EVIDENCE:T1"


def test_investigation_contract_rejects_silent_drop_and_goal_mutation():
    previous = [
        investigation_target("T1", goal="Establish A"),
        investigation_target("T2", goal="Establish B"),
    ]
    ok, reason, _ = validate_investigation(
        [investigation_target("T1", goal="Establish A")], previous=previous, evidence={},
    )
    assert ok is False and reason == "INVESTIGATION_TARGET_DROPPED:T2"

    ok, reason, _ = validate_investigation(
        [
            investigation_target("T1", goal="Changed A"),
            investigation_target("T2", goal="Establish B"),
        ],
        previous=previous,
        evidence={},
    )
    assert ok is False and reason == "INVESTIGATION_TARGET_GOAL_MUTATED:T1"


def test_investigation_contract_accepts_new_target_reopen_and_explicit_dismissal():
    evidence = {"ev-1": _ev()}
    previous = [investigation_target("T1", goal="Establish A", status="established", evidence_ids=["ev-1"], reason="seen")]
    current = [
        investigation_target("T1", goal="Establish A", status="open", evidence_ids=["ev-1"], reason="needs another path"),
        investigation_target("T2", goal="Establish B", status="dismissed", reason="not material after inspection"),
    ]
    ok, reason, normalized = validate_investigation(current, previous=previous, evidence=evidence)
    assert ok is True and reason == "ok"
    assert [item["status"] for item in normalized] == ["open", "dismissed"]


def test_dismissed_target_requires_reason():
    ok, reason, _ = validate_investigation(
        [investigation_target(status="dismissed")], evidence={},
    )
    assert ok is False
    assert reason == "INVESTIGATION_STATUS_REQUIRES_REASON:T1:dismissed"


def test_final_gate_blocks_declared_open_target_before_semantic_review():
    evidence = {"ev-1": _ev()}
    ok, reason, *_ = validate_final(
        {"answer": "x.py contains x.", "evidence_ids": ["ev-1"]},
        evidence,
        request="Analise x.py",
        project_available=True,
        investigation=[investigation_target(goal="Establish x.py behavior")],
        grounding_required=True,
    )
    assert ok is False
    assert reason == "FINAL_INVESTIGATION_TARGET_OPEN:T1"


def test_reviewer_named_gap_reopens_only_existing_target_and_null_gap_creates_nothing():
    investigation = [
        investigation_target("T1", goal="Establish A", status="established", evidence_ids=["ev-1"], reason="seen"),
        investigation_target("T2", goal="Establish B", status="dismissed", reason="not material"),
    ]
    gaps = [
        {"id": "g1", "type": "scope_gap", "target_id": "T1", "evidence_ids": [], "reason": "production path still missing"},
        {"id": "g2", "type": "scope_gap", "target_id": None, "evidence_ids": [], "reason": "another material scope is absent"},
    ]
    updated, reopened = reopen_targets_from_semantic_gaps(investigation, gaps)
    assert reopened == ["T1"]
    assert [item["id"] for item in updated] == ["T1", "T2"]
    assert updated[0]["status"] == "open"
    assert updated[0]["reason"] == "production path still missing"
    assert updated[1]["status"] == "dismissed"


def test_target_evidence_is_pinned_beyond_recent_evidence_window():
    session = AgentSession("analise")
    for index in range(1, 51):
        session.evidence[f"ev-{index}"] = _ev(f"f{index}.py", index)
    session.investigation = [
        investigation_target("T1", goal="Establish early fact", status="established", evidence_ids=["ev-1"], reason="early evidence")
    ]
    index = session.evidence_index()
    by_id = {item["id"]: item for item in index}
    assert "ev-1" in by_id
    assert by_id["ev-1"]["pinned"] is True
    assert "ev-50" in by_id


def test_inspect_project_observable_map_preserves_current_signal_schema():
    detail = {
        "file_count": 12,
        "directory_count": 3,
        "languages": {"Python": 10},
        "scan_complete": True,
        "entrypoint_signals": [{"path": "main.py", "reasons": ["main_guard"]}],
        "test_signals": {"has_tests": True, "count": 2, "files": ["tests/test_x.py"]},
        "ci_signals": {"has_ci": True, "files": [".github/workflows/ci.yml"]},
        "framework_signals": [{"name": "flask", "sources": ["app.py"]}],
        "relation_signals": {
            "local_import_edge_count": 7,
            "local_import_edges_truncated": False,
            "most_imported_files": [{"path": "core.py", "count": 3}],
            "route_files": ["routes.py"],
            "syntax_error_files": [],
        },
    }
    observable = core_agent._observable_tool_result("inspect_project", {"ok": True, "status": "success", "detail": detail})
    assert observable["entrypoint_signals"][0]["path"] == "main.py"
    assert observable["test_signals"]["count"] == 2
    assert observable["ci_signals"]["has_ci"] is True
    assert observable["framework_signals"][0]["name"] == "flask"
    assert observable["relation_signals"]["local_import_edge_count"] == 7
    assert observable["relation_signals"]["most_imported_files"][0]["path"] == "core.py"


def test_agent_and_verifier_schemas_expose_investigation_contract_without_new_tools():
    agent_schema = schema_for_profile("agent")
    gap_schema = schema_for_profile("claim_verifier")["properties"]["semantic_gaps"]["items"]
    assert "plan" not in agent_schema["properties"]
    assert "investigation_updates" in agent_schema["properties"]
    assert "maxItems" not in agent_schema["properties"]["investigation_updates"]
    assert gap_schema["properties"]["target_id"]["anyOf"][0] == {"type": "null"}
    assert len(TOOLS) == 16


def test_parser_accepts_canonical_investigation_target():
    parsed = parse_agent_response({
        "action": "tool_calls",
        "tool_calls": [{"tool": "read_file", "arguments": {"path": "x.py"}}],
        "patches": None,
        "needs_user": None,
        "final": None,
        "workspace_scope": workspace_scope("read"),
        "investigation_updates": [investigation_target(goal="Establish x.py behavior")],
    })
    assert parsed["investigation_updates"][0]["id"] == "T1"


def test_project_grounded_action_cannot_ignore_investigation_contract(monkeypatch, tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    calls = {"n": 0}

    def fake(_prompt, _cfg):
        calls["n"] += 1
        return {
            "tool_calls": [{"tool": "read_file", "arguments": {"path": "x.py"}}],
            "workspace_scope": workspace_scope("read"),
            "investigation_updates": [],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    cfg = base_config(claims_mode="off")
    cfg["agent"]["max_llm_turns"] = 2
    status, _, _, details = core_agent.executar_agente(
        "Analise x.py", cfg, projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "failed"
    assert any(
        item.get("decision") == "investigation_contract" and item.get("reason") == "INVESTIGATION_REQUIRED"
        for item in details["decision_history"]
    )
    assert details["tool_calls"] == 0


def test_claim_review_reopens_target_and_directs_next_investigation(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from app import run\nprint(run())\n", encoding="utf-8")
    cfg = base_config(claims_mode="self_check")
    prompts = []
    verifier_calls = {"n": 0}
    goal = "Establish how app.py participates in the real execution path"

    def fake_agent(prompt, _cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return {
                "tool_calls": [{"tool": "read_file", "arguments": {"path": "app.py"}}],
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [investigation_target(goal=goal)],
            }
        if len(prompts) == 2:
            return {
                "final": {"answer": "app.py defines run().", "evidence_ids": ["ev-0001"], "limitations": []},
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [investigation_target(goal=goal, status="established", evidence_ids=["ev-0001"], reason="app.py defines run")],
            }
        if len(prompts) == 3:
            target = payload["investigation"][0]
            assert target["status"] == "open"
            assert "execution path" in target["reason"]
            assert payload["investigation_map"]
            return {
                "tool_calls": [{"tool": "read_file", "arguments": {"path": "main.py"}}],
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [target],
            }
        return {
            "final": {"answer": "main.py imports and calls app.run().", "evidence_ids": ["ev-0001", "ev-0002"], "limitations": []},
            "workspace_scope": workspace_scope("read"),
            "investigation_updates": [investigation_target(goal=goal, status="established", evidence_ids=["ev-0001", "ev-0002"], reason="app.py defines run and main.py calls it")],
        }

    def fake_verifier(_prompt, _cfg):
        verifier_calls["n"] += 1
        if verifier_calls["n"] == 1:
            return {
                "claims": [{
                    "id": "c1", "answer_ref": "a1", "statement": "app.py defines run().",
                    "kind": "fact", "evidence_ids": ["ev-0001"], "verdict": "supported", "reason": "",
                }],
                "findings": [],
                "semantic_gaps": [{
                    "id": "g1", "type": "scope_gap", "target_id": "T1", "evidence_ids": [],
                    "reason": "definition is shown but the production execution path is not established",
                }],
            }
        return {
            "claims": [{
                "id": "c1", "answer_ref": "a1", "statement": "main.py imports and calls app.run().",
                "kind": "fact", "evidence_ids": ["ev-0002"], "verdict": "supported", "reason": "",
            }],
            "findings": [],
            "semantic_gaps": [],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_verifier)
    status, text, _, details = core_agent.executar_agente(
        "Explique o papel de app.py no fluxo real do projeto", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "main.py" in text
    assert details["tool_calls"] == 2
    reopened = [item for item in details["decision_history"] if item.get("decision") == "investigation_contract" and item.get("outcome") == "reopened"]
    assert reopened and reopened[-1]["reason"] == "T1"
    assert verifier_calls["n"] == 2


def test_test_only_wording_does_not_hide_tools_by_runtime_semantics(tmp_path):
    session = AgentSession("rode os testes do projeto")
    session.turn = 2
    session.evidence["ev-1"] = _ev("<runtime-tests>")
    session.investigation = [investigation_target(goal="Establish the requested test result")]
    session.latest_tool_results = [{"tool": "run_tests", "ok": True, "executed": True}]
    session.tool_history = [{"tool": "run_tests", "result": {"ok": True}, "turn": 1}]
    phase = core_agent._phase_for_call(
        session,
        base_config(claims_mode="off"),
        {"caminho_origem": str(tmp_path)},
    )
    assert phase == "analysis_complete_or_read"
