import json
from pathlib import Path

import pytest

from eyle.core import agent as core_agent
from eyle.core import tools
from eyle.core.session import AgentSession
from eyle.core.token_budget import estimate_tokens
from llm import executar as llm_exec

BASE = Path(__file__).resolve().parents[1]


def release_config():
    return json.loads((BASE / "config.json").read_text(encoding="utf-8"))


def test_release_defaults_use_32k_context_and_96k_job_prompt_budget():
    cfg = release_config()
    assert cfg["llm"]["context_window_tokens"] == 32768
    assert cfg["agent"]["max_prompt_tokens"] == 96000
    assert cfg["agent"]["max_total_tokens"] >= 96000


def test_output_reserve_depends_on_phase_not_source_volume(tmp_path):
    cfg = release_config()
    project = {"caminho_origem": str(tmp_path)}

    small = AgentSession("Analise o projeto")
    small.turn = 2
    small.evidence["ev-0001"] = {"arquivo": "app.py", "file_hash": "a"}
    small.latest_tool_results = [{"tool": "read_file", "detail": {"conteudo": "x" * 20}}]

    huge = AgentSession("Analise o projeto")
    huge.turn = 2
    huge.evidence["ev-0001"] = {"arquivo": "app.py", "file_hash": "a"}
    huge.latest_tool_results = [{"tool": "read_file", "detail": {"conteudo": "x" * 50000}}]

    assert core_agent._phase_for_call(small, cfg, project) == "analysis_complete_or_read"
    assert core_agent._phase_for_call(huge, cfg, project) == "analysis_complete_or_read"
    small_cfg = core_agent._agent_config(cfg, small, project)
    huge_cfg = core_agent._agent_config(cfg, huge, project)
    assert small_cfg["llm"]["agent_max_tokens"] == cfg["llm"]["agent_analysis_max_tokens"]
    assert huge_cfg["llm"]["agent_max_tokens"] == small_cfg["llm"]["agent_max_tokens"]

    write = AgentSession("altere o arquivo app.py")
    write.turn = 2
    write.evidence["ev-0001"] = {"arquivo": "app.py", "file_hash": "a"}
    write_cfg = core_agent._agent_config(cfg, write, project)
    assert write_cfg["llm"]["agent_max_tokens"] == cfg["llm"]["agent_patch_max_tokens"]


def test_analysis_never_receives_patch_dry_run_tools(tmp_path):
    cfg = release_config()
    project = {"caminho_origem": str(tmp_path)}
    for turn, with_evidence in ((1, False), (2, True)):
        session = AgentSession("Faça uma análise profunda do projeto")
        session.turn = turn
        if with_evidence:
            session.evidence["ev-0001"] = {"arquivo": "app.py", "file_hash": "a"}
        phase = core_agent._phase_for_call(session, cfg, project)
        assert phase.startswith("analysis")
        _, catalog = core_agent._tool_catalog(cfg, project, phase, session.request)
        names = {item["name"] for item in catalog}
        assert "test_patch_dry_run" not in names
        assert "test_patch_set_dry_run" not in names


def test_agent_info_separates_registered_from_phase_available_tools(tmp_path):
    cfg = release_config()
    result = tools.executar_tool(
        "agent_info", {},
        {
            "config": cfg,
            "projeto": {"caminho_origem": str(tmp_path)},
            "available_tools": ["agent_info", "read_file", "execution_trace"],
        },
    )
    assert result["ok"] is True
    detail = result["detail"]
    registered = {item["name"] for item in detail["registered_tools"]}
    available = {item["name"] for item in detail["available_tools"]}
    assert len(registered) == 20
    assert {"memory_store", "apply_patch", "apply_patch_set"} <= registered
    assert available == {"agent_info", "read_file", "execution_trace"}
    assert detail["tools"] == detail["registered_tools"]


