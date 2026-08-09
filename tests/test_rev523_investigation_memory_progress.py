import json

from eyle.core import agent as core_agent
from eyle.core.session import AgentSession


def _source(path="app.py", start=1, end=2, file_hash="fh", evidence_id="ev-0001"):
    return {
        "arquivo": path,
        "linha_inicio": start,
        "linha_fim": end,
        "total_linhas_arquivo": end,
        "file_hash": file_hash,
        "content_hash": "ch",
        "trecho_numerado": "1 | VALUE = 1\n2 | VALUE = 2",
        "evidence_id": evidence_id,
    }


def test_visible_source_ranges_are_current_prompt_only_and_history_is_separate():
    session = AgentSession("inspect app")
    payload = {"relevant_sources": [_source()]}
    core_agent._record_prompt_visible_ranges(session, payload)

    assert "app.py" in session.visible_source_ranges
    assert "app.py" in session.historically_seen_source_ranges
    assert core_agent._read_already_covered(
        session,
        "read_range",
        {"caminho_relativo": "app.py", "linha_inicio": 1, "linha_fim": 2},
    ) is True

    # Next compiled prompt no longer carries the body. Historical telemetry must
    # not make the stateless Main LLM unable to read it again.
    core_agent._record_prompt_visible_ranges(session, {"relevant_sources": []})
    assert session.visible_source_ranges == {}
    assert "app.py" in session.historically_seen_source_ranges
    assert core_agent._read_already_covered(
        session,
        "read_range",
        {"caminho_relativo": "app.py", "linha_inicio": 1, "linha_fim": 2},
    ) is False


def test_semantic_followup_pins_reopened_target_and_verifier_evidence():
    session = AgentSession("inspect app")
    for n in range(1, 4):
        evidence_id = f"ev-000{n}"
        item = _source(path=f"f{n}.py", evidence_id=evidence_id)
        item["conteudo"] = f"VALUE = {n}\n"
        session.evidence[evidence_id] = item
    session.investigation = [
        {"id": "T1", "goal": "A", "status": "established", "evidence_ids": ["ev-0001"], "reason": "ok"},
        {"id": "T2", "goal": "B", "status": "open", "evidence_ids": ["ev-0002"], "reason": ""},
    ]
    review = {
        "claims": [
            {"id": "C1", "verdict": "insufficient", "evidence_ids": ["ev-0003"], "target_id": "T2"},
        ],
        "semantic_gaps": [],
    }

    core_agent._pin_semantic_followup_evidence(session, review, ["T2"])
    assert session.followup_pinned_evidence_ids == ["ev-0002", "ev-0003"]

    retained = core_agent._retained_sources_for_prompt(session, {})
    retained_ids = {item.get("evidence_id") for item in retained}
    assert {"ev-0002", "ev-0003"}.issubset(retained_ids)
    assert all(item.get("pinned") is True for item in retained if item.get("evidence_id") in {"ev-0002", "ev-0003"})


def test_success_without_new_evidence_or_state_change_is_not_progress():
    session = AgentSession("audit")
    session.evidence["ev-0001"] = {"arquivo": "<tool:project_stats>"}
    results = [
        {"tool": "project_stats", "status": "success", "ok": True, "executed": True, "changed": False},
        {"tool": "inspect_project", "status": "success", "ok": True, "executed": True, "changed": False},
    ]
    assert core_agent._turn_made_progress(1, results, session) is False


def test_observation_signature_is_reusable_until_workspace_changes():
    session = AgentSession("audit")
    signature = core_agent._semantic_read_signature("project_stats", {})
    successful = {"status": "success", "ok": True, "executed": True, "changed": False, "detail": {}}
    core_agent._record_tool_history(session, "project_stats", {}, successful, semantic_signature=signature)
    assert core_agent._previous_observation(session, signature) is not None

    changed = {"status": "success", "ok": True, "executed": True, "changed": True, "detail": {}}
    core_agent._record_tool_history(session, "memory_store", {}, changed)
    assert core_agent._previous_observation(session, signature) is None


def test_session_roundtrip_keeps_history_and_followup_pins_but_current_visibility_is_explicit():
    session = AgentSession("audit")
    session.visible_source_ranges = {"app.py": [{"start": 1, "end": 2, "file_hash": "fh", "total_lines": 2}]}
    session.historically_seen_source_ranges = {"app.py": [{"start": 1, "end": 2, "file_hash": "fh", "total_lines": 2}]}
    session.followup_pinned_evidence_ids = ["ev-0001"]
    restored = AgentSession.from_dict(session.to_dict())
    assert restored.visible_source_ranges == session.visible_source_ranges
    assert restored.historically_seen_source_ranges == session.historically_seen_source_ranges
    assert restored.followup_pinned_evidence_ids == ["ev-0001"]


