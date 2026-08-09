import json
from pathlib import Path

import eyle.core.agent as core_agent
from eyle.core.claim_review import claim_config
from eyle.core.investigation import reopen_targets_from_review
from eyle.core.session import AgentSession
from llm import structured
from tests.canonical import base_config, investigation_target, workspace_scope


def test_only_two_semantic_profiles_exist():
    assert set(structured._PROFILE_SCHEMAS) == {"agent", "claim_verifier"}
    assert set(structured._PROFILE_NAMES) == {"agent", "claim_verifier"}
    assert set(structured._PROFILE_TOP_LEVEL) == {"agent", "claim_verifier"}


def test_claim_config_rejects_removed_repair_route():
    cfg = base_config(claims_mode="self_check")
    cfg["agent"]["claims"]["repair"] = {"enabled": True, "max_attempts": 1}
    try:
        claim_config(cfg)
    except Exception as exc:
        assert "UNKNOWN_CONFIG_FIELD:agent.claims:repair" in str(exc)
    else:
        raise AssertionError("removed Claim Repair route must not remain accepted as legacy config")


def test_contradicted_claim_reopens_exact_existing_target():
    investigation = [
        investigation_target(
            "T3", goal="Establish real usage", status="established",
            evidence_ids=["ev-1"], reason="provisional conclusion",
        )
    ]
    review = {
        "claims": [{
            "id": "c3", "answer_ref": "a1", "target_id": "T3",
            "statement": "symbols.py is unused", "kind": "fact",
            "evidence_ids": ["ev-2"], "verdict": "contradicted",
            "reason": "editing.py calls extract_symbols",
        }],
        "findings": [], "semantic_gaps": [],
    }
    updated, reopened = reopen_targets_from_review(investigation, review)
    assert reopened == ["T3"]
    assert updated[0]["status"] == "open"
    assert updated[0]["evidence_ids"] == ["ev-1", "ev-2"]
    assert updated[0]["reason"] == "editing.py calls extract_symbols"


def test_claim_rework_lane_uses_only_remaining_global_llm_capacity():
    session = AgentSession("audit")
    session.turn = 8
    cfg = base_config(claims_mode="self_check")
    cfg["agent"]["max_llm_turns"] = 8
    cfg["agent"]["max_llm_calls"] = 12
    cfg["_runtime_agent_budget"]["max_llm_calls"] = 12
    cfg["_runtime_agent_budget"]["llm_calls"] = 9
    # 3 physical calls remain. Runtime reserves one later Claim pass, therefore
    # only two additional Main-LLM turns may become available.
    assert core_agent._extend_claim_rework_lane(session, cfg, 8) == 10


def test_claim_protocol_recovery_preserves_target_id(monkeypatch, tmp_path):
    cfg = base_config(claims_mode="self_check")
    session = AgentSession("audit")
    session.workspace_scope = workspace_scope("read")
    session.investigation = [
        investigation_target("T3", goal="Establish usage", status="established", evidence_ids=["ev-1"], reason="seen")
    ]
    path = tmp_path / "app.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    import hashlib
    text = path.read_text(encoding="utf-8")
    h = hashlib.sha256(text.encode()).hexdigest()
    session.evidence["ev-1"] = {
        "arquivo": "app.py", "linha_inicio": 1, "linha_fim": 1,
        "file_hash": h, "content_hash": h, "conteudo": text,
    }

    calls = {"n": 0}
    def fake_verifier(prompt, _cfg):
        payload = json.loads(prompt)
        calls["n"] += 1
        if calls["n"] == 1:
            # Structurally parsed but semantically invalid: supported fact lacks Evidence.
            return {
                "claims": [{
                    "id": "c1", "answer_ref": "a1", "target_id": "T3",
                    "statement": "VALUE is 1", "kind": "fact", "evidence_ids": [],
                    "verdict": "supported", "reason": "",
                }],
                "findings": [], "semantic_gaps": [],
            }
        assert payload["task"] == "reverify_claims"
        assert payload["target_claims"][0]["target_id"] == "T3"
        return {
            "claims": [{
                "id": "c1", "answer_ref": "a1", "target_id": "T3",
                "statement": "VALUE is 1", "kind": "fact", "evidence_ids": ["ev-1"],
                "verdict": "supported", "reason": "",
            }],
            "findings": [], "semantic_gaps": [],
        }

    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_verifier)
    ok, reason, review, _view = core_agent._run_claim_verification(
        session, cfg, "VALUE is 1", ["ev-1"], project_root=str(tmp_path),
    )
    assert ok is True, reason
    assert review["claims"][0]["target_id"] == "T3"


def test_identical_reviewer_debt_stalls_without_third_brain(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    cfg = base_config(claims_mode="self_check")
    cfg["agent"]["max_llm_turns"] = 4
    cfg["agent"]["max_llm_calls"] = 12
    cfg["_runtime_agent_budget"]["max_llm_calls"] = 12
    agent_calls = {"n": 0}

    def fake_agent(prompt, _cfg):
        payload = json.loads(prompt)
        agent_calls["n"] += 1
        if agent_calls["n"] == 1:
            return {
                "tool_calls": [{"tool": "read_file", "arguments": {"path": "a.py"}}],
                "workspace_scope": workspace_scope("read"),
                "investigation_updates": [investigation_target("T1", goal="Establish A")],
            }
        # Deliberately repeat the same semantic conclusion/state after review.
        if agent_calls["n"] > 2:
            feedback = payload.get("runtime_feedback")
            if isinstance(feedback, str):
                feedback = json.loads(feedback)
            assert feedback["code"] == "CLAIM_REVIEW_FOLLOWUP"
            assert feedback["runtime_capacity"]["agent_calls_before_reserved_verifier"] >= 1
            assert "pending_progress_cycles" in feedback["runtime_capacity"]
        return {
            "final": {"answer": "A is absent.", "evidence_ids": ["ev-0001"], "limitations": []},
            "workspace_scope": workspace_scope("read"),
            "investigation_updates": [
                investigation_target("T1", goal="Establish A", status="established", evidence_ids=["ev-0001"], reason="same conclusion")
            ],
        }

    def fake_verifier(_prompt, _cfg):
        return {
            "claims": [{
                "id": "c1", "answer_ref": "a1", "target_id": "T1",
                "statement": "A is absent", "kind": "fact", "evidence_ids": ["ev-0001"],
                "verdict": "contradicted", "reason": "a.py shows A = 1",
            }],
            "findings": [], "semantic_gaps": [],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_verifier)
    status, text, _pending, details = core_agent.executar_agente(
        "Audit A", cfg, projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] == "CLAIM_REVIEW_STALLED"
    assert "repetição de tokens" in text
    assert details["repeated_rejected_decisions"] >= 1
    assert not any(item.get("mode") == "claim_repair" for item in details.get("prompt_snapshots", []))


def test_reestablishing_reviewer_reopened_target_with_same_evidence_mints_no_credit():
    from eyle.core.investigation import apply_investigation_updates
    previous = [
        investigation_target(
            "T1", goal="Establish A", status="open",
            evidence_ids=["ev-1"], reason="Claim contradicted prior conclusion",
        )
    ]
    canonical, accepted, rejected, progress = apply_investigation_updates(
        [investigation_target(
            "T1", goal="Establish A", status="established",
            evidence_ids=["ev-1"], reason="same Evidence, new semantic interpretation",
        )],
        previous=previous,
        evidence={"ev-1": {"arquivo": "a.py"}},
    )
    assert rejected == [] and accepted
    assert canonical[0]["status"] == "established"
    assert progress == []
