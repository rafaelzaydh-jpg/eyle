import json
from pathlib import Path

import eyle.core.agent as core_agent
from eyle.core.session import AgentSession
from eyle.runtime.history import build_public_job_history


def _config():
    return {
        "codar": {"testes": {"ativado": True}},
        "_runtime_agent_budget": {
            "llm_calls": 2,
            "llm_requests": 2,
            "prompt_tokens_actual": 1200,
            "prompt_tokens_cached": 800,
            "prompt_tokens_uncached": 400,
            "prompt_tokens_effective": 560,
            "completion_tokens_actual": 120,
            "generated_tokens": 120,
            "reasoning_tokens_actual": 10,
            "total_tokens_effective": 680,
            "llm_responses": [
                {"prompt_tokens": 600, "cached_prompt_tokens": 400, "completion_tokens": 60, "finish_reason": "stop"},
                {"prompt_tokens": 600, "cached_prompt_tokens": 400, "completion_tokens": 60, "finish_reason": "stop"},
            ],
        },
    }


def test_history_arguments_never_expose_patch_bodies_or_memory_values():
    patch = core_agent._observable_tool_arguments(
        "test_patch_set_dry_run",
        {"patches": [{"operation": "replace", "path": "app.py", "content": "TOP_SECRET_SOURCE"}]},
    )
    memory = core_agent._observable_tool_arguments(
        "memory_store", {"text": "PRIVATE_MEMORY_BODY", "kind": "fact", "evidence_ids": ["ev-1"]},
    )
    dumped = json.dumps({"patch": patch, "memory": memory})
    assert "TOP_SECRET_SOURCE" not in dumped
    assert "PRIVATE_MEMORY_BODY" not in dumped
    assert patch == {"patches": [{"operation": "replace", "path": "app.py"}]}


def test_details_keep_observable_phase_tools_and_token_usage():
    session = AgentSession("analise", task_id="job-9")
    session.turn = 1
    session.phase = "analysis_discover"
    session.record_prompt("agent", 900, 300, 4, phase=session.phase, turn=1)
    core_agent._record_tool_history(
        session,
        "calculate",
        {"expression": "20-(50+3)"},
        {"status": "success", "ok": True, "executed": True, "changed": False, "detail": {"result": "-33", "exact": True}},
    )
    details = core_agent._details(session, "success", _config())
    assert details["prompt_snapshots"][0]["phase"] == "analysis_discover"
    assert details["tool_history"][0]["arguments"] == {"expression": "20-(50+3)"}
    assert details["tool_history"][0]["result"]["result"] == "-33"
    assert details["llm_usage"]["prompt_tokens_cached"] == 800
    assert details["llm_usage"]["prompt_tokens_uncached"] == 400


def test_public_history_is_bounded_observability_not_chain_of_thought():
    details = core_agent._details(AgentSession("x"), "success", _config())
    details.update({
        "runtime_phase": "analysis_answer_only",
        "turns": 2,
        "tool_calls": 1,
        "tool_history": [{
            "tool": "read_file", "turn": 1, "phase": "analysis_discover", "status": "success",
            "arguments": {"path": "app.py"},
            "result": {"status": "success", "ok": True, "executed": True, "file": "app.py", "lines": [1, 20]},
        }],
        "raw_prompt": "DO_NOT_EXPOSE_PROMPT",
        "chain_of_thought": "DO_NOT_EXPOSE_REASONING",
        "source_content": "DO_NOT_EXPOSE_SOURCE",
    })
    history = build_public_job_history({
        "id": 12,
        "status": "completed",
        "criado_em": "2026-08-06T20:00:00Z",
        "iniciado_em": "2026-08-06T20:00:01Z",
        "concluido_em": "2026-08-06T20:00:02Z",
        "progresso": {"elapsed_seconds": 1.0},
        "resultado": {"status": "success", "details": details},
    })
    dumped = json.dumps(history)
    assert history["job_id"] == 12
    assert history["tokens"]["prompt_cached"] == 800
    assert history["llm_calls"][0]["uncached_prompt_tokens"] == 200
    assert history["tools"][0]["tool"] == "read_file"
    assert "DO_NOT_EXPOSE_PROMPT" not in dumped
    assert "DO_NOT_EXPOSE_REASONING" not in dumped
    assert "DO_NOT_EXPOSE_SOURCE" not in dumped
    assert history["privacy"]["chain_of_thought_exposed"] is False


def test_confirmed_write_records_compile_tests_reread_and_rollback_metadata(monkeypatch, tmp_path):
    session = AgentSession("altere app.py")
    pending = {
        "tool_pendente": {"tool": "apply_patch_set", "arguments": {"patches": [{"operation": "replace", "path": "app.py", "content": "x=2\\n"}]}},
    }
    project = {"caminho_origem": str(tmp_path)}
    config = _config()

    monkeypatch.setattr(core_agent, "validar_chamada_tool", lambda tool, args: (args, None))
    monkeypatch.setattr(core_agent, "executar_tool", lambda tool, args, context: {
        "status": "success", "ok": True, "executed": True, "changed": True,
        "detail": {"applied_patches": [{"operation": "replace", "path": "app.py", "content": "x=2\\n", "result_content": "x=2\\n"}]},
    } if tool == "apply_patch_set" else {"status": "success", "ok": True, "executed": True, "changed": False, "detail": "ok"})
    monkeypatch.setattr(core_agent, "_compile_after_write", lambda *args, **kwargs: {"ok": True, "executed": True, "detail": "compileall passou", "files": ["app.py"]})
    monkeypatch.setattr(core_agent, "_run_tests_after_write", lambda *args, **kwargs: {"ok": True, "executed": True, "detail": "2 passed"})
    monkeypatch.setattr(core_agent, "_reread_with_tools", lambda *args, **kwargs: {"ok": True, "executed": True, "detail": "reread tool ok"})
    monkeypatch.setattr(core_agent, "verify_expected_outputs", lambda *args, **kwargs: {"ok": True, "detail": "full reread ok", "checked": [{"path": "app.py"}], "failures": []})

    status, _, _, details = core_agent._resume_set(session, pending, config, project, True)
    assert status == "success"
    assert set(details["write_validation"]) == {"apply", "compileall", "tests", "tool_reread", "full_reread"}
    assert details["write_validation"]["compileall"]["ok"] is True
    assert details["write_validation"]["tests"]["ok"] is True


def test_web_ui_contains_on_demand_expandable_history_controls():
    js = Path("web/static/app.js").read_text(encoding="utf-8")
    css = Path("web/static/style.css").read_text(encoding="utf-8")
    routes = Path("web/routes.py").read_text(encoding="utf-8")
    assert "/jobs/${numeric}/history" in js
    assert "ocultar histórico" in js
    assert "chain-of-thought" in js
    assert ".execution-history" in css
    assert '/jobs/<int:job_id>/history' in routes
