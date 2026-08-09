import copy
import hashlib
import json

import pytest

import eyle.core.agent as core_agent
import llm.executar as llm_exec
from eyle.core.claim_review import (
    ClaimConfigError, claim_config, insufficient_feedback, normalize_claim_review,
    semantic_gap_protocol_recovery_target,
)
from eyle.core.session import AgentSession
from eyle.core.tools import TOOLS, executar_tool
from eyle.runtime.config import ConfigError, validar_config
from llm.structured import (
    StructuredResponseError,
    parse_agent_response,
    parse_claim_repair_response,
    parse_claim_review_response,
)


def _agent_envelope(*, action="final", final=None, calls=None, patches=None, question=None, investigation=None, scope=None):
    return {
        "action": action,
        "tool_calls": calls,
        "patches": patches,
        "needs_user": question,
        "final": final,
        "workspace_scope": dict(scope or {"mode": "none", "reason": "fixture is workspace-independent"}),
        "investigation": list(investigation or []),
    }


def _review(claims=None, findings=None, gaps=None):
    return {"claims": list(claims or []), "findings": list(findings or []), "semantic_gaps": list(gaps or [])}


def _config(mode="off"):
    return {
        "llm": {
            "base_url": "http://127.0.0.1:8080", "model": "explicit", "openai_compatible": True,
            "context_window_tokens": 32768, "connect_timeout_seconds": 5, "read_timeout_seconds": 120,
            "retry_max_attempts": 1, "agent_retry_max_attempts": 1, "max_concurrent_requests": 1,
            "agent_decision_max_tokens": 1100, "agent_analysis_max_tokens": 1800, "agent_patch_max_tokens": 3600,
        },
        "context_engine": {"safety_margin_tokens": 500, "chars_per_token_fallback": 3, "working_set_target_tokens": 12000},
        "agent": {
            "max_llm_turns": 8, "max_tool_calls": 12, "max_identical_tool_repeats": 2,
            "max_patch_dry_run_failures": 2, "max_write_investigation_turns": 2,
            "max_no_progress_turns": 2, "max_phase_violations": 1,
            "max_llm_calls": 12, "max_prompt_tokens": 96000, "max_completion_tokens": 9000,
            "max_total_tokens": 105000, "task_deadline_seconds": 900,
            "chat_history_token_budget": 700,
            "max_read_range_lines": 400, "max_tree_entries": 200, "max_tree_depth": 6,
            "max_project_scan_entries": 20000, "max_project_scan_depth": 32,
            "max_project_file_bytes": 4194304, "max_inspect_relation_edges": 60,
            "max_search_range_lines": 16, "max_search_matches": 40, "max_search_ranges": 12,
            "max_secret_scan_bytes": 65536, "max_git_diff_chars": 6000,
            "structured_protocol_retries": 1, "final_validation_retries": 1,
            "context_view": {"max_relevant_sources": 4, "max_relevant_source_chars": 3500, "max_search_source_chars": 600, "max_symbol_preview_chars": 2600},
            "claims": {"mode": mode, "require_supported": True, "verifier": {"max_tokens": 900, "temperature": 0.0}, "evidence": {"max_chars_per_item": 2200}, "repair": {"enabled": True, "max_attempts": 1}},
        },
        "codar": {"ativado": True, "testes": {"ativado": False}},
        "_runtime_agent_budget": {"max_llm_calls": 12, "max_prompt_tokens": 96000, "max_completion_tokens": 9000, "max_total_tokens": 105000, "llm_calls": 0, "llm_requests": 0},
    }


def test_profile_parsers_accept_only_canonical_shapes():
    with pytest.raises(StructuredResponseError):
        parse_agent_response({"tool": "list_tree", "arguments": {}})
    with pytest.raises(StructuredResponseError):
        parse_claim_review_response({"semantic_gaps": []})
    with pytest.raises(StructuredResponseError):
        parse_claim_repair_response({"claim_id": "c1"})

    parsed = parse_agent_response(_agent_envelope(action="tool_calls", calls=[{"tool": "list_tree", "arguments": {}}]))
    assert parsed == {
        "tool_calls": [{"tool": "list_tree", "arguments": {}}],
        "workspace_scope": {"mode": "none", "reason": "fixture is workspace-independent"},
        "investigation": [],
    }


def test_claim_normalizer_only_checks_runtime_authority_after_parse():
    raw = parse_claim_review_response(_review(claims=[{
        "id": "claim-1", "answer_ref": "a1", "target_id": None, "statement": "X is defined.", "kind": "fact",
        "evidence_ids": ["ev-1"], "verdict": "supported", "reason": "",
    }]))
    ok, reason, review = normalize_claim_review(
        raw, {"ev-1": {"arquivo": "x.py"}}, answer="X is defined.",
        answer_anchors=[{"id": "a1", "text": "X is defined."}], visible_evidence_ids=["ev-1"],
    )
    assert ok is True and reason == "ok"
    assert review["claims"][0]["answer_quote"] == "X is defined."


