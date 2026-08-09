import json

import pytest

import eyle.core.agent as core_agent
from eyle.core.claim_review import normalize_claim_review
from eyle.core.investigation import reopen_targets_from_review
from eyle.core.session import AgentSession
from llm.structured import StructuredResponseError, parse_agent_response, parse_claim_review_response
from tests.canonical import base_config, investigation_target, workspace_scope


def _agent_envelope(final):
    return {
        "action": "final",
        "tool_calls": None,
        "patches": None,
        "needs_user": None,
        "final": final,
        "workspace_scope": {"mode": "none", "reason": "fixture is workspace-independent"},
        "investigation": [],
    }


def _evidence(path="app.py", line=1):
    return {
        "arquivo": path,
        "linha_inicio": line,
        "linha_fim": line,
        "file_hash": f"fh-{path}-{line}",
        "content_hash": f"ch-{path}-{line}",
        "conteudo": "x = 1",
    }


def test_agent_final_contract_is_canonical_before_runtime_final_gate():
    with pytest.raises(StructuredResponseError) as exc:
        parse_agent_response(_agent_envelope({"answer": "ok", "evidence_ids": []}))
    assert exc.value.code == "AGENT_FINAL_SHAPE_INVALID"

    parsed = parse_agent_response(_agent_envelope({
        "answer": "ok", "evidence_ids": [], "limitations": [],
    }))
    assert parsed["final"] == {"answer": "ok", "evidence_ids": [], "limitations": []}


def test_insufficient_claim_can_explicitly_reopen_existing_target_without_semantic_gap():
    investigation = [
        investigation_target(
            "T3", goal="Establish the real execution path", status="established",
            evidence_ids=["ev-1"], reason="initially considered sufficient",
        )
    ]
    review = {
        "claims": [{
            "id": "c3", "answer_ref": "a1", "answer_quote": "AgentSession drives the flow.",
            "target_id": "T3", "statement": "AgentSession drives the real execution flow.",
            "kind": "fact", "evidence_ids": ["ev-1"], "verdict": "insufficient",
            "reason": "construction is visible, but participation in the execution loop is not",
        }],
        "findings": [],
        "semantic_gaps": [],
    }
    updated, reopened = reopen_targets_from_review(investigation, review)
    assert reopened == ["T3"]
    assert updated[0]["status"] == "open"
    assert "execution loop" in updated[0]["reason"]


def test_claim_target_id_is_semantic_input_but_runtime_validates_identity():
    raw = parse_claim_review_response({
        "claims": [{
            "id": "c1", "answer_ref": "a1", "target_id": "T404",
            "statement": "x is 1", "kind": "fact", "evidence_ids": ["ev-1"],
            "verdict": "supported", "reason": "",
        }],
        "findings": [],
        "semantic_gaps": [],
    })
    ok, reason, _ = normalize_claim_review(
        raw, {"ev-1": _evidence()}, request="Analise app.py", answer="x is 1",
        visible_evidence_ids=["ev-1"],
        investigation=[investigation_target("T1", status="established", evidence_ids=["ev-1"], reason="seen")],
    )
    assert ok is False
    assert reason == "CLAIM_REVIEW_UNKNOWN_TARGET:1:T404"


def test_semantic_followup_no_progress_never_orders_agent_to_stop_investigating():
    session = AgentSession("Analise o projeto")
    session.claim_followup_pending = True
    session.evidence["ev-1"] = _evidence()
    session.investigation = [
        investigation_target(
            "T3", goal="Establish the real execution path", status="open",
            evidence_ids=["ev-1"], reason="reviewer says more evidence is needed",
        )
    ]
    payload = json.loads(core_agent._semantic_followup_stalled_feedback(session))
    assert payload["code"] == "SEMANTIC_FOLLOWUP_STALLED"
    assert payload["open_targets"][0]["id"] == "T3"
    assert "Runtime does not choose the tool" in payload["instruction"]
    assert "stop using tools" not in payload["instruction"].lower()