def test_claim_insufficient_keeps_reviewer_source_body_visible_in_next_prompt(monkeypatch, tmp_path):
    from tests.canonical import base_config, investigation_target, workspace_scope

    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    cfg = base_config(claims_mode="self_check")
    prompts = []
    verifier_calls = {"n": 0}
    goal = "Establish the current value in app.py"

    def fake_agent(prompt, _cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        turn = len(prompts)
        if turn == 1:
            return {
                "tool_calls": [{"tool": "read_range", "arguments": {"caminho_relativo": "app.py", "linha_inicio": 1, "linha_fim": 1}}],
                "workspace_scope": workspace_scope("read"),
                "investigation": [investigation_target("T1", goal=goal)],
            }
        if turn == 2:
            return {
                "final": {"answer": "app.py has VALUE = 1.", "evidence_ids": ["ev-0001"], "limitations": []},
                "workspace_scope": workspace_scope("read"),
                "investigation": [investigation_target("T1", goal=goal, status="established", evidence_ids=["ev-0001"], reason="value read")],
            }
        # After CLAIM_INSUFFICIENT ordinary relevant_sources/latest results were
        # cleared. The reviewer Evidence body must nevertheless be back in the
        # CURRENT prompt, not only in historical coverage.
        pinned = [item for item in payload.get("relevant_sources") or [] if item.get("evidence_id") == "ev-0001"]
        assert pinned and "VALUE = 1" in (pinned[0].get("trecho_numerado") or "")
        assert pinned[0].get("pinned") is True
        return {
            "final": {"answer": "app.py has VALUE = 1; no broader claim is made.", "evidence_ids": ["ev-0001"], "limitations": []},
            "workspace_scope": workspace_scope("read"),
            "investigation": [investigation_target("T1", goal=goal, status="established", evidence_ids=["ev-0001"], reason="narrowed to directly observed value")],
        }

    def fake_verifier(_prompt, _cfg):
        verifier_calls["n"] += 1
        if verifier_calls["n"] == 1:
            return {
                "claims": [{
                    "id": "c1", "answer_ref": "a1", "target_id": "T1",
                    "statement": "app.py has VALUE = 1.", "kind": "fact",
                    "evidence_ids": ["ev-0001"], "verdict": "insufficient",
                    "reason": "Narrow the conclusion to the exact observed source fact.",
                }],
                "findings": [], "semantic_gaps": [],
            }
        return {
            "claims": [{
                "id": "c1", "answer_ref": "a1", "target_id": "T1",
                "statement": "app.py has VALUE = 1.", "kind": "fact",
                "evidence_ids": ["ev-0001"], "verdict": "supported", "reason": "",
            }],
            "findings": [], "semantic_gaps": [],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_verifier)
    status, text, _, details = core_agent.executar_agente(
        "Read app.py and state only what is confirmed.", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert details["tool_calls"] == 1
    assert verifier_calls["n"] == 2


def test_repeated_project_stats_and_inspect_project_do_not_execute_forever(monkeypatch, tmp_path):
    from tests.canonical import base_config, investigation_target, workspace_scope

    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    cfg = base_config(claims_mode="off")
    cfg["agent"]["max_llm_turns"] = 4
    goal = "Establish enough architecture facts"
    calls = {"n": 0}

    def fake_agent(_prompt, _cfg):
        calls["n"] += 1
        tool = "project_stats" if calls["n"] % 2 else "inspect_project"
        return {
            "tool_calls": [{"tool": tool, "arguments": {}}],
            "workspace_scope": workspace_scope("read"),
            "investigation": [investigation_target("T1", goal=goal)],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    status, _text, _pending, details = core_agent.executar_agente(
        "Audit the project.", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "failed"
    # First stats + first inspect are distinct observations. The second copy of
    # each is suppressed instead of consuming two more physical tool calls.
    assert details["tool_calls"] == 2
    skipped = [
        item for item in details["decision_history"]
        if item.get("decision") == "tool_execution" and item.get("reason") == "IDENTICAL_OBSERVATION_BLOCKED"
    ]
    assert len(skipped) >= 1
    phase_rejected = [
        item for item in details["decision_history"]
        if item.get("decision") == "tool_validation" and item.get("reason") == "FINAL_PHASE_REQUIRES_ANSWER"
    ]
    assert phase_rejected


def test_run_tests_signature_is_cached_until_a_state_change():
    session = AgentSession("fix tests")
    signature = core_agent._semantic_read_signature("run_tests", {"scope": "tests/test_x.py"})
    failed = {
        "status": "failed", "ok": False, "executed": True, "changed": False,
        "error_code": "TESTS_FAILED", "detail": {},
    }
    core_agent._record_tool_history(
        session, "run_tests", {"scope": "tests/test_x.py"}, failed,
        semantic_signature=signature,
    )
    assert core_agent._previous_observation(session, signature) is not None

    changed = {"status": "success", "ok": True, "executed": True, "changed": True, "detail": {}}
    core_agent._record_tool_history(session, "memory_store", {}, changed)
    assert core_agent._previous_observation(session, signature) is None
