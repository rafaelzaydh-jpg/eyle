import json
from pathlib import Path

import eyle.core.agent as core_agent
import llm.executar as llm_mod
from eyle.core.token_budget import estimate_tokens
from llm.response_adapter import normalize_openai_chat_response
from tests.canonical import investigation_target


def _config():
    return {
        "llm": {
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
            "claims": {"mode": "off"},
            "context_view": {"max_source_preview_chars": 3500, "max_symbol_preview_chars": 2600, "max_search_source_chars": 600},
        },
        "codar": {"ativado": True, "testes": {"ativado": False}},

    }


def test_fixed_agent_prompt_is_compact():
    assert estimate_tokens(llm_mod.PROMPT_AGENTE, 3) <= 1100
    assert len(llm_mod.PROMPT_AGENTE) < 3300
    assert "Investigation is YOUR semantic working memory" in llm_mod.PROMPT_AGENTE
    assert "never a Runtime requirement" in llm_mod.PROMPT_AGENTE
    assert "1-based sentence" not in llm_mod.PROMPT_AGENTE
    assert "either polarity is a valid result" in llm_mod.PROMPT_AGENTE
    assert "same target/candidate before exploring another" in llm_mod.PROMPT_AGENTE
    assert "otherwise leave include_text_references false" in llm_mod.PROMPT_AGENTE


def test_common_multifile_write_reaches_transaction_in_three_calls(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8")
    (tmp_path / "routes.py").write_text("def amor():\n    return '<h1>Amor</h1>'\n", encoding="utf-8")
    (tmp_path / "test_routes.py").write_text("def test_amor():\n    assert True\n", encoding="utf-8")
    prompts = []
    merged = "from flask import Flask\n\napp = Flask(__name__)\n\n@app.get('/amor')\ndef amor():\n    return '<h1>Amor</h1>'\n"

    def fake(prompt, cfg):
        payload = json.loads(prompt); prompts.append(payload)
        index_text = "\n".join(payload["capability_index"])
        assert not any(name in index_text for name in ("apply_patch", "test_patch_dry_run", "apply_patch_set", "test_patch_set_dry_run"))
        if len(prompts) == 1:
            return {"tool_calls": [{"tool": "list_tree", "arguments": {}}], "investigation_updates": [investigation_target(goal="Establish the files needed for the requested refactor")]}
        if len(prompts) == 2:
            return {"tool_calls": [
                {"tool": "read_file", "arguments": {"path": "app.py"}},
                {"tool": "read_file", "arguments": {"path": "routes.py"}},
                {"tool": "read_file", "arguments": {"path": "test_routes.py"}},
            ], "investigation_updates": [investigation_target(goal="Establish the files needed for the requested refactor")]}
        return {"patches": [
            {"operation": "replace", "path": "app.py", "content": merged},
            {"operation": "delete", "path": "routes.py"},
            {"operation": "delete", "path": "test_routes.py"},
        ], "investigation_updates": [investigation_target(goal="Establish the files needed for the requested refactor", status="established", evidence_ids=["ev-0001", "ev-0002", "ev-0003"], reason="All source files required for the transaction were read.")]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, details = core_agent.executar_agente(
        "Apague o teste e junte routes.py em app.py", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert pending["continuation_kind"] == "write_confirmation"
    assert len(pending["session"]["write_transaction"]["patches"]) == 3
    assert len(prompts) == 3
    assert sum((item.get("prompt") or {}).get("estimated_tokens", 0) for item in details["llm_calls"]) < 12000


def test_semantic_read_coverage_blocks_overlapping_range(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return {"tool_calls": [{"tool": "read_file", "arguments": {"path": "app.py"}}], "investigation_updates": [investigation_target(goal="Establish what app.py defines")]}
        if len(prompts) == 2:
            return {"tool_calls": [{"tool": "read_file", "arguments": {"path": "app.py", "line_start": 1, "line_end": 1}}], "investigation_updates": [investigation_target(goal="Establish what app.py defines")]}
        assert any(
            item.get("coverage_replayed") is True
            and item.get("source_observation_tool") == "read_file"
            for item in payload["latest_tool_results"]
        )
        return {"final": {"answer": "app.py define x como 1.", "limitations": [], "evidence_ids": ["ev-0001"]}, "investigation_updates": [investigation_target(goal="Establish what app.py defines", status="established", evidence_ids=["ev-0001"], reason="app.py was read")]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, _, details = core_agent.executar_agente(
        "Analise app.py", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert details["tool_calls"] == 1
    assert len(prompts) == 3


def test_provider_cache_metadata_reduces_effective_task_budget():
    from eyle.core.execution_context import ExecutionContext
    cfg = _config()
    cfg.setdefault("agent", {}).update({"max_llm_calls": 8, "max_prompt_tokens": 12000, "max_completion_tokens": 6000, "max_total_tokens": 18000})
    execution = ExecutionContext.from_config(cfg)
    execution.begin_call(mode="agent", turn=1, prompt={})
    first = llm_mod._reservar_requisicao_llm(cfg, execution, "same system", "first", 100)
    llm_mod._finalizar_requisicao_llm(cfg, execution, first, {"prompt_tokens": 1000, "cached_prompt_tokens": 0})
    execution.begin_call(mode="agent", turn=2, prompt={})
    second = llm_mod._reservar_requisicao_llm(cfg, execution, "same system", "second", 100)
    llm_mod._finalizar_requisicao_llm(cfg, execution, second, {"prompt_tokens": 1000, "cached_prompt_tokens": 800})
    usage = execution.usage_view()
    assert usage["prompt_tokens_actual"] == 2000
    assert usage["prompt_tokens_cached"] == 800
    assert usage["prompt_tokens_uncached"] == 1200
    assert usage["prompt_tokens_effective"] == 1360


def test_response_adapter_reads_openai_cached_prompt_tokens():
    normalized = normalize_openai_chat_response({
        "choices": [{"message": {"content": "ok"}}],
        "usage": {
            "prompt_tokens": 900,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 700},
        },
    })
    assert normalized.prompt_tokens == 900
    assert normalized.cached_prompt_tokens == 700
    assert normalized.completion_tokens == 20


def test_replace_without_current_read_is_rejected_by_write_contract(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        prompts.append(json.loads(prompt))
        return {
            "patches": [{"operation": "replace", "path": "app.py", "content": "x = 2\n"}],
            "investigation_updates": [],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, pending, details = core_agent.executar_agente(
        "Mude x para 2", _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "failed"
    assert pending is None
    assert "read the existing file before replace" in text
    assert any(
        item.get("decision") == "patch_validation" and item.get("outcome") == "rejected"
        for item in details["decision_history"]
    )
    assert all("runtime_phase" not in payload for payload in prompts)