def test_claim_structural_retry_is_top_level_and_not_local(monkeypatch):
    session = AgentSession("onde X está definido?")
    session.evidence["ev-1"] = {"arquivo": "x.py", "linha_inicio": 1, "linha_fim": 1, "file_hash": "h", "content_hash": "c", "conteudo": "class X: pass"}
    calls = {"n": 0}

    def fake(_prompt, _config):
        calls["n"] += 1
        if calls["n"] == 1:
            raise llm_exec.ErroLLM(
                "missing claims", transient=False,
                error_code="STRUCTURED_RESPONSE_INVALID:claim_verifier:CLAIM_REVIEW_MISSING_KEYS",
            )
        return _review(claims=[{
            "id": "claim-1", "answer_ref": "a1", "target_id": None, "statement": "X is defined.", "kind": "fact",
            "evidence_ids": ["ev-1"], "verdict": "supported", "reason": "",
        }])

    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake)
    ok, reason, review, _ = core_agent._run_claim_verification(
        session, _config("self_check"), "X is defined.", ["ev-1"], project_root=None,
    )
    assert ok is True and reason == "ok"
    assert calls["n"] == 2
    assert len(review["claims"]) == 1


def test_local_claim_recovery_preserves_good_claims(monkeypatch):
    session = AgentSession("analise")
    session.evidence["ev-1"] = {"arquivo": "x.py", "linha_inicio": 1, "linha_fim": 1, "file_hash": "h", "content_hash": "c", "conteudo": "A. B."}
    global_review = _review(claims=[
        {"id": "claim-1", "answer_ref": "a1", "target_id": None, "statement": "A.", "kind": "fact", "evidence_ids": ["ev-1"], "verdict": "supported", "reason": ""},
        {"id": "claim-2", "answer_ref": "a2", "target_id": None, "statement": "B.", "kind": "fact", "evidence_ids": [], "verdict": "supported", "reason": ""},
    ])
    calls = []

    def fake(_prompt, _config):
        calls.append(1)
        if len(calls) == 1:
            return global_review
        return _review(claims=[
            {"id": "claim-2", "answer_ref": "a2", "target_id": None, "statement": "B.", "kind": "fact", "evidence_ids": ["ev-1"], "verdict": "supported", "reason": ""},
        ])

    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake)
    ok, reason, review, _ = core_agent._run_claim_verification(
        session, _config("self_check"), "A.\n\nB.", ["ev-1"], project_root=None,
    )
    assert ok is True and reason == "ok"
    assert [c["id"] for c in review["claims"]] == ["claim-1", "claim-2"]
    assert review["claims"][1]["evidence_ids"] == ["ev-1"]


def test_config_boundary_knows_only_current_fields():
    cfg = copy.deepcopy(_config("off"))
    cfg.pop("_runtime_agent_budget")
    validar_config(cfg)
    bad = copy.deepcopy(cfg); bad["llm"]["provider"] = "old"
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD:llm:provider"):
        validar_config(bad)
    bad = copy.deepcopy(cfg); bad["agent"]["context_view"]["enabled"] = True
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD:agent.context_view:enabled"):
        validar_config(bad)


def test_self_check_inherits_main_llm_and_verified_is_explicit():
    cfg = _config("self_check")
    resolved = claim_config(cfg)
    assert not {"base_url", "model", "openai_compatible"} & set(resolved["verifier"])

    verified = copy.deepcopy(cfg)
    verified["agent"]["claims"]["mode"] = "verified"
    verified["agent"]["claims"]["verifier"].update({
        "base_url": "http://127.0.0.1:9090", "model": "verifier", "openai_compatible": True,
    })
    assert claim_config(verified)["verifier"]["model"] == "verifier"


def test_public_tool_registry_has_no_patch_tools_and_rejects_aliases(tmp_path):
    assert len(TOOLS) == 16
    assert not {"apply_patch", "test_patch_dry_run", "apply_patch_set", "test_patch_set_dry_run"} & set(TOOLS)
    (tmp_path / "x.py").write_text("x=1\n", encoding="utf-8")
    result = executar_tool("search_code", {"pergunta": "x"}, {"projeto": {"caminho_origem": str(tmp_path)}, "config": {}})
    assert result["ok"] is False and result["error_code"] == "INVALID_ARGUMENT"