def test_semantic_followup_reserves_only_known_next_claim_review_budget(tmp_path):
    session = AgentSession("Analise o projeto")
    session.claim_followup_pending = True
    cfg = base_config(claims_mode="self_check")
    call_cfg = core_agent._agent_config(cfg, session, {"caminho_origem": str(tmp_path)})
    assert call_cfg["llm"]["downstream_completion_reserve_tokens"] == 900

    session.claim_review = {
        "claims": [{"id": f"c{i}"} for i in range(5)],
        "semantic_gaps": [],
    }
    elastic_cfg = core_agent._agent_config(cfg, session, {"caminho_origem": str(tmp_path)})
    assert elastic_cfg["llm"]["downstream_completion_reserve_tokens"] == 900

    session.claim_followup_pending = False
    normal_cfg = core_agent._agent_config(cfg, session, {"caminho_origem": str(tmp_path)})
    assert "downstream_completion_reserve_tokens" not in normal_cfg["llm"]


def test_end_to_end_insufficient_claim_reopens_target_and_main_llm_changes_observation(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from app import run\nprint(run())\n", encoding="utf-8")
    cfg = base_config(claims_mode="self_check")
    agent_prompts = []
    verifier_calls = {"n": 0}
    goal = "Establish app.py's role in the real execution path"

    def fake_agent(prompt, _cfg):
        payload = json.loads(prompt)
        agent_prompts.append(payload)
        turn = len(agent_prompts)
        if turn == 1:
            return {
                "tool_calls": [{"tool": "read_file", "arguments": {"caminho_relativo": "app.py"}}],
                "workspace_scope": workspace_scope("read"),
                "investigation": [investigation_target("T3", goal=goal)],
            }
        if turn == 2:
            return {
                "final": {"answer": "app.py defines run().", "evidence_ids": ["ev-0001"], "limitations": []},
                "workspace_scope": workspace_scope("read"),
                "investigation": [investigation_target(
                    "T3", goal=goal, status="established", evidence_ids=["ev-0001"], reason="run is defined",
                )],
            }
        if turn == 3:
            target = payload["investigation"][0]
            assert target["id"] == "T3" and target["status"] == "open"
            feedback = json.loads(payload["runtime_feedback"])
            assert feedback["claims"][0]["target_id"] == "T3"
            return {
                "tool_calls": [{"tool": "read_file", "arguments": {"caminho_relativo": "main.py"}}],
                "workspace_scope": workspace_scope("read"),
                "investigation": [target],
            }
        return {
            "final": {
                "answer": "main.py imports run from app.py and calls it.",
                "evidence_ids": ["ev-0001", "ev-0002"], "limitations": [],
            },
            "workspace_scope": workspace_scope("read"),
            "investigation": [investigation_target(
                "T3", goal=goal, status="established", evidence_ids=["ev-0001", "ev-0002"],
                reason="definition and call site are both visible",
            )],
        }

    def fake_verifier(_prompt, _cfg):
        verifier_calls["n"] += 1
        if verifier_calls["n"] == 1:
            return {
                "claims": [{
                    "id": "c1", "answer_ref": "a1", "target_id": "T3",
                    "statement": "app.py defines run().", "kind": "fact",
                    "evidence_ids": ["ev-0001"], "verdict": "insufficient",
                    "reason": "definition alone does not establish the execution path",
                }],
                "findings": [], "semantic_gaps": [],
            }
        return {
            "claims": [{
                "id": "c1", "answer_ref": "a1", "target_id": "T3",
                "statement": "main.py imports and calls app.run().", "kind": "fact",
                "evidence_ids": ["ev-0002"], "verdict": "supported", "reason": "",
            }],
            "findings": [], "semantic_gaps": [],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_verifier)

    status, text, _, details = core_agent.executar_agente(
        "Analise o projeto e explique o papel de app.py no fluxo real.", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "main.py" in text
    assert details["tool_calls"] == 2
    reopened = [
        item for item in details["decision_history"]
        if item.get("decision") == "investigation_contract" and item.get("outcome") == "reopened"
    ]
    assert reopened and reopened[-1]["reason"] == "T3"
    accepted = [
        item for item in details["decision_history"]
        if item.get("decision") == "investigation_contract" and item.get("outcome") == "accepted"
    ]
    assert any("T3=open" in str(item.get("reason")) for item in accepted)
    assert any("T3=established" in str(item.get("reason")) for item in accepted)
    assert verifier_calls["n"] == 2
