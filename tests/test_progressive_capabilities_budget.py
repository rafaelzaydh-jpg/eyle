import json

import pytest

import eyle.core.agent as core_agent
import llm.executar as llm_mod
from eyle.core.execution_context import ExecutionContext
from eyle.core.token_budget import available_user_prompt_tokens, estimate_tokens
from eyle.runtime.config import ConfigError, validar_config
from tests.canonical import agent_final, agent_tools, base_config, tool_call


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


def test_ungrounded_final_skips_claim_without_semantic_router(monkeypatch):
    called = {"claim": 0}
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda prompt, cfg: agent_final("Oi! Como posso ajudar?"))

    def claim_should_not_run(*args, **kwargs):
        called["claim"] += 1
        raise AssertionError("Claim must not run without grounded runtime state")

    monkeypatch.setattr(core_agent, "executar_verificador_claims", claim_should_not_run)
    status, text, _, details = core_agent.executar_agente(
        "oi", base_config(claims_mode="self_check"), projeto={}, retornar_detalhes=True,
    )
    assert status == "success"
    assert text.startswith("Oi")
    assert called["claim"] == 0
    assert any(
        item.get("decision") == "claim_review" and item.get("outcome") == "skipped"
        and item.get("reason") == "NO_GROUNDED_STATE"
        for item in details["decision_history"]
    )


def test_release_training_budget_is_98k_and_context_is_capped_at_32k():
    cfg = base_config()
    validar_config(cfg)
    execution = ExecutionContext.from_config(cfg)
    assert execution.max_prompt_tokens == 90000
    assert execution.max_completion_tokens == 8000
    assert execution.max_total_tokens == 98000
    assert cfg["llm"]["context_window_tokens"] == 32768

    too_large = base_config()
    too_large["agent"]["max_total_tokens"] = 98001
    with pytest.raises(ConfigError, match="max_total_tokens"):
        validar_config(too_large)

    too_wide = base_config()
    too_wide["llm"]["context_window_tokens"] = 32769
    with pytest.raises(ConfigError, match="context_window_tokens"):
        validar_config(too_wide)

    # The physical compiler also clamps defensively if validation is bypassed.
    bypassed = base_config()
    bypassed["llm"]["context_window_tokens"] = 100000
    canonical = base_config()
    assert available_user_prompt_tokens(bypassed, "system", output_tokens=3600) == available_user_prompt_tokens(canonical, "system", output_tokens=3600)


def test_98k_budget_counts_full_prompt_attempts_not_cache_discount():
    cfg = base_config()
    execution = ExecutionContext.from_config(cfg)
    # Keep prompt below its 90k cap, but leave too little room under the 98k
    # physical message envelope for another Agent response reservation.
    execution.max_prompt_tokens = 98000
    execution.max_completion_tokens = 98000
    execution.prompt_tokens_estimated_raw = 95000
    execution.prompt_tokens_budgeted_physical = 95000
    execution.prompt_tokens_effective = 1000  # cache discount must not bypass the hard cap
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
