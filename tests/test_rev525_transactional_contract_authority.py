import json
from pathlib import Path

import pytest

from eyle.core import agent as core_agent
from eyle.core.investigation import apply_investigation_updates
from eyle.core.session import AgentSession
from eyle.runtime.config import ConfigError, validar_config
from tests.canonical import base_config, investigation_target, workspace_scope


def _cfg(*, claims_mode="off"):
    cfg = base_config(claims_mode=claims_mode)
    cfg["agent"]["committed_progress_extension_calls"] = 4
    return cfg


def test_transactional_updates_commit_valid_siblings_and_reject_only_invalid():
    evidence = {"ev-1": {"arquivo": "a.py"}}
    updates = [
        investigation_target("T1", goal="Establish A", status="established", evidence_ids=["ev-1"], reason="A read"),
        investigation_target("T2", goal="Establish B", status="established", evidence_ids=["ev-missing"], reason="B read"),
    ]
    canonical, accepted, rejected, progress = apply_investigation_updates(
        updates, previous=[], evidence=evidence,
    )
    assert [item["id"] for item in canonical] == ["T1"]
    assert [item["id"] for item in accepted] == ["T1"]
    assert rejected[0]["id"] == "T2"
    assert rejected[0]["reason"].startswith("INVESTIGATION_UNKNOWN_EVIDENCE:T2")
    # New target creation defines debt/state but does not mint authority.
    assert progress == []



def test_new_target_with_old_evidence_cannot_mint_committed_progress():
    evidence = {"ev-1": {"arquivo": "a.py"}}
    canonical, accepted, rejected, progress = apply_investigation_updates(
        [investigation_target("T-new", goal="Late target", status="established", evidence_ids=["ev-1"], reason="late mapping")],
        previous=[], evidence=evidence,
    )
    assert canonical[0]["status"] == "established"
    assert accepted and rejected == []
    assert progress == []

def test_transactional_updates_preserve_omitted_targets_and_committed_evidence():
    previous = [
        investigation_target("T1", goal="Establish A", status="established", evidence_ids=["ev-1"], reason="A read"),
        investigation_target("T2", goal="Establish B"),
    ]
    evidence = {"ev-1": {}, "ev-2": {}}
    canonical, accepted, rejected, progress = apply_investigation_updates(
        [investigation_target("T2", goal="Establish B", status="established", evidence_ids=["ev-2"], reason="B read")],
        previous=previous, evidence=evidence,
    )
    assert rejected == []
    assert canonical[0] == previous[0]
    assert canonical[1]["status"] == "established"
    assert accepted[0]["id"] == "T2"
    assert progress[0]["target_id"] == "T2"

    canonical2, accepted2, rejected2, progress2 = apply_investigation_updates(
        [investigation_target("T1", goal="Establish A", status="open", evidence_ids=[], reason="reopen")],
        previous=canonical, evidence=evidence,
    )
    assert rejected2 == []
    assert accepted2[0]["id"] == "T1"
    assert canonical2[0]["status"] == "open"
    assert canonical2[0]["evidence_ids"] == ["ev-1"]
    assert progress2 == []


def test_committed_progress_deposits_one_epoch_per_main_llm_update_cycle():
    session = AgentSession("audit")
    session.evidence = {"ev-1": {}, "ev-2": {}}
    material = [
        {"target_id": "T1", "added_evidence_ids": ["ev-1"], "established_transition": True},
        {"target_id": "T2", "added_evidence_ids": ["ev-2"], "established_transition": True},
    ]
    assert core_agent._record_committed_progress(session, material) is True
    assert session.committed_progress_epoch == 1
    assert session.committed_progress_history[-1]["target_ids"] == ["T1", "T2"]
    assert core_agent._record_committed_progress(session, []) is False
    assert session.committed_progress_epoch == 1


def test_extension_is_runtime_authority_from_committed_progress_not_claim_review():
    session = AgentSession("audit")
    session.investigation = [investigation_target("T1", goal="Establish A")]
    session.committed_progress_epoch = 1
    cfg = _cfg()
    assert core_agent._grant_committed_progress_extension(session, cfg) == 4
    assert session.earned_tool_extension == 4
    assert session.tool_extension_cycles == 1
    assert session.last_extension_progress_epoch == 1
    # Same deposit cannot mint another extension.
    assert core_agent._grant_committed_progress_extension(session, cfg) == 0
    session.committed_progress_epoch = 2
    assert core_agent._grant_committed_progress_extension(session, cfg) == 4
    assert session.earned_tool_extension == 8
    session.committed_progress_epoch = 3
    assert core_agent._grant_committed_progress_extension(session, cfg) == 4
    assert session.earned_tool_extension == 12
    assert core_agent._grant_committed_progress_extension(session, cfg) == 0


