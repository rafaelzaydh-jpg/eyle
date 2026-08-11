#!/usr/bin/env python3
"""execution_trace regressions: one safe observer over real runtime facts."""
from __future__ import annotations

import json
from pathlib import Path

import eyle.core.agent as core_agent
from eyle.core.execution_context import ExecutionContext, bind_execution, reset_execution
import eyle.core.tools as tools
from eyle.core.execution_trace import build_execution_trace
from eyle.core.session import AgentSession
from eyle.runtime import queue as runtime_queue


def _config():
    return {
        "app_version": "2.7.4",
        "revision": "rev5.7.5-canonical-boundary-hardening",
        "llm": {
            "model": "auto",
            "context_window_tokens": 10000,
            "agent_max_tokens": 3600,
        },
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "cached_prompt_weight": 0.2,
        },
        "agent": {
            "max_llm_turns": 6,
            "max_tool_calls": 12,
            "max_patch_dry_run_failures": 2,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_file_read_lines": 400,
            "max_git_diff_chars": 6000,
            "claims": {"mode": "off"},
            "context_view": {"max_source_preview_chars": 3500, "max_symbol_preview_chars": 2600, "max_search_source_chars": 600},
        },
        "codar": {"ativado": True, "testes": {"ativado": False}},

    }


def test_execution_trace_is_one_registered_read_only_observer():
    assert "execution_trace" in tools.TOOLS
    item = tools.TOOLS["execution_trace"]
    contract = " ".join([
        item["description"], item["returns"], " ".join(item["caveats"]),
    ]).lower()
    assert item["category"] == "READ_ONLY"
    assert item["effects"] == ["NONE"]
    assert "diagnos" in contract
    assert "chain-of-thought" in contract
    assert len(tools.TOOLS) == 18
    assert "read_range" not in tools.TOOLS


def test_compile_prompt_records_context_composition_without_raw_content(tmp_path):
    cfg = _config()
    session = AgentSession("Analise o projeto SECRET_MARKER")
    session.turn = 1
    execution = ExecutionContext.from_config(cfg)
    token = bind_execution(execution)
    try:
        prompt, allowed = core_agent._compile_prompt(
            session, cfg, {"caminho_origem": str(tmp_path)},
            {"recent_messages": [{"role": "user", "text": "PRIVATE_HISTORY_MARKER"}]}, "",
        )
    finally:
        reset_execution(token)
    assert "execution_trace" in allowed
    payload = json.loads(prompt)
    assert any(item.startswith("execution_trace(") for item in payload["capability_index"])
    assert payload["active_tools"] == []
    snap = execution.llm_calls[-1]["prompt"]
    assert snap["components_before"]["capability_index"]["items"] >= 1
    assert snap["components_before"]["active_tools"]["items"] == 0
    assert snap["components_after"]["request"]["estimated_tokens"] >= 1
    assert snap["system_prompt_estimated_tokens"] >= 1
    assert isinstance(snap["crop_applied"], bool)
    serialized_snapshot = json.dumps(snap, ensure_ascii=False)
    assert "SECRET_MARKER" not in serialized_snapshot
    assert "PRIVATE_HISTORY_MARKER" not in serialized_snapshot
    assert "SECRET_MARKER" in prompt


