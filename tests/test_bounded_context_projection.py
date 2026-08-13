import pytest

from eyle.core import agent as core_agent
from eyle.runtime.execution_context import ExecutionContext, bind_execution, reset_execution
from eyle.runtime.observation import register_material_candidates, set_pending_results
from eyle.core.session import AgentSession
from llm import executar as llm_mod
from tests.canonical import base_config


def _material(session, index, text=None):
    ids = register_material_candidates(session.observation_ledger, [{
        "locator": {"kind": "file", "path": f"f{index}.py", "line_start": 1, "line_end": 2},
        "content": text or f"line {index}\nvalue {index}\n",
        "source_type": "file",
        "source_capability": "read_file",
    }])
    return ids[0]


def test_observation_map_is_fresh_delta_not_recent_history():
    session = AgentSession("inspect")
    for turn in range(1, 9):
        gid = _material(session, turn)
        session.observation_ledger["entries"][f"w0:sig-{turn}"] = {
            "turn": turn,
            "capability": "read_file",
            "observation_signature": f"sig-{turn}",
            "grounding_ids": [gid],
            "frontier_ids": [],
            "coverage": {"complete": True, "scope": {"kind": "file"}},
        }
    session.turn = 9
    projected = core_agent._project_observation_map(session)
    rows = [item for item in projected if item.get("capability") == "read_file"]
    assert [item.get("turn") for item in rows] == [8]


def test_observation_map_keeps_old_investigation_coordinate_compactly():
    session = AgentSession("inspect")
    old_gid = _material(session, 1)
    session.observation_ledger["entries"]["w0:old"] = {
        "turn": 1, "capability": "read_file", "observation_signature": "old",
        "grounding_ids": [old_gid], "frontier_ids": [],
        "coverage": {"complete": True, "scope": {"kind": "file"}},
    }
    session.investigation = [{
        "id": "inv-1", "goal": "understand old file", "status": "open",
        "grounding_ids": [old_gid], "reason": "needed",
    }]
    session.turn = 6
    projected = core_agent._project_observation_map(session)
    assert len(projected) == 1
    assert projected[0]["retained_for"] == "investigation_grounding"
    assert projected[0]["grounding_ids"] == [old_gid]
    assert "frontiers" not in projected[0]


def test_grounding_index_projects_pinned_pending_and_tiny_recency_tail():
    session = AgentSession("inspect")
    ids = [_material(session, index) for index in range(1, 13)]
    session.investigation = [{
        "id": "inv-1", "goal": "keep first", "status": "open",
        "grounding_ids": [ids[0]], "reason": "needed",
    }]
    set_pending_results(session, [{"grounding_ids": ids[3:11]}])
    projected = core_agent._project_grounding_index(session)
    projected_ids = [item["id"] for item in projected]
    assert ids[0] in projected_ids
    assert set(ids[5:11]).issubset(projected_ids)
    assert ids[-1] in projected_ids
    assert len(projected) <= 9
    assert next(item for item in projected if item["id"] == ids[0])["pinned"] is True


def test_cached_replay_returns_coordinates_and_small_recall_excerpt():
    session = AgentSession("inspect")
    gid = _material(session, 1, text="x" * 5000)
    entry = {
        "turn": 1,
        "capability": "read_file",
        "grounding_ids": [gid],
        "frontier_ids": [],
        "replay_result": {
            "capability": "read_file", "status": "success", "ok": True, "executed": True,
            "changed": False, "detail": {"numbered_content": "x" * 5000},
            "grounding_ids": [gid],
        },
    }
    replay = core_agent._rehydrate_observation(session, entry, base_config())
    assert replay["replayed"] is True
    assert replay["context_compacted"] is True
    assert replay["detail"]["cached_observation"] is True
    assert replay["detail"]["materials"][0]["grounding_id"] == gid
    assert len(replay["detail"]["materials"][0]["excerpt"]) < 900


def test_claim_reserve_config_is_removed_from_canonical_agent_config():
    cfg = base_config()
    assert "claim_reserve_tokens" not in cfg["agent"]


def test_agent_preflight_has_no_downstream_claim_reserve():
    cfg = base_config()
    execution = ExecutionContext.from_config(cfg)
    execution.prompt_tokens_budgeted_physical = 7000
    reservation = llm_mod._reservar_requisicao_llm(
        cfg, execution, "system", "user" * 200, 1000, profile="agent",
    )
    assert reservation["protected_tokens"] == 0


def test_per_call_context_window_still_fails_closed():
    cfg = base_config()
    cfg["llm"]["context_window_tokens"] = 4000
    execution = ExecutionContext.from_config(cfg)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._reservar_requisicao_llm(
            cfg, execution, "system", "user" * 5000, 1000, profile="agent",
        )
    assert exc.value.error_code == "PROMPT_CONTEXT_BUDGET_EXCEEDED"


def test_agent_output_ceiling_is_not_clamped_for_a_future_claim():
    cfg = base_config()
    execution = ExecutionContext.from_config(cfg)
    execution.prompt_tokens_budgeted_physical = 15000
    token = bind_execution(execution)
    try:
        resolved = core_agent._agent_config(cfg, AgentSession("x"), {})
    finally:
        reset_execution(token)
    assert resolved["llm"]["agent_max_tokens"] == 3600