@pytest.mark.parametrize("user_request", [
    "Onde AgentSession é definido e onde ele é utilizado?",
    "Explique o caminho real de uma mensagem minha até a chamada da LLM. Use evidências do projeto.",
    "Identifique até 5 bugs reais neste projeto. Se encontrar menos, não invente.",
    "Agora identifique até 5 riscos, separando claramente risco de bug.",
])
def test_formerly_failing_investigations_fit_32k_after_large_evidence(user_request, tmp_path):
    cfg = release_config()
    project = {"caminho_origem": str(tmp_path)}
    session = AgentSession(user_request)
    session.turn = 3
    session.evidence["ev-0001"] = {
        "id": "ev-0001", "arquivo": "a.py", "file_hash": "a", "content_hash": "a",
        "conteudo": "a" * 9000,
    }
    session.evidence["ev-0002"] = {
        "id": "ev-0002", "arquivo": "b.py", "file_hash": "b", "content_hash": "b",
        "conteudo": "b" * 9000,
    }
    session.latest_tool_results = [
        {"tool": "search_code", "status": "success", "ok": True, "executed": True,
         "detail": {"resultados": [{"arquivo": "a.py", "trecho_numerado": "x" * 12000}]}, "evidence_ids": ["ev-0001"]},
        {"tool": "read_file", "status": "success", "ok": True, "executed": True,
         "detail": {"arquivo": "b.py", "conteudo": "y" * 12000}, "evidence_ids": ["ev-0002"]},
    ]
    call_cfg = core_agent._agent_config(cfg, session, project)
    prompt, _allowed = core_agent._compile_prompt(session, call_cfg, project, {}, "")
    system_tokens = estimate_tokens(llm_exec.PROMPT_AGENTE, cfg["context_engine"]["chars_per_token_fallback"])
    prompt_tokens = estimate_tokens(prompt, cfg["context_engine"]["chars_per_token_fallback"])
    total = system_tokens + prompt_tokens + call_cfg["llm"]["agent_max_tokens"] + cfg["context_engine"]["safety_margin_tokens"]
    assert total <= cfg["llm"]["context_window_tokens"]
    assert not ({"test_patch_dry_run", "test_patch_set_dry_run"} & _allowed)


def test_96k_cumulative_prompt_budget_allows_multiple_32k_safe_calls():
    cfg = release_config()
    cfg["_runtime_agent_budget"] = {
        "max_prompt_tokens": 96000,
        "max_total_tokens": 102000,
        "max_completion_tokens": 6000,
        "prompt_tokens_effective": 0,
        "generated_tokens": 0,
        "system_prompt_hashes": [],
    }
    # About 20k user tokens per request with the configured 3 chars/token fallback.
    user_prompt = "x" * 60000
    for _ in range(4):
        llm_exec._reservar_requisicao_llm(cfg, "system", user_prompt, 1000)
    assert cfg["_runtime_agent_budget"]["prompt_tokens_effective"] < 96000
    with pytest.raises(llm_exec.ErroLLM) as exc:
        llm_exec._reservar_requisicao_llm(cfg, "system", user_prompt, 1000)
    assert exc.value.error_code == "MAX_PROMPT_TOKENS_EXCEEDED"


def test_windows_style_test_failures_are_reported_not_masked(monkeypatch, tmp_path):
    def fake_runner(*_args, **_kwargs):
        return {
            "ok": False,
            "executado": True,
            "codigo": 1,
            "comando": r"C:\\Python312\\python.exe -m pytest -q",
            "scope": None,
            "backend": "trusted_local",
            "runner": "pytest",
            "tests_detected": True,
            "saida_resumida": "8 failed, 137 passed, 1 warning in 4.12s",
            "error_code": "TESTS_FAILED",
        }

    monkeypatch.setattr(tools, "rodar_testes_projeto", fake_runner)
    cfg = release_config()
    result = tools.executar_tool(
        "run_tests", {}, {"config": cfg, "projeto": {"caminho_origem": str(tmp_path)}},
    )
    assert result["status"] == "failed"
    assert result["ok"] is False
    assert result["executed"] is True
    assert result["error_code"] == "TESTS_FAILED"
    assert "8 failed" in result["detail"]["summary"]
    assert result["detail"]["returncode"] == 1


def test_execution_trace_is_used_inside_a_real_investigation(monkeypatch, tmp_path):
    # Use the actual tool registry and actual source search; only the model is scripted.
    source = tmp_path / "agent_sample.py"
    source.write_text("def _agent_config():\n    return 'phase-budget'\n", encoding="utf-8")
    responses = iter([
        json.dumps({"tool": "execution_trace", "arguments": {"section": "summary"}}),
        json.dumps({"tool": "search_code", "arguments": {"query": "def _agent_config"}}),
        json.dumps({"final": {
            "answer": "O trace foi consultado e a implementação de _agent_config foi localizada no projeto.",
            "claims": [{"kind": "fact", "sentence": 1, "evidence_ids": ["ev-0001", "ev-0002"]}],
            "limitations": [],
        }}),
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda *_args, **_kwargs: next(responses))
    cfg = release_config()
    status, text, _pending, details = core_agent.executar_agente(
        "Investigue esta execução e depois inspecione o código responsável.",
        cfg, projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "trace" in text.lower()
    assert details["tools_used"][:2] == ["execution_trace", "search_code"]
    assert {item["tool"] for item in details["tool_history"]} >= {"execution_trace", "search_code"}