def test_direct_patch_action_creates_canonical_write_transaction(monkeypatch, tmp_path):
    source = tmp_path / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    calls = {"n": 0}

    def fake(_prompt, _config):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tool_calls": [{"tool": "read_file", "arguments": {"caminho_relativo": "app.py"}}], "workspace_scope": {"mode":"write","reason":"The active request asks to change app.py."}, "investigation": [{"id":"T1","goal":"Establish the current app.py before editing","status":"open","evidence_ids":[],"reason":""}]}
        return {"patches": [{"operation": "replace", "path": "app.py", "content": "VALUE = 2\n"}], "workspace_scope": {"mode":"write","reason":"The active request asks to change app.py."}, "investigation": [{"id":"T1","goal":"Establish the current app.py before editing","status":"established","evidence_ids":["ev-0001"],"reason":"app.py was read"}]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, _ = core_agent.executar_agente(
        "Altere app.py para VALUE = 2", _config("off"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert pending["continuation_kind"] == "write_confirmation"
    assert pending["write_transaction"]["patches"][0]["file_hash_expected"] == hashlib.sha256(b"VALUE = 1\n").hexdigest()
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_semantic_gap_recovery_target_is_local_and_does_not_reclassify():
    raw = _review(gaps=[
        {"id": "gap-1", "type": "material_omission", "evidence_ids": [], "reason": "usage path was omitted"},
    ])
    ok, reason, target, index = semantic_gap_protocol_recovery_target(
        raw, "CLAIM_REVIEW_SEMANTIC_GAP_EVIDENCE_REQUIRED:1:material_omission",
    )
    assert ok is True and reason == "ok" and index == 1
    assert target == {**raw["semantic_gaps"][0], "target_id": None}
    assert target["type"] == "material_omission"
    assert target["evidence_ids"] == []


def test_local_semantic_gap_recovery_preserves_review_and_can_reclassify(monkeypatch):
    session = AgentSession("explique definição, uso e fluxo")
    session.evidence["ev-1"] = {
        "arquivo": "session.py", "linha_inicio": 1, "linha_fim": 10,
        "file_hash": "h", "content_hash": "c", "conteudo": "class AgentSession: pass",
    }
    global_review = _review(
        claims=[{
            "id": "claim-1", "answer_ref": "a1", "target_id": None, "statement": "AgentSession is defined.",
            "kind": "fact", "evidence_ids": ["ev-1"], "verdict": "supported", "reason": "",
        }],
        findings=[{"id": "finding-1", "type": "fact", "claim_ids": ["claim-1"]}],
        gaps=[
            {"id": "gap-1", "type": "scope_gap", "evidence_ids": [], "reason": "one requested area remains partial"},
            {"id": "gap-2", "type": "material_omission", "evidence_ids": [], "reason": "runtime usage was not established"},
        ],
    )
    local_review = _review(gaps=[
        {"id": "gap-2", "type": "scope_gap", "evidence_ids": [], "reason": "runtime usage still requires targeted investigation"},
    ])
    calls = []

    def fake(_prompt, _config):
        calls.append(_prompt)
        return global_review if len(calls) == 1 else local_review

    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake)
    ok, reason, review, _ = core_agent._run_claim_verification(
        session, _config("self_check"), "AgentSession is defined.", ["ev-1"], project_root=None,
    )
    assert ok is True and reason == "ok"
    assert [item["id"] for item in review["claims"]] == ["claim-1"]
    assert review["findings"] == [{"id": "finding-1", "type": "fact", "claim_ids": ["claim-1"]}]
    assert [item["id"] for item in review["semantic_gaps"]] == ["gap-1", "gap-2"]
    assert review["semantic_gaps"][1]["type"] == "scope_gap"
    assert review["semantic_gaps"][1]["evidence_ids"] == []
    assert '"task":"reverify_semantic_gap"' in calls[1]
    assert '"id":"gap-2"' in calls[1]
    decisions = [item for item in session.decision_history if item.get("decision") == "semantic_gap_protocol_recovery"]
    assert [item.get("outcome") for item in decisions] == ["requested", "resolved"]


def test_local_semantic_gap_recovery_can_add_evidence_without_changing_type(monkeypatch):
    session = AgentSession("analise")
    session.evidence["ev-1"] = {
        "arquivo": "agent.py", "linha_inicio": 20, "linha_fim": 30,
        "file_hash": "h", "content_hash": "c", "conteudo": "session = AgentSession(...)"
    }
    global_review = _review(
        claims=[{
            "id": "claim-1", "answer_ref": "a1", "target_id": None, "statement": "A fact.", "kind": "fact",
            "evidence_ids": ["ev-1"], "verdict": "supported", "reason": "",
        }],
        gaps=[{"id": "gap-1", "type": "material_omission", "evidence_ids": [], "reason": "usage evidence omitted"}],
    )
    local_review = _review(gaps=[
        {"id": "gap-1", "type": "material_omission", "evidence_ids": ["ev-1"], "reason": "usage evidence was omitted"},
    ])
    calls = {"n": 0}

    def fake(_prompt, _config):
        calls["n"] += 1
        return global_review if calls["n"] == 1 else local_review

    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake)
    ok, reason, review, _ = core_agent._run_claim_verification(
        session, _config("self_check"), "A fact.", ["ev-1"], project_root=None,
    )
    assert ok is True and reason == "ok"
    assert review["semantic_gaps"][0]["type"] == "material_omission"
    assert review["semantic_gaps"][0]["evidence_ids"] == ["ev-1"]


def test_local_semantic_gap_recovery_can_remove_invalid_gap(monkeypatch):
    session = AgentSession("analise")
    session.evidence["ev-1"] = {
        "arquivo": "x.py", "linha_inicio": 1, "linha_fim": 1,
        "file_hash": "h", "content_hash": "c", "conteudo": "A"
    }
    global_review = _review(
        claims=[{
            "id": "claim-1", "answer_ref": "a1", "target_id": None, "statement": "A.", "kind": "fact",
            "evidence_ids": ["ev-1"], "verdict": "supported", "reason": "",
        }],
        gaps=[{"id": "gap-1", "type": "material_omission", "evidence_ids": [], "reason": "claimed omission"}],
    )
    calls = {"n": 0}

    def fake(_prompt, _config):
        calls["n"] += 1
        return global_review if calls["n"] == 1 else _review()

    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake)
    ok, reason, review, _ = core_agent._run_claim_verification(
        session, _config("self_check"), "A.", ["ev-1"], project_root=None,
    )
    assert ok is True and reason == "ok"
    assert review["semantic_gaps"] == []
    decisions = [item for item in session.decision_history if item.get("decision") == "semantic_gap_protocol_recovery"]
    assert decisions[-1]["outcome"] == "removed"


