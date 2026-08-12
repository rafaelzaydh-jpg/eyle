import json

import pytest

import eyle.core.agent as core_agent
import llm.executar as llm_mod
from eyle.core.execution_context import ExecutionContext
from eyle.core.token_budget import available_user_prompt_tokens, estimate_tokens
from eyle.runtime.config import ConfigError, validar_config
from tests.canonical import agent_final, agent_tools, agent_needs_user, base_config, review, issue, tool_call


def test_capability_index_is_small_and_first_use_expands_only_requested_tool(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            assert payload["active_tools"] == []
            assert any(item.startswith("read_file(") for item in payload["capability_index"])
            assert "available_tools" not in payload
            assert "tool_taxonomy" not in payload
            # The discovery view should stay far below the old ~2.2k-token full catalog.
            assert len(json.dumps(payload["capability_index"], ensure_ascii=False)) < 2200
            assert estimate_tokens(payload["capability_index"], 3) < 600
            return agent_tools(tool_call("read_file", {"path": "app.py"}))
        active = {item["name"] for item in payload["active_tools"]}
        assert active == {"read_file"}
        assert not any(item.startswith("read_file(") for item in payload["capability_index"])
        assert any(item.startswith("search_code(") for item in payload["capability_index"])
        assert estimate_tokens(payload["capability_index"], 3) + estimate_tokens(payload["active_tools"], 3) < 1000
        return agent_final("app.py foi observado.")

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = core_agent.executar_agente(
        "Leia app.py", base_config(claims_mode="off"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "observado" in text
    assert len(prompts) == 2
    assert details["tool_calls"] == 1


def test_ungrounded_final_is_claim_audited_instead_of_auto_accepted(monkeypatch):
    agent_calls=[]; claim_calls=[]
    def fake_agent(prompt,cfg):
        payload=json.loads(prompt); agent_calls.append(payload)
        if len(agent_calls)==1:
            return agent_final("O workspace atual possui exatamente 16 tools públicas.")
        assert "CLAIM_CHALLENGE" in payload["runtime_feedback"]
        return agent_needs_user("Preciso de uma informação do usuário para continuar.")
    def fake_claim(prompt,cfg):
        payload=json.loads(prompt); claim_calls.append(payload); assert payload["observed_material"]==[]
        return review(issues=[issue(kind="scope", grounding_refs=["request:r1","answer:a1"], reason="Current workspace state was not observed.")])
    monkeypatch.setattr(core_agent,"executar_agente_llm",fake_agent); monkeypatch.setattr(core_agent,"executar_verificador_claims",fake_claim)
    status,_,_,details=core_agent.executar_agente("Audite as tools públicas atuais.",base_config(claims_mode="self_check"),projeto={},retornar_detalhes=True)
    assert status=="needs_user" and len(claim_calls)==1
    assert any(x.get("decision")=="claim_review" and x.get("outcome")=="challenge" for x in details["decision_history"])

def test_pure_ungrounded_answer_can_still_pass_claim(monkeypatch):
    calls=[]
    monkeypatch.setattr(core_agent,"executar_agente_llm",lambda prompt,cfg:agent_final("Oi! Como posso ajudar?"))
    def fake_claim(prompt,cfg):
        payload=json.loads(prompt); calls.append(payload); assert payload["observed_material"]==[]; return review(verdict="accept")
    monkeypatch.setattr(core_agent,"executar_verificador_claims",fake_claim)
    status,text,_,details=core_agent.executar_agente("oi",base_config(claims_mode="self_check"),projeto={},retornar_detalhes=True)
    assert status=="success" and text.startswith("Oi") and len(calls)==1
    assert any(x.get("decision")=="claim_review" and x.get("outcome")=="accepted" for x in details["decision_history"])

def test_rev123_uses_38k_physical_context_and_90k_task_fuse_without_cumulative_prompt_completion_fuses():
    cfg=base_config(); validar_config(cfg); execution=ExecutionContext.from_config(cfg)
    assert not hasattr(execution,"max_prompt_tokens")
    assert not hasattr(execution,"max_completion_tokens")
    assert execution.max_total_tokens==90000
    assert cfg["llm"]["context_window_tokens"]==38000
    assert available_user_prompt_tokens(cfg, "", output_tokens=0) == 37500
    assert "max_prompt_tokens" not in cfg["agent"] and "max_completion_tokens" not in cfg["agent"]
    assert all(key not in cfg["agent"] for key in ("max_llm_turns","max_llm_calls","max_tool_calls"))
    too_wide=base_config(); too_wide["llm"]["context_window_tokens"]=38001
    with pytest.raises(ConfigError,match="context_window_tokens"):
        validar_config(too_wide)

def test_task_token_fuse_counts_physical_usage_not_cache_discount():
    cfg = base_config()
    execution = ExecutionContext.from_config(cfg)
    execution.max_total_tokens = 90000
    execution.prompt_tokens_budgeted_physical = 87000
    execution.prompt_tokens_effective = 1000
    execution.completion_tokens_actual = 1000
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._reservar_requisicao_llm(cfg, execution, "sys", "user", 3600)
    assert exc.value.error_code == "MAX_TOTAL_TOKENS_EXCEEDED"


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
    assert execution.physical_tokens_remaining>0

def test_agent_config_has_only_per_call_output_ceiling_no_claim_reserve():
    cfg=base_config(claims_mode="self_check")
    from eyle.core.session import AgentSession
    agent_cfg=core_agent._agent_config(cfg,AgentSession("x"),{})
    assert agent_cfg["llm"]["agent_max_tokens"]==3600
    assert "downstream_completion_reserve_tokens" not in agent_cfg["llm"]

def test_cumulative_completion_budget_api_is_removed():
    assert not hasattr(llm_mod,"_completion_budget_remaining")
    assert not hasattr(llm_mod,"_preflight_completion_budget")
