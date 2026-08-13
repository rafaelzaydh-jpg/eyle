import json
from pathlib import Path

import eyle.core.agent as core_agent
import llm.executar as llm_mod
from eyle.core.token_budget import estimate_tokens
from llm.response_adapter import normalize_openai_chat_response
from tests.canonical import investigation_target, agent_tools, agent_patches, agent_final, agent_needs_user, tool_call


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
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_file_read_lines": 400,
        },
        "codar": {"ativado": True, "testes": {"ativado": False}},

    }


def test_fixed_agent_prompt_is_compact_and_non_prescriptive():
    assert estimate_tokens(llm_mod.PROMPT_AGENTE,3)<=650
    assert len(llm_mod.PROMPT_AGENTE)<2000
    lower = llm_mod.PROMPT_AGENTE.lower()
    assert "you own semantic decisions" in lower
    assert "freely choose" in lower
    assert "use structure only when it helps" in lower
    assert "ambient workspace state and capability availability are context, not tasks" in lower
    assert "mat-*" in llm_mod.PROMPT_AGENTE
    assert "fr-*" in llm_mod.PROMPT_AGENTE
    assert "mf-*" in llm_mod.PROMPT_AGENTE
    assert "prefer" not in lower
    assert "not a prerequisite" not in lower

def test_common_multifile_write_reaches_transaction_in_three_calls(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8")
    (tmp_path / "routes.py").write_text("def amor():\n    return '<h1>Amor</h1>'\n", encoding="utf-8")
    (tmp_path / "test_routes.py").write_text("def test_amor():\n    assert True\n", encoding="utf-8")
    prompts = []
    merged = "from flask import Flask\n\napp = Flask(__name__)\n\n@app.get('/amor')\ndef amor():\n    return '<h1>Amor</h1>'\n"

    def fake(prompt, cfg):
        payload = json.loads(prompt); prompts.append(payload)
        index_text = "\n".join(payload["available_capabilities"])
        assert not any(name in index_text for name in ("apply_patch", "test_patch_dry_run", "apply_patch_set", "test_patch_set_dry_run"))
        if len(prompts) == 1:
            return agent_tools(tool_call("list_tree", {}), investigation=[investigation_target(goal="Establish the files needed for the requested refactor")])
        if len(prompts) == 2:
            return agent_tools(
                tool_call("read_file", {"path": "app.py"}),
                tool_call("read_file", {"path": "routes.py"}),
                tool_call("read_file", {"path": "test_routes.py"}),
                investigation=[investigation_target(goal="Establish the files needed for the requested refactor")],
            )
        return agent_patches([
            {"operation": "replace", "path": "app.py", "content": merged},
            {"operation": "delete", "path": "routes.py"},
            {"operation": "delete", "path": "test_routes.py"},
        ], investigation=[investigation_target(goal="Establish the files needed for the requested refactor", status="established", grounding_ids=["mat-0002", "mat-0003", "mat-0004"], reason="All source files required for the transaction were read.")])

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
            return agent_tools(tool_call("read_file", {"path": "app.py"}), investigation=[investigation_target(goal="Establish what app.py defines")])
        if len(prompts) == 2:
            return agent_tools(tool_call("read_file", {"path": "app.py", "line_start": 1, "line_end": 1}), investigation=[investigation_target(goal="Establish what app.py defines")])
        assert any(
            item.get("coverage_replayed") is True
            and item.get("source_observation_tool") == "read_file"
            for item in payload["latest_capability_results"]
        )
        return agent_final({"answer": "app.py define x como 1.", "grounding_ids": ["mat-0001"]}, investigation=[investigation_target(goal="Establish what app.py defines", status="established", grounding_ids=["mat-0001"], reason="app.py was read")])

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
    cfg.setdefault("agent", {}).update({"max_total_tokens": 18000})
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


def test_replace_without_current_read_is_physically_rejected_but_not_semantically_fatal(monkeypatch, tmp_path):
    path=tmp_path/"app.py"; path.write_text("x = 1\n",encoding="utf-8")
    prompts=[]
    def fake(prompt,cfg):
        prompts.append(json.loads(prompt))
        if len(prompts) <= 3:
            return agent_patches([{"operation":"replace","path":"app.py","content":"x = 2\n"}])
        return agent_needs_user("Preciso de uma decisão do usuário para continuar.")
    monkeypatch.setattr(core_agent,"executar_agente_llm",fake)
    status,_,pending,details=core_agent.executar_agente("Mude x para 2",_config(),projeto={"caminho_origem":str(tmp_path)},retornar_detalhes=True)
    assert status=="needs_user" and pending is not None
    assert path.read_text(encoding="utf-8")=="x = 1\n"
    assert len(prompts)==4
    assert sum(1 for x in details["decision_history"] if x.get("decision")=="patch_validation" and x.get("outcome")=="rejected") >= 3

def test_source_preview_compaction_preserves_head_and_tail():
    text = "HEAD_DIAGNOSTIC\n" + ("x" * 6000) + "\nTAIL_DIAGNOSTIC"
    bounded = core_agent._bounded_source_text(text, 1200, source_span=(1, 500))
    assert "HEAD_DIAGNOSTIC" in bounded
    assert "TAIL_DIAGNOSTIC" in bounded
    assert "cropped" in bounded
    assert len(bounded) <= 1250


def test_pending_result_projection_tightens_on_long_jobs():
    from eyle.core.session import AgentSession
    session = AgentSession("audit")
    session.turn = 9
    huge = []
    for index in range(4):
        huge.append({
            "tool": "read_file", "status": "success", "ok": True, "executed": True, "changed": False,
            "grounding_ids": [f"mat-{index+1:04d}"],
            "detail": {"file": f"f{index}.py", "numbered_content": "line\n" * 3000},
        })
    session.observation_ledger["pending_results"] = huge
    projected = core_agent._project_pending_results(session, _config())
    encoded = json.dumps(projected, ensure_ascii=False)
    assert len(projected) == 4
    assert len(encoded) < 9000
    assert all(item.get("grounding_ids") for item in projected)



def test_runtime_has_no_resource_pressure_strategy_feedback():
    assert not hasattr(core_agent,"_resource_pressure_feedback")
    assert "RESOURCE_PRESSURE" not in Path(core_agent.__file__).read_text(encoding="utf-8")