def test_extension_requires_open_debt():
    session = AgentSession("audit")
    session.investigation = [
        investigation_target("T1", goal="Establish A", status="established", evidence_ids=["ev-1"], reason="A read")
    ]
    session.committed_progress_epoch = 1
    assert core_agent._grant_committed_progress_extension(session, _cfg()) == 0



def test_claim_review_remains_second_brain_and_does_not_mint_extension(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    cfg = _cfg(claims_mode="self_check")
    cfg["agent"]["max_tool_calls"] = 1
    cfg["agent"]["max_llm_turns"] = 3
    calls = {"n": 0}

    def fake_agent(prompt, _cfg):
        payload = json.loads(prompt)
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "tool_calls": [{"tool": "read_file", "arguments": {"path": "a.py"}}],
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [
                    investigation_target("T1", goal="Establish A"),
                    investigation_target("T2", goal="Establish B"),
                ],
            }
        if calls["n"] == 2:
            return {
                "final": {"answer": "A and B are confirmed.", "evidence_ids": ["ev-0001"], "limitations": []},
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [
                    investigation_target("T1", goal="Establish A", status="established", evidence_ids=["ev-0001"], reason="A read"),
                    investigation_target("T2", goal="Establish B", status="established", evidence_ids=["ev-0001"], reason="provisional mapping"),
                ],
            }
        assert payload["tool_authority"]["earned_extension"] == 0
        assert "CLAIM_REVIEW_FOLLOWUP" in str(payload.get("runtime_feedback"))
        return {
            "needs_user": "T2 needs a new observation.",
            "workspace_scope": workspace_scope("read"),
            "investigation_updates": [],
        }

    def fake_verifier(_prompt, _cfg):
        return {
            "claims": [
                {"id": "c1", "answer_ref": "a1", "target_id": "T1", "statement": "A is confirmed.", "kind": "fact", "evidence_ids": ["ev-0001"], "verdict": "supported", "reason": ""},
                {"id": "c2", "answer_ref": "a1", "target_id": "T2", "statement": "B is confirmed.", "kind": "fact", "evidence_ids": ["ev-0001"], "verdict": "insufficient", "reason": "Need direct B evidence."},
            ],
            "findings": [],
            "semantic_gaps": [],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_verifier)
    status, _text, _pending, details = core_agent.executar_agente(
        "Confirm A and B.", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user", (_text, details)
    assert details["tool_budget"]["earned_extension"] == 0
    assert not any(
        item.get("decision") == "tool_authority" and item.get("outcome") == "extension_granted"
        for item in details["decision_history"]
    )
    assert any(item.get("decision") == "claim_review" for item in details["decision_history"])

def test_atomic_batch_does_not_execute_when_no_committed_credit(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    cfg = _cfg()
    cfg["agent"]["max_tool_calls"] = 1
    cfg["agent"]["max_llm_turns"] = 2
    executed = []
    calls = {"n": 0}

    def fake_agent(_prompt, _cfg):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "tool_calls": [
                    {"tool": "project_stats", "arguments": {}},
                    {"tool": "inspect_project", "arguments": {}},
                ],
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [investigation_target("T1", goal="Establish project shape")],
            }
        return {
            "needs_user": "Need one choice.",
            "workspace_scope": workspace_scope("read"),
            "investigation_updates": [],
        }

    def fake_tool(*args, **kwargs):
        executed.append((args, kwargs))
        return {"status": "success", "ok": True, "executed": True, "changed": False}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_tool", fake_tool)
    status, _text, _pending, details = core_agent.executar_agente(
        "Audit the project.", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert details["tool_calls"] == 0
    assert executed == []
    assert any(
        item.get("decision") == "tool_authority" and item.get("outcome") == "batch_rejected"
        for item in details["decision_history"]
    )


def test_committed_progress_extension_unlocks_batch_before_final_review(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("B = 2\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("C = 3\n", encoding="utf-8")
    cfg = _cfg(claims_mode="off")
    cfg["agent"]["max_tool_calls"] = 1
    cfg["agent"]["committed_progress_extension_calls"] = 2
    cfg["agent"]["max_llm_turns"] = 3
    calls = {"n": 0}

    def fake_agent(prompt, _cfg):
        payload = json.loads(prompt)
        calls["n"] += 1
        if calls["n"] == 1:
            assert payload["investigation"] == []
            assert "effective_tool_limit" not in payload["tool_authority"]
            return {
                "tool_calls": [{"tool": "read_file", "arguments": {"path": "a.py"}}],
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [
                    investigation_target("T1", goal="Establish A"),
                    investigation_target("T2", goal="Establish B"),
                ],
            }
        if calls["n"] == 2:
            # T2 is omitted on purpose: runtime must preserve it canonically.
            assert {item["id"] for item in payload["investigation"]} == {"T1", "T2"}
            return {
                "tool_calls": [
                    {"tool": "read_file", "arguments": {"path": "b.py"}},
                    {"tool": "read_file", "arguments": {"path": "c.py"}},
                ],
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [
                    investigation_target("T1", goal="Establish A", status="established", evidence_ids=["ev-0001"], reason="A read"),
                ],
            }
        return {
            "final": {"answer": "A and B are established.", "evidence_ids": ["ev-0001", "ev-0002"], "limitations": []},
            "workspace_scope": workspace_scope("read"),
            "investigation_updates": [
                investigation_target("T2", goal="Establish B", status="established", evidence_ids=["ev-0002"], reason="B read"),
            ],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    status, _text, _pending, details = core_agent.executar_agente(
        "Confirm A and B.", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success", (_text, details)
    assert details["tool_calls"] == 3
    assert details["tool_budget"]["base"] == 1
    assert details["tool_budget"]["earned_extension"] == 2
    assert details["tool_budget"]["effective_limit"] == 3
    assert details["tool_budget"]["extension_cycles"] == 1
    assert details["committed_progress_history"]
    assert any(
        item.get("decision") == "tool_authority" and item.get("outcome") == "extension_granted"
        for item in details["decision_history"]
    )
    assert not any(
        item.get("decision") == "claim_review"
        for item in details["decision_history"]
    )


def test_partial_contract_failure_keeps_valid_deposit_for_retry(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    cfg = _cfg()
    cfg["agent"]["max_llm_turns"] = 3
    calls = {"n": 0}

    def fake_agent(prompt, _cfg):
        payload = json.loads(prompt)
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "tool_calls": [{"tool": "read_file", "arguments": {"path": "a.py"}}],
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [
                    investigation_target("T1", goal="Establish A"),
                    investigation_target("T2", goal="Establish B"),
                ],
            }
        if calls["n"] == 2:
            return {
                "needs_user": "ignored while contract correction is required",
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [
                    investigation_target("T1", goal="Establish A", status="established", evidence_ids=["ev-0001"], reason="A read"),
                    investigation_target("T2", goal="Establish B", status="established", evidence_ids=["ev-missing"], reason="bad id"),
                ],
            }
        feedback = json.loads(payload["runtime_feedback"])
        assert feedback["code"] == "INVESTIGATION_UPDATES_PARTIALLY_REJECTED"
        assert payload["investigation"][0]["status"] == "established"
        assert payload["investigation"][0]["evidence_ids"] == ["ev-0001"]
        return {
            "needs_user": "Only T2 remains unresolved.",
            "workspace_scope": workspace_scope("read"),
            "investigation_updates": [],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    status, _text, _pending, details = core_agent.executar_agente(
        "Audit A and B.", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert details["investigation"][0]["status"] == "established"
    assert details["committed_progress_history"][-1]["target_ids"] == ["T1"]


def test_authority_state_survives_session_roundtrip():
    session = AgentSession("audit")
    session.earned_tool_extension = 4
    session.tool_extension_cycles = 1
    session.committed_progress_epoch = 3
    session.last_extension_progress_epoch = 2
    session.committed_progress_history = [{"turn": 2, "epoch": 3, "target_ids": ["T1"]}]
    session.tool_extension_history = [{"turn": 3, "granted": 4, "progress_epoch": 2}]
    restored = AgentSession.from_dict(session.to_dict())
    assert restored.earned_tool_extension == 4
    assert restored.tool_extension_cycles == 1
    assert restored.committed_progress_epoch == 3
    assert restored.last_extension_progress_epoch == 2
    assert restored.committed_progress_history == session.committed_progress_history
    assert restored.tool_extension_history == session.tool_extension_history


def test_config_validates_transactional_authority_fields():
    assert validar_config({"agent": {"committed_progress_extension_calls": 4}})
    with pytest.raises(ConfigError, match="committed_progress_extension_calls"):
        validar_config({"agent": {"committed_progress_extension_calls": 0}})
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD:agent:max_earned_tool_extension"):
        validar_config({"agent": {"max_earned_tool_extension": 8}})


def test_history_ui_keeps_expand_all_and_shows_contract_authority():
    source = (Path(__file__).parents[1] / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'expandAll.textContent = "expandir tudo"' in source
    assert 'panel.querySelectorAll("details.history-item")' in source
    assert 'expandAll.textContent = shouldOpen ? "recolher tudo" : "expandir tudo"' in source
    assert 'historyLine("extensão conquistada", agent.earned_tool_extension)' in source
    assert 'Committed progress · ${commits.length} depósito(s)' in source
    assert 'Extensões de tools · ${extensions.length} ciclo(s)' in source
