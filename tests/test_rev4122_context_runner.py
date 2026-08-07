#!/usr/bin/env python3
"""Rev4.12.2 regressions: structured context, test runner and preflight history."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import eyle.core.agent as core_agent
import eyle.core.editing as editing
import eyle.core.tools as tools
from eyle.core.session import AgentSession
from eyle.core.token_budget import available_user_prompt_tokens, estimate_tokens
from eyle.runtime.history import build_public_job_history
from llm.executar import PROMPT_AGENTE


BASE = Path(__file__).resolve().parents[1]


def _config():
    return {
        "llm": {
            "context_window_tokens": 10000,
            "agent_decision_max_tokens": 1100,
            "agent_patch_max_tokens": 3600,
            "agent_max_tokens": 1100,
        },
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "cached_prompt_weight": 0.2,
        },
        "agent": {
            "max_llm_turns": 6,
            "max_tool_calls": 12,
            "max_identical_tool_repeats": 2,
            "protocol_parse_retries": 1,
            "final_validation_retries": 1,
            "max_patch_dry_run_failures": 2,
            "max_write_investigation_turns": 2,
            "max_no_progress_turns": 2,
            "max_phase_violations": 1,
            "chat_history_token_budget": 700,
            "task_context_token_budget": 500,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "max_git_diff_chars": 6000,
            "response_quality": {"enabled": True},
        },
        "codar": {
            "ativado": True,
            "testes": {
                "ativado": True,
                "comando_python": "python -m pytest -q",
                "timeout_segundos": 60,
                "sandbox": {
                    "backend": "auto",
                    "bloquear_rede": True,
                    "comandos_permitidos": [["pytest"], ["python", "-m", "pytest"], ["python3", "-m", "pytest"]],
                    "allow_trusted_local": True,
                },
            },
        },
        "_runtime_agent_budget": {
            "max_llm_calls": 8,
            "max_prompt_tokens": 12000,
            "max_completion_tokens": 6000,
            "max_total_tokens": 18000,
            "llm_calls": 0,
            "llm_requests": 0,
            "prompt_tokens_reserved": 0,
            "prompt_tokens_actual": 0,
            "prompt_tokens_effective": 0,
            "generated_tokens": 0,
        },
    }


def _tool(name, arguments=None):
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    return tools.executar_tool(
        name,
        arguments or {},
        {"config": cfg, "projeto": {"caminho_origem": str(BASE)}, "evidence": {}},
    )


def test_structured_tool_results_are_compacted_to_context_budget_without_mutating_session():
    list_tree = core_agent._model_tool_result(AgentSession(request="x"), "list_tree", _tool(
        "list_tree", {"filtro": "*", "limite": 100, "profundidade": 3},
    ))
    inspect = core_agent._model_tool_result(AgentSession(request="x"), "inspect_project", _tool("inspect_project"))
    readme = core_agent._model_tool_result(AgentSession(request="x"), "read_file", _tool("read_file", {"caminho_relativo": "README.md"}))

    session = AgentSession(request="Faça uma analise do projeto")
    session.turn = 3
    session.latest_tool_results = [list_tree, readme, inspect]
    original_tree_count = len(list_tree["detail"]["entradas"])
    original_edges = len(inspect["detail"]["relation_signals"]["local_import_edges"])

    cfg = _config()
    call_cfg = core_agent._agent_config(cfg, session, {"caminho_origem": str(BASE)})
    prompt, _ = core_agent._compile_prompt(session, call_cfg, {"caminho_origem": str(BASE)}, None, "")
    budget = available_user_prompt_tokens(
        call_cfg, PROMPT_AGENTE,
        output_tokens=int(call_cfg["llm"].get("agent_max_tokens", 1100)),
    )
    assert estimate_tokens(prompt, 3) <= budget
    # Cropping is a prompt view only; full live results remain recoverable.
    assert len(session.latest_tool_results[0]["detail"]["entradas"]) == original_tree_count
    assert len(session.latest_tool_results[2]["detail"]["relation_signals"]["local_import_edges"]) == original_edges


def test_missing_pytest_is_runner_unavailable_not_test_failure(monkeypatch, tmp_path):
    (tmp_path / "test_sample.py").write_text("def test_x(): assert True\n", encoding="utf-8")
    monkeypatch.setattr(editing, "_detectar_comando_teste", lambda root, cfg: "python -m pytest -q")
    monkeypatch.setattr(editing, "executar_no_sandbox", lambda root, argv, cfg: {
        "executado": True,
        "ok": False,
        "codigo": 1,
        "saida": "C:\\Python312\\python.exe: No module named pytest",
        "backend": "trusted_local",
    })
    raw = editing.rodar_testes_projeto(str(tmp_path), _config()["codar"]["testes"])
    assert raw["ok"] is False
    assert raw["executado"] is False
    assert raw["error_code"] == "TEST_RUNNER_UNAVAILABLE"
    assert raw["runner"] == "pytest"

    result = tools.executar_tool(
        "run_tests", {},
        {"projeto": {"caminho_origem": str(tmp_path)}, "config": _config()},
    )
    assert result["ok"] is False
    assert result["executed"] is False
    assert result["error_code"] == "TEST_RUNNER_UNAVAILABLE"
    assert result["detail"]["runner"] == "pytest"


def test_runner_unavailable_is_runtime_evidence_and_next_turn_is_answer_only(monkeypatch, tmp_path):
    prompts = []

    def fake_tool(name, arguments, ctx):
        assert name == "run_tests"
        return {
            "status": "failed", "ok": False, "executed": False, "changed": False,
            "error_code": "TEST_RUNNER_UNAVAILABLE",
            "detail": {
                "command": "python -m pytest -q", "returncode": 1, "runner": "pytest",
                "tests_detected": True, "summary": "pytest não está disponível", "output_tail": "No module named pytest",
            },
        }

    def fake_llm(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            assert "run_tests" in {item["name"] for item in payload["available_tools"]}
            return '{"tool":"run_tests","arguments":{}}'
        assert payload["runtime_phase"] == "analysis_answer_only"
        assert payload["available_tools"] == []
        result = payload["latest_tool_results"][0]
        assert result["error_code"] == "TEST_RUNNER_UNAVAILABLE"
        assert result["evidence_ids"] == ["ev-0001"]
        return json.dumps({"final": {
            "answer": "Os testes não chegaram a executar porque o pytest não está disponível neste ambiente.",
            "claims": [{"kind": "fact", "sentence": 1, "evidence_ids": ["ev-0001"]}],
        }})

    monkeypatch.setattr(core_agent, "executar_tool", fake_tool)
    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_llm)
    status, text, _, details = core_agent.executar_agente(
        "Faça testes do projeto", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "não chegaram" in text
    assert len(prompts) == 2
    assert details["turns"] == 2
    assert details["evidence"][0]["source_type"] == "run_tests"


def test_history_distinguishes_sent_request_from_preflight_block():
    registro = {
        "id": 2,
        "status": "failed",
        "resultado": {"details": {
            "turns": 3,
            "runtime_phase": "analysis_complete_or_read",
            "failure_code": "PROMPT_CONTEXT_BUDGET_EXCEEDED",
            "llm_usage": {"llm_calls": 3, "llm_requests": 2},
            "prompt_snapshots": [
                {"turn": 1, "phase": "analysis_investigate", "characters": 3000, "estimated_tokens": 1000, "tool_count": 15},
                {"turn": 2, "phase": "analysis_complete_or_read", "characters": 4400, "estimated_tokens": 1470, "tool_count": 15},
                {"turn": 3, "phase": "analysis_complete_or_read", "characters": 22000, "estimated_tokens": 7300, "tool_count": 15},
            ],
            "llm_responses": [
                {"prompt_tokens": 1010, "completion_tokens": 101},
                {"prompt_tokens": 1562, "completion_tokens": 106},
            ],
        }},
    }
    history = build_public_job_history(registro)
    assert history["llm"] == {"logical_attempts": 3, "requests_sent": 2, "preflight_blocked": 1}
    assert [item["request_status"] for item in history["llm_calls"]] == ["sent", "sent", "preflight_blocked"]
    assert "prompt_tokens" not in history["llm_calls"][2]


def test_pytest_is_a_runtime_dependency_now():
    runtime = (BASE / "requirements.txt").read_text(encoding="utf-8").lower()
    lock = (BASE / "requirements.lock").read_text(encoding="utf-8").lower()
    assert "pytest==" in runtime
    for package in ("pytest==", "iniconfig==", "packaging==", "pluggy=="):
        assert package in lock