def test_semantic_gap_feedback_returns_exact_recovered_gap_to_agent():
    review = {
        "claims": [],
        "semantic_gaps": [{
            "id": "gap-2", "type": "scope_gap", "evidence_ids": [],
            "reason": "runtime usage still requires targeted investigation", "signature": "sig",
        }],
    }
    payload = json.loads(insufficient_feedback(review))
    assert payload["semantic_gaps"] == [{
        "id": "gap-2", "type": "scope_gap", "target_id": None, "evidence_ids": [],
        "reason": "runtime usage still requires targeted investigation", "signature": "sig",
    }]
    assert "You decide the next action" in payload["instruction"]


def test_claim_verifier_prompt_states_semantic_gap_evidence_rules_concisely():
    prompt = llm_exec.PROMPT_CLAIM_VERIFIER
    assert "material_omission" in prompt and "MUST cite at least one supplied evidence_id" in prompt
    assert "scope_gap" in prompt and "MAY use evidence_ids=[]" in prompt
    assert "task=reverify_semantic_gap" in prompt
    assert "do not change type merely to satisfy the protocol" in prompt


def test_multiple_semantic_gap_recoveries_survive_index_shift_after_removal(monkeypatch):
    session = AgentSession("analise")
    session.evidence["ev-1"] = {
        "arquivo": "x.py", "linha_inicio": 1, "linha_fim": 1,
        "file_hash": "h", "content_hash": "c", "conteudo": "A",
    }
    global_review = _review(
        claims=[{
            "id": "claim-1", "answer_ref": "a1", "target_id": None, "statement": "A.", "kind": "fact",
            "evidence_ids": ["ev-1"], "verdict": "supported", "reason": "",
        }],
        gaps=[
            {"id": "gap-1", "type": "material_omission", "evidence_ids": [], "reason": "first"},
            {"id": "gap-2", "type": "conflicting_evidence", "evidence_ids": [], "reason": "second"},
        ],
    )
    replies = [
        global_review,
        _review(),
        _review(gaps=[{
            "id": "gap-2", "type": "scope_gap", "evidence_ids": [], "reason": "needs more investigation",
        }]),
    ]

    def fake(_prompt, _config):
        return replies.pop(0)

    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake)
    ok, reason, review, _ = core_agent._run_claim_verification(
        session, _config("self_check"), "A.", ["ev-1"], project_root=None,
    )
    assert ok is True and reason == "ok"
    assert [(g["id"], g["type"]) for g in review["semantic_gaps"]] == [("gap-2", "scope_gap")]
