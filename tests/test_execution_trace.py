from __future__ import annotations

import json

import eyle.core.agent as core_agent
import eyle.providers.standard as tools
from eyle.runtime.execution_context import ExecutionContext, bind_execution, reset_execution
from eyle.runtime.history import build_execution_trace
from eyle.core.session import AgentSession
from tests.canonical import standard_registry, base_config


def test_execution_trace_is_internal_diagnostics_not_main_capability():
    assert "execution_trace" not in tools.CAPABILITIES
    assert "agent_info" not in tools.CAPABILITIES
    assert callable(build_execution_trace)


def test_compile_prompt_records_context_composition_without_exposing_diagnostic_tool(tmp_path):
    cfg = base_config()
    session = AgentSession("Analise o projeto SECRET_MARKER")
    session.turn = 1
    execution = ExecutionContext.from_config(cfg)
    token = bind_execution(execution)
    try:
        prompt, allowed = core_agent._compile_prompt(
            session, cfg, {"standard": {"caminho_origem": str(tmp_path)}},
            {"recent_messages": [{"role": "user", "content": "PRIVATE_HISTORY_MARKER"}]}, standard_registry(),
        )
    finally:
        reset_execution(token)
    assert "execution_trace" not in allowed
    payload = json.loads(prompt)
    assert "execution_trace" not in json.dumps(payload["ecc_operations"], ensure_ascii=False)
    snap = execution.llm_calls[-1]["prompt"]
    serialized = json.dumps(snap, ensure_ascii=False)
    assert "SECRET_MARKER" not in serialized
    assert "PRIVATE_HISTORY_MARKER" not in serialized
    assert "SECRET_MARKER" in prompt


def test_internal_execution_trace_projects_runtime_facts_without_raw_prompts():
    details = {
        "status": "processing", "turns": 2, "physical_capability_calls": 1,
        "llm_usage": {"llm_calls": 2, "llm_requests": 2, "prompt_tokens_actual": 2100, "prompt_tokens_uncached": 2100},
        "llm_calls": [{"logical_call_id": 1, "turn": 1, "mode": "ecc", "prompt": {
            "characters": 3000, "estimated_tokens": 1000, "components_after": {"ecc_operations": {"characters": 900, "estimated_tokens": 300, "items": 2}},
        }, "attempts": [{"physical_attempt": 1, "prompt_tokens": 2100}]}],
        "operation_history": [{"turn": 1, "capability": "project_stats", "status": "success", "arguments": {}, "result": {"ok": True, "files": 92}}],
    }
    trace = build_execution_trace(details, job_id=44, status="processing")
    assert trace["summary"]["job_id"] == 44
    assert trace["tokens"]["prompt_total"] == 2100
    assert trace["capabilities"][0]["capability"] == "project_stats"
    assert trace["privacy"]["raw_prompts_exposed"] is False
    assert "diagnosis" not in trace
    assert "repeated_rejected_decisions" not in trace["summary"]
    assert "completion_remaining" not in trace["tokens"]
