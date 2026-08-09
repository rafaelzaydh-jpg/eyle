import json
from pathlib import Path

import eyle.core.agent as core_agent
import llm.executar as llm_mod
from eyle.core.token_budget import estimate_tokens
from llm.response_adapter import normalize_openai_chat_response
from tests.canonical import investigation_target, workspace_scope


def _config():
    return {
        "llm": {
            "context_window_tokens": 10000,
            "agent_decision_max_tokens": 1100,
            "agent_patch_max_tokens": 3600,
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
            "structured_protocol_retries": 1,
            "final_validation_retries": 1,
            "max_patch_dry_run_failures": 2,
            "max_write_investigation_turns": 2,
            "max_no_progress_turns": 2,
            "max_phase_violations": 1,
            "chat_history_token_budget": 700,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "claims": {"mode": "off"},
            "context_view": {"max_relevant_sources": 4, "max_relevant_source_chars": 3500, "max_symbol_preview_chars": 2600, "max_search_source_chars": 600},
        },
        "codar": {"ativado": True, "testes": {"ativado": False}},
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


def test_fixed_agent_prompt_is_compact():
    assert estimate_tokens(llm_mod.PROMPT_AGENTE, 3) <= 750
    assert len(llm_mod.PROMPT_AGENTE) < 2300
    assert "Claims and target coverage are reviewed separately" in llm_mod.PROMPT_AGENTE
    assert "1-based sentence" not in llm_mod.PROMPT_AGENTE


def test_common_multifile_write_reaches_transaction_in_three_calls(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8")
    (tmp_path / "routes.py").write_text("def amor():\n    return '<h1>Amor</h1>'\n", encoding="utf-8")
    (tmp_path / "test_routes.py").write_text("def test_amor():\n    assert True\n", encoding="utf-8")
    prompts = []
    merged = "from flask import Flask\n\napp = Flask(__name__)\n\n@app.get('/amor')\ndef amor():\n    return '<h1>Amor</h1>'\n"

    def fake(prompt, cfg):
        payload = json.loads(prompt); prompts.append(payload)
        names = {item["name"] for item in payload["available_tools"]}
        assert not {"apply_patch", "test_patch_dry_run", "apply_patch_set", "test_patch_set_dry_run"} & names
        if len(prompts) == 1:
            return {"tool_calls": [{"tool": "list_tree", "arguments": {}}], "workspace_scope": workspace_scope("write"), "investigation": [investigation_target(goal="Establish the files needed for the requested refactor")]}
        if len(prompts) == 2:
            return {"tool_calls": [
                {"tool": "read_file", "arguments": {"caminho_relativo": "app.py"}},
                {"tool": "read_file", "arguments": {"caminho_relativo": "routes.py"}},
                {"tool": "read_file", "arguments": {"caminho_relativo": "test_routes.py"}},
            ], "workspace_scope": workspace_scope("write"), "investigation": [investigation_target(goal="Establish the files needed for the requested refactor")]}
        return {"patches": [
            {"operation": "replace", "path": "app.py", "content": merged},
            {"operation": "delete", "path": "routes.py"},
            {"operation": "delete", "path": "test_routes.py"},
        ], "workspace_scope": workspace_scope("write"), "investigation": [investigation_target(goal="Establish the files needed for the requested refactor", status="established", evidence_ids=["ev-0001", "ev-0002", "ev-0003"], reason="All source files required for the transaction were read.")]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, details = core_agent.executar_agente(
        "Apague o teste e junte routes.py em app.py", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert pending["continuation_kind"] == "write_confirmation"
    assert len(pending["write_transaction"]["patches"]) == 3
    assert len(prompts) == 3
    assert sum(item["estimated_tokens"] for item in details["prompt_snapshots"]) < 12000


def test_semantic_read_coverage_blocks_overlapping_range(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return {"tool_calls": [{"tool": "read_file", "arguments": {"caminho_relativo": "app.py"}}], "workspace_scope": workspace_scope("read"), "investigation": [investigation_target(goal="Establish what app.py defines")]}
        if len(prompts) == 2:
            return {"tool_calls": [{"tool": "read_range", "arguments": {"caminho_relativo": "app.py", "linha_inicio": 1, "linha_fim": 1}}], "workspace_scope": workspace_scope("read"), "investigation": [investigation_target(goal="Establish what app.py defines")]}
        assert any(
            item.get("error_code") == "SEMANTIC_READ_BLOCKED"
            for item in payload["latest_tool_results"]
        )
        return {"final": {"answer": "app.py define x como 1.", "evidence_ids": ["ev-0001"]}, "workspace_scope": workspace_scope("read"), "investigation": [investigation_target(goal="Establish what app.py defines", status="established", evidence_ids=["ev-0001"], reason="app.py was read")]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, _, details = core_agent.executar_agente(
        "Analise app.py", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert details["tool_calls"] == 1
    assert len(prompts) == 3


def test_provider_cache_metadata_reduces_effective_task_budget():
    cfg = _config()
    runtime = cfg["_runtime_agent_budget"]

    first = llm_mod._reservar_requisicao_llm(cfg, "same system", "first", 100)
    llm_mod._finalizar_requisicao_llm(
        cfg, first, {"prompt_tokens": 1000, "cached_prompt_tokens": 0},
    )
    second = llm_mod._reservar_requisicao_llm(cfg, "same system", "second", 100)
    llm_mod._finalizar_requisicao_llm(
        cfg, second, {"prompt_tokens": 1000, "cached_prompt_tokens": 800},
    )

    assert runtime["prompt_tokens_actual"] == 2000
    assert runtime["prompt_tokens_cached"] == 800
    assert runtime["prompt_tokens_uncached"] == 1200
    assert runtime["prompt_tokens_effective"] == 1360


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


def test_premature_patch_is_redirected_to_reads_without_poisoning_retry(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return {"patches": [
                {"operation": "replace", "path": "app.py", "content": "x = 2\n"},
            ], "workspace_scope": workspace_scope("write"), "investigation": []}
        if len(prompts) == 2:
            assert payload["runtime_phase"] == "write_prepare"
            assert "INVESTIGATION_REQUIRED" in (payload.get("runtime_feedback") or "")
            assert any(
                item.get("content") == "Não use arquivos de rotas separados."
                for item in payload["conversation_background"]
            )
            return {"tool_calls": [{"tool": "read_file", "arguments": {"caminho_relativo": "app.py"}}], "workspace_scope": workspace_scope("write"), "investigation": [{"id": "T1", "goal": "Establish current app.py before editing", "status": "open", "evidence_ids": [], "reason": ""}]}
        assert payload["runtime_phase"] == "write_patch_only"
        return {"patches": [
            {"operation": "replace", "path": "app.py", "content": "x = 2\n"},
        ], "workspace_scope": workspace_scope("write"), "investigation": [{"id": "T1", "goal": "Establish current app.py before editing", "status": "established", "evidence_ids": ["ev-0001"], "reason": "app.py was read"}]}

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, details = core_agent.executar_agente(
        "Mude x para 2", _config(),
        projeto={"caminho_origem": str(tmp_path)},
        conversation_context={"recent_messages": [
            {"role": "user", "content": "Não use arquivos de rotas separados."},
        ]},
        retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert pending is not None
    assert len(prompts) == 3
    assert details["runtime_phase"] == "write_patch_only"
