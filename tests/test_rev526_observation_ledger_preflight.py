from __future__ import annotations
import json

import eyle.core.agent as core_agent
from eyle.core.observation import lookup, record, semantic_signature
from eyle.core.session import AgentSession
from tests.canonical import base_config, investigation_target, workspace_scope


def _cfg():
    cfg = base_config(claims_mode="off")
    cfg["agent"].update({
        "max_llm_turns": 8,
        "max_tool_calls": 12,
        "committed_progress_extension_calls": 4,
    })
    return cfg


def test_search_a_b_a_rehydrates_without_third_physical_execution(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("ALPHA = 1\nBETA = 2\n", encoding="utf-8")
    turns = {"n": 0}

    def fake(prompt, _cfg):
        turns["n"] += 1
        payload = json.loads(prompt)
        if turns["n"] == 1:
            return {"tool_calls": [{"tool": "search_code", "arguments": {"query": "ALPHA"}}], "workspace_scope": workspace_scope("read"), "investigation_updates": [investigation_target("T1", goal="Establish constants")]}
        if turns["n"] == 2:
            return {"tool_calls": [{"tool": "search_code", "arguments": {"query": "BETA"}}], "workspace_scope": workspace_scope("read"), "investigation_updates": []}
        if turns["n"] == 3:
            return {"tool_calls": [{"tool": "search_code", "arguments": {"query": "ALPHA"}}], "workspace_scope": workspace_scope("read"), "investigation_updates": [investigation_target("T1", goal="Establish constants", status="established", evidence_ids=["ev-0001"], reason="Both constants were located") ]}
        assert any(item.get("replayed") is True for item in payload.get("latest_tool_results") or [])
        return {"final": {"answer": "ALPHA e BETA foram localizados.", "evidence_ids": ["ev-0001"]}, "workspace_scope": workspace_scope("read"), "investigation_updates": []}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _text, _pending, details = core_agent.executar_agente("Audit constants", _cfg(), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True)
    assert status == "success"
    assert details["tool_calls"] == 2
    assert details["observation_replays"] >= 1
    assert any(d.get("decision") == "tool_preflight" and d.get("reason") == "OBSERVATION_REHYDRATED" for d in details["decision_history"])


def test_complete_zero_match_becomes_citable_negative_evidence(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    calls = {"n": 0}

    def fake(prompt, _cfg):
        calls["n"] += 1
        payload = json.loads(prompt)
        if calls["n"] == 1:
            return {"tool_calls": [{"tool": "search_code", "arguments": {"query": "NEVER_PRESENT_LITERAL"}}], "workspace_scope": workspace_scope("read"), "investigation_updates": [investigation_target("T1", goal="Establish whether the literal occurs") ]}
        evidence = payload["evidence_index"]
        assert any(item.get("source_type") == "search_observation" for item in evidence)
        return {"final": {"answer": "A busca completa não encontrou o literal.", "evidence_ids": ["ev-0001"]}, "workspace_scope": workspace_scope("read"), "investigation_updates": [investigation_target("T1", goal="Establish whether the literal occurs", status="established", evidence_ids=["ev-0001"], reason="Complete search returned zero matches") ]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _text, _pending, details = core_agent.executar_agente("Check literal", _cfg(), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True)
    assert status == "success"
    assert details["tool_calls"] == 1
    assert details["evidence"][0]["source_type"] == "search_observation"


def test_symbol_not_found_is_reusable_observation(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    calls = {"n": 0}

    def fake(_prompt, _cfg):
        calls["n"] += 1
        if calls["n"] <= 2:
            return {"tool_calls": [{"tool": "find_symbol", "arguments": {"symbol": "MissingSymbol"}}], "workspace_scope": workspace_scope("read"), "investigation_updates": [investigation_target("T1", goal="Check symbol absence")] if calls["n"] == 1 else []}
        return {"final": {"answer": "O símbolo não foi localizado.", "evidence_ids": ["ev-0001"]}, "workspace_scope": workspace_scope("read"), "investigation_updates": [investigation_target("T1", goal="Check symbol absence", status="established", evidence_ids=["ev-0001"], reason="Symbol lookup returned not found") ]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _text, _pending, details = core_agent.executar_agente("Check symbol", _cfg(), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True)
    assert status == "success"
    assert details["tool_calls"] == 1
    assert details["observation_replays"] == 1
    assert any(item.get("source_type") == "symbol_observation" for item in details["evidence"])


def test_ledger_identity_changes_only_with_workspace_epoch():
    session = AgentSession("x")
    sig = semantic_signature("search_code", {"query": "X"})
    raw = {"ok": True, "executed": True, "changed": False, "detail": {"coverage_complete": True, "resultados": []}}
    model = {"tool": "search_code", "ok": True, "executed": True, "changed": False, "detail": {"coverage_complete": True, "resultados": []}, "evidence_ids": []}
    record(session, sig, "search_code", {"query": "X"}, raw, model)
    assert lookup(session, sig) is not None
    session.workspace_epoch += 1
    assert lookup(session, sig) is None


def test_observation_ledger_survives_session_persistence():
    session = AgentSession("x")
    sig = semantic_signature("project_stats", {})
    raw = {"ok": True, "executed": True, "changed": False, "detail": {"files": 1}}
    model = {"tool": "project_stats", "ok": True, "executed": True, "changed": False, "detail": {"files": 1}, "evidence_ids": []}
    record(session, sig, "project_stats", {}, raw, model)
    restored = AgentSession.from_dict(session.to_dict())
    assert lookup(restored, sig) is not None
    assert len(restored.observation_ledger) == 1


def test_extension_is_computed_after_replays_are_removed(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("ALPHA = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("BETA = 2\n", encoding="utf-8")
    (tmp_path / "g.py").write_text("GAMMA = 3\n", encoding="utf-8")
    cfg = _cfg(); cfg["agent"]["max_tool_calls"] = 1
    calls = {"n": 0}

    def fake(_prompt, _cfg):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tool_calls": [{"tool": "search_code", "arguments": {"query": "ALPHA"}}], "workspace_scope": workspace_scope("read"), "investigation_updates": [investigation_target("T1", goal="Establish alpha"), investigation_target("T2", goal="Establish beta/gamma")]}
        if calls["n"] == 2:
            return {
                "tool_calls": [
                    {"tool": "search_code", "arguments": {"query": "ALPHA"}},
                    {"tool": "search_code", "arguments": {"query": "BETA"}},
                    {"tool": "search_code", "arguments": {"query": "GAMMA"}},
                ],
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [investigation_target("T1", goal="Establish alpha", status="established", evidence_ids=["ev-0001"], reason="Alpha located")],
            }
        return {"final": {"answer": "Observações concluídas.", "evidence_ids": ["ev-0001", "ev-0002", "ev-0003"]}, "workspace_scope": workspace_scope("read"), "investigation_updates": [investigation_target("T2", goal="Establish beta/gamma", status="established", evidence_ids=["ev-0002", "ev-0003"], reason="Beta and gamma located") ]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _text, _pending, details = core_agent.executar_agente("Inspect", cfg, projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True)
    assert status == "success"
    assert details["tool_calls"] == 3  # 1 base + 2 genuinely novel after replay removal
    assert details["observation_replays"] >= 1
    assert details["tool_budget"]["earned_extension"] == 4
    assert details["tool_extension_history"][0]["granted"] == 4


def test_repeated_rejected_batch_is_detected_before_turn_limit(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("ALPHA = 1\n", encoding="utf-8")
    cfg = _cfg(); cfg["agent"]["max_tool_calls"] = 1; cfg["agent"]["max_llm_turns"] = 8
    calls = {"n": 0}

    def fake(_prompt, _cfg):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tool_calls": [{"tool": "search_code", "arguments": {"query": "ALPHA"}}], "workspace_scope": workspace_scope("read"), "investigation_updates": [investigation_target("T1", goal="Keep debt open")]}
        return {"tool_calls": [
            {"tool": "search_code", "arguments": {"query": "BETA"}},
            {"tool": "search_code", "arguments": {"query": "GAMMA"}},
        ], "workspace_scope": workspace_scope("read"), "investigation_updates": []}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _text, _pending, details = core_agent.executar_agente("Inspect", cfg, projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True)
    assert status == "failed"
    assert details["failure_code"] == "ADMINISTRATIVE_LOOP"
    assert details["turns"] < cfg["agent"]["max_llm_turns"]
    assert details["tool_calls"] == 1
    assert details["repeated_rejected_decisions"] >= 1