def test_current_execution_trace_returns_facts_up_to_before_tool_call():
    details = {
        "status": "processing",
        "turns": 2,
        "tool_calls": 1,
        "llm_usage": {"llm_calls": 2, "llm_requests": 2, "prompt_tokens_actual": 2100, "prompt_tokens_uncached": 2100},
        "llm_calls": [{"logical_call_id": 1, "turn": 1, "mode": "agent", "prompt": {
            "characters": 3000, "estimated_tokens": 1000, "tool_count": 16,
            "components_after": {"capability_index": {"characters": 900, "estimated_tokens": 300, "items": 16}, "active_tools": {"characters": 2, "estimated_tokens": 1, "items": 0}},
        }, "attempts": [{"physical_attempt": 1, "prompt_tokens": 2100}]}],
        "decision_history": [{"turn": 1, "decision": "tool", "outcome": "validated", "tools": ["project_stats"]}],
        "tool_history": [{"turn": 1, "tool": "project_stats", "status": "success", "arguments": {}, "result": {"ok": True, "files": 92}}],
    }
    current = build_execution_trace(details, job_id=44, status="processing")
    result = tools.executar_tool(
        "execution_trace", {"section": "all", "limit": 20},
        {"execution_trace": current, "config": _config(), "projeto": {}},
    )
    assert result["ok"] is True
    trace = result["detail"]
    assert trace["summary"]["job_id"] == 44
    assert trace["tokens"]["prompt_total"] == 2100
    assert trace["context"][0]["components_after"]["capability_index"]["items"] == 16
    assert trace["tools"][0]["tool"] == "project_stats"
    assert trace["privacy"]["raw_prompts_exposed"] is False
    assert "diagnosis" not in trace


def test_execution_trace_can_read_one_persisted_job(monkeypatch):
    registro = {
        "id": 15,
        "status": "completed",
        "criado_em": "2026-08-07T12:00:00-03:00",
        "iniciado_em": "2026-08-07T12:00:01-03:00",
        "concluido_em": "2026-08-07T12:00:10-03:00",
        "progresso": {"elapsed_seconds": 9.0},
        "resultado": {"details": {
            "status": "success", "turns": 3, "tool_calls": 2,
                "llm_usage": {"llm_calls": 3, "llm_requests": 3, "prompt_tokens_actual": 5866},
            "llm_calls": [{"logical_call_id": 1, "turn": 1, "mode": "agent", "prompt": {"characters": 2500, "estimated_tokens": 834, "tool_count": 16}, "attempts": [{"physical_attempt": 1, "prompt_tokens": 5866}]}],
            }},
    }
    monkeypatch.setattr(runtime_queue, "obter", lambda job_id: registro if int(job_id) == 15 else None)
    result = tools.executar_tool(
        "execution_trace", {"job_id": 15, "section": "llm"},
        {"execution_trace": None, "config": _config(), "projeto": {}},
    )
    assert result["ok"] is True
    assert result["detail"]["summary"]["job_id"] == 15
    assert result["detail"]["summary"]["duration_seconds"] == 9.0
    assert result["detail"]["tokens"]["prompt_total"] == 5866
    assert "tools" not in result["detail"]


def test_agent_can_choose_execution_trace_and_cite_it(monkeypatch, tmp_path):
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            assert any(item.startswith("execution_trace(") for item in payload["capability_index"])
            assert payload["active_tools"] == []
            return {
                "tool_calls": [{"tool": "execution_trace", "arguments": {"section": "context", "limit": 20}}],
                "investigation_updates": [{"id": "T1", "goal": "Establish what the current trace records", "status": "open", "evidence_ids": [], "reason": ""}],
            }
        result = payload["latest_tool_results"][0]
        assert result["tool"] == "execution_trace"
        assert result["detail"]["context"]
        evidence_id = result["evidence_ids"][0]
        return {"final": {
            "answer": "O trace atual registra a composição do contexto sem expor o prompt bruto.",
            "limitations": [],
            "evidence_ids": [evidence_id],
        },
        "investigation_updates": [{"id": "T1", "goal": "Establish what the current trace records", "status": "established", "evidence_ids": [evidence_id], "reason": "The trace Evidence was observed."}]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = core_agent.executar_agente(
        "Inspecione o trace desta execução e diga o que ele registra.",
        _config(), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "composição do contexto" in text
    assert len(prompts) == 2
    assert "execution_trace" in details["tools_used"]
    assert any(item.get("source_type") == "execution_trace" for item in details["evidence"])
    assert "phase_history" not in details
