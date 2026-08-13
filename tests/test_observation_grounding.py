from __future__ import annotations

import eyle.core.agent as core_agent
import eyle.core.tools as tools
from eyle.core.tools import capability_observation_signature as observation_signature
from eyle.core.investigation import apply_investigation_updates
from eyle.core.observation import (
    material_items,
    record,
)
from eyle.core.session import AgentSession
from llm.structured import contract_instruction, schema_for_profile
from tests.canonical import base_config


def _ctx(root, *, session=None, max_ranges=3, max_matches=3):
    cfg = base_config()
    cfg["agent"]["max_search_ranges"] = max_ranges
    cfg["agent"]["max_search_matches"] = max_matches
    data = {
        "projeto": {"caminho_origem": str(root)},
        "config": cfg,
        "workspace_epoch": 0,
    }
    if session is not None:
        data["observation_ledger"] = session.observation_ledger
    return data


def _observe(session, tool, arguments, result, config=None):
    model = core_agent._model_tool_result(session, tool, result, config or base_config(), arguments)
    record(session, observation_signature(tool, arguments), tool, arguments, result, model)
    return model


def test_search_diversifies_files_before_material_limit(tmp_path):
    (tmp_path / "a.py").write_text("\n".join("needle" if i % 20 == 0 else "x" for i in range(120)) + "\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("needle\n", encoding="utf-8")

    result = tools.executar_tool("search_code", {"query": "needle"}, _ctx(tmp_path, max_ranges=3, max_matches=3))
    detail = result["detail"]
    assert detail["matches_observed"] >= 8
    assert detail["files_with_matches"] == 3
    assert detail["ranges_materialized"] == 3
    assert set(detail["materialized_files"]) == {"a.py", "b.py", "c.py"}
    assert detail["scope_complete"] is True
    assert detail["coverage_complete"] is True
    assert "projection_complete" not in detail


def test_frontier_continuation_keeps_runtime_handle_private(tmp_path):
    for index in range(5):
        (tmp_path / f"f{index}.py").write_text("needle\n", encoding="utf-8")
    session = AgentSession("inspect")
    context = _ctx(tmp_path, session=session, max_ranges=2, max_matches=2)
    args = {"query": "needle"}
    raw = tools.executar_tool("search_code", args, context)
    model = _observe(session, "search_code", args, raw, context["config"])

    assert raw["detail"]["coverage_complete"] is True
    assert model["frontiers"]
    frontier = model["frontiers"][0]
    assert frontier["id"].startswith("fr-")
    assert "handle" not in frontier
    assert "handles" not in model

    continued = tools.executar_tool("continue_observation", {"frontier": frontier["id"]}, context)
    assert continued["ok"] is True
    assert continued["observations"]
    continued_model = _observe(session, "continue_observation", {"frontier": frontier["id"]}, continued, context["config"])
    if continued_model.get("frontiers"):
        next_frontier = continued_model["frontiers"][0]
        assert str(next_frontier.get("id") or "").startswith("fr-")
        assert "handle" not in next_frontier
        assert not str(next_frontier.get("at") or "").startswith("handle:")
        assert "handles" not in continued_model
    again = tools.executar_tool("continue_observation", {"frontier": frontier["id"]}, context)
    assert again["ok"] is False
    assert again["error_code"] == "FRONTIER_CONSUMED"


def test_tool_materialization_creates_canonical_observation_material(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    session = AgentSession("inspect")
    raw = tools.executar_tool("read_file", {"path": "app.py"}, _ctx(tmp_path, session=session))
    projected = core_agent._model_tool_result(session, "read_file", raw, base_config(), {"path": "app.py"})

    assert projected["grounding_ids"] == ["mat-0001"]
    assert list(material_items(session.observation_ledger)) == ["mat-0001"]
    assert not hasattr(session, "source_record_ledger")
    assert not hasattr(session, "evidence_ledger")


def test_investigation_grounding_points_directly_to_observed_material(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    session = AgentSession("inspect")
    raw = tools.executar_tool("read_file", {"path": "app.py"}, _ctx(tmp_path, session=session))
    core_agent._model_tool_result(session, "read_file", raw, base_config(), {"path": "app.py"})

    updated, accepted, rejected = apply_investigation_updates(
        [{"id": "T1", "goal": "Establish VALUE", "status": "established", "grounding_ids": ["mat-0001"], "reason": "Observed."}],
        previous=[], grounding=material_items(session.observation_ledger),
    )
    assert rejected == []
    assert accepted[0]["grounding_ids"] == ["mat-0001"]
    assert updated[0]["grounding_ids"] == ["mat-0001"]










def test_agent_prompt_exposes_physical_frontier_without_prescribing_tool_strategy():
    from llm.executar import PROMPT_AGENTE
    assert "mat-*" in PROMPT_AGENTE
    assert "fr-*" in PROMPT_AGENTE
    assert "handle:" not in PROMPT_AGENTE
    assert "projection_complete" not in PROMPT_AGENTE
    assert "audit" not in PROMPT_AGENTE.lower()
    assert "prefer" not in PROMPT_AGENTE.lower()

def test_persisted_synthetic_material_requires_reexecution(tmp_path):
    session = AgentSession("inspect")
    raw = tools.executar_tool("inspect_project", {}, _ctx(tmp_path, session=session))
    core_agent._model_tool_result(session, "inspect_project", raw, base_config(), {})
    assert "mat-0001" in material_items(session.observation_ledger)

    restored = AgentSession.from_dict(session.to_dict())
    tools.capability_rehydrate_materials(material_items(restored.observation_ledger), str(tmp_path), max_lines=400)
    material = material_items(restored.observation_ledger)["mat-0001"]
    assert material["rehydration_error"] == "OBSERVATION_REEXECUTION_REQUIRED"


def test_old_open_frontier_remains_in_main_navigation_after_recency_compaction(tmp_path):
    for index in range(8):
        (tmp_path / f"f{index}.py").write_text("needle\n" + ("x\n" * 20), encoding="utf-8")
    session = AgentSession("inspect")
    context = _ctx(tmp_path, session=session, max_ranges=2, max_matches=2)
    search_args = {"query": "needle"}
    session.turn = 1
    raw = tools.executar_tool("search_code", search_args, context)
    first = _observe(session, "search_code", search_args, raw, context["config"])
    frontier_id = first["frontiers"][0]["id"]

    for index in range(6):
        session.turn = index + 2
        args = {"path": f"f{index}.py", "line_start": 1, "line_end": 2}
        read = tools.executar_tool("read_file", args, context)
        _observe(session, "read_file", args, read, context["config"])

    projected = core_agent._project_observation_map(session)
    retained = [
        frontier for item in projected for frontier in (item.get("frontiers") or [])
        if frontier.get("id") == frontier_id
    ]
    assert retained
    assert retained[0]["status"] == "open"
    assert any(item.get("retained_for") == "open_frontiers" for item in projected)






