import json
import multiprocessing
import os
import subprocess
import time
from pathlib import Path

import pytest

import eyle.core.agent as core_agent
import eyle.core.request_policy as request_policy
from eyle.core.session import AgentSession
from eyle.core.tools import executar_tool
from eyle.core.workspace_io import ler_faixa_projeto
from eyle.runtime.lock import lock_para
from llm.executar import _preflight_completion_budget
from tests.canonical import base_config, investigation_target, workspace_scope


def _locked_append(path_text, value, delay=0.05):
    path = Path(path_text)
    with lock_para(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = []
        time.sleep(delay)
        data.append(value)
        path.write_text(json.dumps(data), encoding="utf-8")


def test_followup_reserve_is_fixed_to_one_verifier_call_even_after_large_review(tmp_path):
    cfg = base_config(claims_mode="self_check")
    cfg["_runtime_agent_budget"]["generated_tokens"] = 4474
    session = AgentSession("analise")
    session.claim_followup_pending = True
    session.claim_review = {
        "claims": [
            {"id": f"c{i}", "verdict": "supported", "target_id": None}
            for i in range(20)
        ],
        "semantic_gaps": [{"id": f"g{i}"} for i in range(10)],
    }
    resolved = core_agent._agent_config(cfg, session, {"caminho_origem": str(tmp_path)})
    assert resolved["llm"]["downstream_completion_reserve_tokens"] == 900
    # This is the exact shape that used to die with ~4.5k completion tokens left.
    check = _preflight_completion_budget(resolved, 1100)
    assert check["remaining"] == 4526
    assert check["requested"] == 1100
    assert check["downstream_reserve"] == 900


def test_main_llm_workspace_scope_not_legacy_regex_controls_grounding(monkeypatch, tmp_path):
    (tmp_path / "session.py").write_text("class AgentSession:\n    pass\n", encoding="utf-8")
    # Rev5.2.8 removes the legacy lexical authority helpers entirely.
    assert not hasattr(request_policy, "request_needs_project_evidence")
    assert not hasattr(request_policy, "request_requires_write")
    outputs = iter([
        {
            "tool_calls": [{"tool": "read_file", "arguments": {"path": "session.py"}}],
            "workspace_scope": workspace_scope("read", "Answer depends on current AgentSession code."),
            "investigation_updates": [investigation_target(goal="Establish what AgentSession does")],
        },
        {
            "final": {"answer": "AgentSession is defined in session.py.", "evidence_ids": ["ev-0001"], "limitations": []},
            "workspace_scope": workspace_scope("read", "Answer depends on current AgentSession code."),
            "investigation_updates": [investigation_target(status="established", evidence_ids=["ev-0001"], reason="session.py was read", goal="Establish what AgentSession does")],
        },
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda _p, _c: next(outputs))
    status, text, _, details = core_agent.executar_agente(
        "O que AgentSession faz?", base_config(claims_mode="off"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "AgentSession" in text
    assert details["workspace_scope"]["mode"] == "read"
    assert details["tool_calls"] == 1


def test_main_llm_can_declare_write_for_wording_legacy_regex_misses(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert not hasattr(request_policy, "request_requires_write")
    prompts = []

    def fake(prompt, _cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return {
                "tool_calls": [{"tool": "read_file", "arguments": {"path": "app.py"}}],
                "workspace_scope": workspace_scope("write", "The user asks to make the code work."),
                "investigation_updates": [investigation_target(goal="Establish current app.py before changing it")],
            }
        return {
            "patches": [{"operation": "replace", "path": "app.py", "content": "VALUE = 2\n"}],
            "workspace_scope": workspace_scope("write", "The user asks to make the code work."),
            "investigation_updates": [investigation_target(
                goal="Establish current app.py before changing it", status="established",
                evidence_ids=["ev-0001"], reason="app.py was read",
            )],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, details = core_agent.executar_agente(
        "Faça funcionar o código", base_config(claims_mode="off"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert pending["continuation_kind"] == "write_confirmation"
    assert details["workspace_scope"]["mode"] == "write"


def test_open_investigation_blocks_patch_and_confirmed_resume(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    cfg = base_config(claims_mode="off")
    session = AgentSession("mude app.py")
    session.workspace_scope = workspace_scope("write")
    session.investigation = [investigation_target(goal="Establish current app.py")]
    pending = {
        "continuation_kind": "write_confirmation",
        "write_transaction": {"patches": [{
            "operation": "replace", "path": "app.py", "content": "VALUE = 2\n",
            "file_hash_expected": "unused-because-gate-runs-first",
        }]},
    }
    called = {"apply": False}
    monkeypatch.setattr(core_agent, "apply_patch_set", lambda *_a, **_k: called.__setitem__("apply", True))
    status, _, _, details = core_agent._resume(session, pending, cfg, {"caminho_origem": str(tmp_path)}, True)
    assert status == "failed"
    assert called["apply"] is False
    assert str(details["failure_code"]).startswith("WRITE_INVESTIGATION_TARGET_OPEN:T1")
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_lock_para_serializes_updates_across_processes(tmp_path):
    path = tmp_path / "conversation.json"
    path.write_text("[]", encoding="utf-8")
    ctx = multiprocessing.get_context("spawn")
    a = ctx.Process(target=_locked_append, args=(str(path), "A"))
    b = ctx.Process(target=_locked_append, args=(str(path), "B"))
    a.start(); b.start()
    a.join(10); b.join(10)
    assert a.exitcode == 0 and b.exitcode == 0
    assert sorted(json.loads(path.read_text(encoding="utf-8"))) == ["A", "B"]


def test_unified_secret_policy_blocks_read_search_and_git_diff(tmp_path):
    (tmp_path / ".env").write_text("TOKEN=supersecret\n", encoding="utf-8")
    (tmp_path / "credentials.json").write_text('{"token":"supersecret"}\n', encoding="utf-8")
    (tmp_path / "normal.py").write_text("TOKEN=supersecret\n", encoding="utf-8")
    ctx = {"projeto": {"caminho_origem": str(tmp_path)}, "config": base_config(claims_mode="off")}

    read = executar_tool("read_file", {"path": ".env"}, ctx)
    assert read["ok"] is False and read["error_code"] == "SECRET_PATH_BLOCKED"

    normal_secret = executar_tool("read_file", {"path": "normal.py"}, ctx)
    assert normal_secret["ok"] is False and normal_secret["error_code"] == "SECRET_CONTENT_BLOCKED"

    search = executar_tool("search_code", {"query": "supersecret"}, ctx)
    detail = search.get("detail") or {}
    assert detail.get("resultados") == []
    assert detail.get("arquivos_relevantes") == []
    assert "conteudo" not in json.dumps(detail, ensure_ascii=False)

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", ".env"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    (tmp_path / ".env").write_text("TOKEN=anothersecret\n", encoding="utf-8")
    diff = executar_tool("git_diff", {"path": ".env"}, ctx)
    assert diff["ok"] is False and diff["error_code"] == "SECRET_PATH_BLOCKED"
    all_diff = executar_tool("git_diff", {}, ctx)
    assert all_diff["ok"] is False and all_diff["error_code"] in {"SECRET_PATH_BLOCKED", "SECRET_CONTENT_BLOCKED"}
    assert "anothersecret" not in json.dumps(all_diff, ensure_ascii=False)


def test_resume_rehydrates_persisted_evidence_and_releases_stale_coverage(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    cfg = base_config(claims_mode="off")
    reading = ler_faixa_projeto(str(tmp_path), "app.py", 1, 1, max_linhas=400)
    session = AgentSession("analise app.py")
    session.workspace_scope = workspace_scope("read")
    session.evidence["ev-0001"] = dict(reading)
    session.investigation = [investigation_target(
        status="established", evidence_ids=["ev-0001"], reason="app.py was read",
        goal="Establish app.py value",
    )]
    session.visible_source_ranges["app.py"] = [{"linha_inicio": 1, "linha_fim": 1}]

    persisted = AgentSession.from_dict(session.to_dict())
    assert "conteudo" not in persisted.evidence["ev-0001"]
    core_agent._rehydrate_persisted_evidence(persisted, {"caminho_origem": str(tmp_path)}, cfg)
    assert persisted.evidence["ev-0001"]["conteudo"] == "VALUE = 1\n"
    assert persisted.evidence["ev-0001"]["trecho_numerado"].strip().endswith("VALUE = 1")
    assert any(source.get("evidence_id") == "ev-0001" for source in persisted.relevant_sources)

    stale = AgentSession.from_dict(session.to_dict())
    (tmp_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    core_agent._rehydrate_persisted_evidence(stale, {"caminho_origem": str(tmp_path)}, cfg)
    assert stale.evidence["ev-0001"]["stale"] is True
    assert stale.evidence["ev-0001"]["rehydration_error"] == "EVIDENCE_STALE"
    assert "app.py" not in stale.visible_source_ranges


def test_ungrounded_workspace_final_is_semantically_fail_closed(monkeypatch, tmp_path):
    (tmp_path / "session.py").write_text("class AgentSession:\n    pass\n", encoding="utf-8")
    cfg = base_config(claims_mode="self_check")
    agent_calls = {"n": 0}
    verifier_calls = {"n": 0}

    def fake_agent(prompt, _cfg):
        agent_calls["n"] += 1
        payload = json.loads(prompt)
        if agent_calls["n"] == 1:
            # Reproduce the old P0 exactly: the Main LLM tries to answer from memory.
            return {
                "final": {"answer": "AgentSession controls task state.", "evidence_ids": [], "limitations": []},
                "workspace_scope": workspace_scope("none", "I can answer without the live workspace."),
                "investigation_updates": [],
            }
        if agent_calls["n"] == 2:
            assert payload["runtime_phase"] == "analysis_investigate"
            assert "WORKSPACE_SCOPE_INSUFFICIENT" in (payload.get("runtime_feedback") or "")
            return {
                "tool_calls": [{"tool": "read_file", "arguments": {"path": "session.py"}}],
                "workspace_scope": workspace_scope("read", "The reviewer established that current workspace facts are material."),
                "investigation_updates": [investigation_target(goal="Establish AgentSession's current role")],
            }
        return {
            "final": {"answer": "AgentSession is defined in session.py.", "evidence_ids": ["ev-0001"], "limitations": []},
            "workspace_scope": workspace_scope("read", "The answer depends on current workspace facts."),
            "investigation_updates": [investigation_target(
                goal="Establish AgentSession's current role", status="established",
                evidence_ids=["ev-0001"], reason="session.py was read",
            )],
        }

    def fake_verifier(prompt, _cfg):
        verifier_calls["n"] += 1
        payload = json.loads(prompt)
        if verifier_calls["n"] == 1:
            assert payload["task"] == "verify_workspace_scope"
            assert payload["workspace_scope"]["mode"] == "none"
            return {
                "claims": [], "findings": [],
                "semantic_gaps": [{
                    "id": "scope-1", "type": "scope_gap", "target_id": None,
                    "evidence_ids": [], "reason": "The request asks about the current AgentSession implementation.",
                }],
            }
        assert payload["task"] == "verify_claims"
        return {
            "claims": [{
                "id": "c1", "answer_ref": "a1", "target_id": "T1",
                "statement": "AgentSession is defined in session.py.", "kind": "fact",
                "evidence_ids": ["ev-0001"], "verdict": "supported", "reason": "",
            }],
            "findings": [], "semantic_gaps": [],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_verifier)
    status, text, _, details = core_agent.executar_agente(
        "O que AgentSession faz?", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "session.py" in text
    assert details["tool_calls"] == 1
    assert verifier_calls["n"] == 2
    assert any(
        item.get("decision") == "workspace_scope_review" and item.get("outcome") == "insufficient"
        for item in details["decision_history"]
    )
