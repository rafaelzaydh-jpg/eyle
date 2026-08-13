from tests.canonical import run_agent
from tests.canonical import standard_registry
import json

import pytest

import eyle.core.agent as core_agent
import llm.executar as llm_mod
from eyle.runtime.execution_context import ExecutionContext
from eyle.core.token_budget import available_user_prompt_tokens, estimate_tokens
from eyle.runtime.config import ConfigError, validar_config
from tests.canonical import agent_complete, agent_tools, base_config, tool_call


def test_full_capability_contracts_are_visible_before_first_use(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        contracts = {item["name"]: item for item in payload["available_capabilities"]}
        assert "standard.read_file" in contracts and "standard.search_code" in contracts
        assert contracts["standard.read_file"]["purpose"]
        assert contracts["standard.read_file"]["effect"] == "observe"
        assert "path" in contracts["standard.read_file"]["inputs"]
        assert contracts["standard.read_file"]["returns"]
        if len(prompts) == 1:
            return agent_tools(tool_call("read_file", {"path": "app.py"}))
        assert "standard.read_file" in contracts  # no first-use hiding/activation
        return agent_complete({"answer": "app.py foi observado.", "grounding_ids": ["mat-0001"]})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = run_agent(core_agent, 
        "Leia app.py", base_config(),
        provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "observado" in text
    assert len(prompts) == 2
    assert details["capability_calls"] == 1





def test_rev148_keeps_38k_physical_context_without_task_token_fuse():
    cfg=base_config(); validar_config(cfg, standard_registry()); execution=ExecutionContext.from_config(cfg)
    assert not hasattr(execution,"max_prompt_tokens")
    assert not hasattr(execution,"max_completion_tokens")
    assert not hasattr(execution,"max_total_tokens")
    assert "max_total_tokens" not in cfg["agent"]
    assert cfg["llm"]["context_window_tokens"]==38000
    assert available_user_prompt_tokens(cfg, "", output_tokens=0) == 37500
    assert all(key not in cfg["agent"] for key in ("max_llm_turns","max_llm_calls","max_tool_calls"))
    too_wide=base_config(); too_wide["llm"]["context_window_tokens"]=38001
    with pytest.raises(ConfigError,match="context_window_tokens"):
        validar_config(too_wide, standard_registry())

def test_prior_task_token_spend_does_not_block_a_new_call():
    cfg = base_config()
    execution = ExecutionContext.from_config(cfg)
    execution.prompt_tokens_budgeted_physical = 87000
    execution.prompt_tokens_effective = 1000
    execution.completion_tokens_actual = 1000
    reservation = llm_mod._reservar_requisicao_llm(cfg, execution, "sys", "user", 3600)
    assert reservation["budgeted_prompt_tokens"] > 0


def test_provider_token_counts_calibrate_future_context_and_budget():
    cfg = base_config()
    execution = ExecutionContext.from_config(cfg)
    execution.prompt_tokens_estimated_raw = 10000
    execution.prompt_tokens_actual = 14000
    assert execution.prompt_token_calibration == pytest.approx(1.4)
    assert execution.physical_tokens_used == 14000
    calibrated = available_user_prompt_tokens(
        cfg, "system", output_tokens=3600, token_estimate_multiplier=execution.prompt_token_calibration,
    )
    uncalibrated = available_user_prompt_tokens(cfg, "system", output_tokens=3600)
    assert calibrated < uncalibrated



def test_provider_usage_can_calibrate_conservative_estimate_downward():
    cfg = base_config()
    execution = ExecutionContext.from_config(cfg)
    execution.prompt_tokens_estimated_raw = 10000
    execution.prompt_tokens_actual = 7000
    assert execution.prompt_token_calibration == pytest.approx(0.75)
    calibrated = available_user_prompt_tokens(
        cfg, "system", output_tokens=3600, token_estimate_multiplier=execution.prompt_token_calibration,
    )
    uncalibrated = available_user_prompt_tokens(cfg, "system", output_tokens=3600)
    assert calibrated > uncalibrated


def test_provider_usage_reconciles_prompt_reservation_to_physical_truth():
    cfg = base_config()
    execution = ExecutionContext.from_config(cfg)
    execution.begin_call(mode="agent", turn=1, prompt={})
    reservation = llm_mod._reservar_requisicao_llm(cfg, execution, "system", "x" * 9000, 100)
    reserved = execution.prompt_tokens_budgeted_physical
    assert reserved > 0
    llm_mod._finalizar_requisicao_llm(
        cfg, execution, reservation, {"prompt_tokens": max(1, reserved // 2), "cached_prompt_tokens": 0},
    )
    assert execution.prompt_tokens_budgeted_physical == execution.prompt_tokens_actual
    assert execution.physical_tokens_used == execution.prompt_tokens_actual


def test_repeated_provider_truth_reconciles_conservative_estimates_without_cumulative_prompt_fuse():
    cfg=base_config(); execution=ExecutionContext.from_config(cfg)
    for turn in range(1,15):
        execution.begin_call(mode="agent",turn=turn,prompt={})
        reservation=llm_mod._reservar_requisicao_llm(cfg,execution,"system","x"*18000,10)
        local_reserved=int(reservation["budgeted_prompt_tokens"]); actual=max(1,int(local_reserved*0.70))
        llm_mod._finalizar_requisicao_llm(cfg,execution,reservation,{"prompt_tokens":actual,"cached_prompt_tokens":0})
    assert execution.prompt_tokens_actual>0
    assert execution.prompt_tokens_budgeted_physical==execution.prompt_tokens_actual
    usage = execution.usage_view()
    assert "physical_tokens_remaining" not in usage
    assert "physical_tokens_limit" not in usage


def test_cumulative_completion_budget_api_is_removed():
    assert not hasattr(llm_mod,"_completion_budget_remaining")
    assert not hasattr(llm_mod,"_preflight_completion_budget")
