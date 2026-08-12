import json

import pytest

import eyle.core.agent as core_agent
import llm.executar as llm_mod
from eyle.core.execution_context import ExecutionContext, bind_execution, reset_execution
from eyle.core.operational_feedback import build_operational_feedback
from eyle.core.session import AgentSession
from eyle.runtime.config import ConfigError, validar_config
from tests.canonical import agent_final, agent_tools, base_config, issue, review, tool_call


def test_operational_feedback_projects_problem_and_replay_without_prescribing_strategy():
    session = AgentSession("inspect")
    session.turn = 5
    session.observation_ledger["materials"]["mat-0001"] = {
        "id": "mat-0001", "locator": {"kind": "device", "id": "sensor-7"},
        "content_hash": "abc", "source_type": "sensor", "source_capability": "sensor_scan",
    }
    session.decision_ledger["events"] = [
        {"event_id": "dec-0001", "turn": 2, "decision": "final", "outcome": "provisional",
         "facts": {"grounding_ids": [], "workspace_epoch": 0}},
        {"event_id": "dec-0002", "turn": 2, "decision": "claim_review", "outcome": "challenge",
         "reason": "unsupported", "facts": {"issue_kinds": ["unsupported"], "workspace_epoch": 0}},
        {"event_id": "dec-0003", "turn": 3, "decision": "tool", "outcome": "requested", "tools": ["sensor_scan"]},
        {"event_id": "dec-0004", "turn": 3, "decision": "tool_preflight", "outcome": "replayed",
         "reason": "OBSERVATION_REHYDRATED", "tools": ["sensor_scan"]},
        {"event_id": "dec-0005", "turn": 3, "decision": "tool_preflight", "outcome": "cached",
         "reason": "OBSERVATION_CACHE_HIT", "tools": ["sensor_scan"]},
    ]
    session.observation_ledger["events"] = [
        {"event_id": "obs-0001", "turn": 1, "tool": "sensor_scan", "executed": True,
         "ok": True, "grounding_ids": ["mat-0001"], "frontier_ids": [], "result": {"status": "success"}},
    ]

    execution = ExecutionContext.from_config(base_config())
    token = bind_execution(execution)
    try:
        view = build_operational_feedback(session)
    finally:
        reset_execution(token)

    assert view["last_problem"]["decision"] == "claim_review"
    assert view["last_problem"]["outcome"] == "challenge"
    assert view["last_problem"]["since"]["executed_observations"] == 0
    assert view["last_problem"]["since"]["replay_preflights"] == 1
    assert view["last_problem"]["since"]["workspace_changed"] is False
    assert view["last_provisional_final"]["grounding_ids"] == []
    assert "mat-0001" in view["physical_state"]["available_material_ids"]
    assert view["physical_state"]["replay_only_since_last_executed_observation"] is True
    encoded = json.dumps(view, ensure_ascii=False)
    for forbidden in ("retry this", "stop now", "change tool", "you are in a loop"):
        assert forbidden not in encoded.lower()


def test_main_sees_claim_error_then_replay_only_history_and_can_finish_with_existing_material(monkeypatch, tmp_path):
    (tmp_path / "README.md").write_text("# Eyle\nPhysical observation architecture.\n", encoding="utf-8")
    prompts = []
    claim_calls = []

    def fake_agent(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        n = len(prompts)
        if n == 1:
            return agent_tools(tool_call("read_file", {"path": "README.md"}))
        if n == 2:
            assert payload["grounding_index"]
            return agent_final("README.md descreve a arquitetura física atual.")
        if n == 3:
            op = payload["operational_feedback"]
            assert op["last_problem"]["decision"] == "claim_review"
            assert op["last_problem"]["outcome"] == "challenge"
            assert op["last_provisional_final"]["grounding_ids"] == []
            assert "mat-0001" in op["physical_state"]["available_material_ids"]
            # Deliberately repeat once to prove the next turn can see that it was only replayed.
            return agent_tools(tool_call("read_file", {"path": "README.md"}))
        if n == 4:
            op = payload["operational_feedback"]
            assert op["last_problem"]["since"]["executed_observations"] == 0
            assert op["last_problem"]["since"]["replay_preflights"] == 1
            assert op["physical_state"]["replay_only_since_last_executed_observation"] is True
            return agent_final({
                "answer": "README.md descreve a arquitetura física atual.",
                "limitations": [],
                "grounding_ids": ["mat-0001"],
            })
        raise AssertionError(f"unexpected Main call {n}")

    def fake_claim(prompt, cfg):
        payload = json.loads(prompt)
        claim_calls.append(payload)
        if len(claim_calls) == 1:
            assert payload["observed_material"] == []
            return review(issues=[issue(
                kind="unsupported",
                grounding_refs=["request:r1", "answer:a1"],
                reason="Observed workspace assertion lacks selected Material.",
            )])
        assert payload["observed_material"]
        assert payload["observed_material"][0]["ref"] == "observation:mat-0001"
        return review(verdict="accept")

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_claim)

    status, text, _, details = core_agent.executar_agente(
        "Leia README.md e resuma a arquitetura.",
        base_config(claims_mode="self_check"),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert "arquitetura" in text
    assert details["tool_calls"] == 1
    assert details["observation_replays"] == 1
    assert len(prompts) == 4
    assert len(claim_calls) == 2
    assert details["operational_feedback"]["last_provisional_final"]["grounding_ids"] == ["mat-0001"]


def test_90k_is_default_and_maximum_task_physical_fuse():
    cfg = base_config()
    validar_config(cfg)
    execution = ExecutionContext.from_config(cfg)
    assert execution.max_total_tokens == 90000

    too_high = base_config()
    too_high["agent"]["max_total_tokens"] = 90001
    with pytest.raises(ConfigError, match="max_total_tokens"):
        validar_config(too_high)


def test_90k_fuse_blocks_next_request_before_physical_overspend():
    cfg = base_config()
    execution = ExecutionContext.from_config(cfg)
    execution.prompt_tokens_budgeted_physical = 88000
    execution.prompt_tokens_actual = 88000
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._reservar_requisicao_llm(cfg, execution, "sys", "user", 3600)
    assert exc.value.error_code == "MAX_TOTAL_TOKENS_EXCEEDED"
