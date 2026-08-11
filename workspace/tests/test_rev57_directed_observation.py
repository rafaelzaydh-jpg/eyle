from __future__ import annotations

import json
from pathlib import Path

import eyle.core.agent as core_agent
import eyle.core.tools as tools
from eyle.core.observation import observation_signature
from eyle.core.observation_contract import register_snapshot_handle
from eyle.core.session import AgentSession
from tests.canonical import base_config


def _ctx(root: Path, *, handles=None, epoch=0):
    return {
        "projeto": {"caminho_origem": str(root)},
        "config": base_config(),
        "observation_handles": handles if handles is not None else {},
        "workspace_epoch": epoch,
    }


def test_reachability_query_materializes_complete_path_from_auto_entrypoint(tmp_path):
    (tmp_path / "main.py").write_text(
        "from flow import a\n\n"
        "def main():\n    a()\n\n"
        "if __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (tmp_path / "flow.py").write_text(
        "from target import target\n\n"
        "def a():\n    b()\n\n"
        "def b():\n    c()\n\n"
        "def c():\n    target()\n",
        encoding="utf-8",
    )
    (tmp_path / "target.py").write_text("def target():\n    return 1\n", encoding="utf-8")

    result = tools.executar_tool(
        "symbol_relations", {"symbol": "target", "query": "reachability"}, _ctx(tmp_path),
    )
    assert result["ok"] is True
    assert result["coverage"]["objective_complete"] is True
    assert result["coverage"]["objective_result"] == "reachable"
    assert result["frontiers"] == []
    observation = result["observations"][0]
    assert observation["kind"] == "structural_reachability"
    assert observation["value"] == "reachable"
    assert observation["path"][0] == "main.py::<module>"
    assert observation["path"][-1] == "target.py::target"
    assert len(observation["path_edges"]) == len(observation["path"]) - 1
    # Directed mode must not dump every local relation family into the result.
    assert result["detail"]["incoming"] == []
    assert result["detail"]["outgoing"] == []
    assert result["detail"]["unresolved_dynamic"] == []


def test_generic_dynamic_negative_is_one_nonexpandable_boundary(tmp_path):
    (tmp_path / "main.py").write_text(
        "def main():\n"
        "    name = 'target'\n"
        "    globals()[name]()\n\n"
        "if __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (tmp_path / "target.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    handles = {}
    context = _ctx(tmp_path, handles=handles)

    result = tools.executar_tool(
        "symbol_relations", {"symbol": "target", "query": "reachability"}, context,
    )
    assert result["coverage"]["objective_complete"] is False
    assert result["coverage"]["objective_result"] == "inconclusive"
    assert result["coverage"]["depth_mode"] == "auto_exhaustive"
    assert result["observations"][0]["value"] == "not_found_in_resolved_graph"
    frontier = next(item for item in result["frontiers"] if item["kind"] == "dynamic_resolution_boundary")
    assert frontier["expandable"] is False
    assert "handle" not in frontier
    assert result["handles"] == []


def test_target_directed_dynamic_frontier_is_expandable(tmp_path):
    (tmp_path / "main.py").write_text(
        "def main():\n"
        "    getattr(mod, 'target')()\n\n"
        "if __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (tmp_path / "target.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    handles = {}
    context = _ctx(tmp_path, handles=handles)
    result = tools.executar_tool(
        "symbol_relations", {"symbol": "target", "query": "reachability"}, context,
    )
    frontier = next(item for item in result["frontiers"] if item["kind"] == "unresolved_dynamic")
    assert frontier["handle"].startswith("handle:")
    assert frontier["handle"] in handles
    expanded = tools.executar_tool("expand_observation", {"handle": frontier["handle"]}, context)
    assert expanded["ok"] is True
    assert any("target" in str(item.get("expression") or "") for item in expanded["observations"])


def test_snapshot_handle_is_stale_after_workspace_epoch_changes(tmp_path):
    (tmp_path / "main.py").write_text(
        "def main():\n    getattr(mod, 'target')()\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (tmp_path / "x.py").write_text("def target():\n    pass\n", encoding="utf-8")
    handles = {}
    result = tools.executar_tool(
        "symbol_relations", {"symbol": "target", "query": "reachability"},
        _ctx(tmp_path, handles=handles, epoch=0),
    )
    handle = result["handles"][0]["id"]
    stale = tools.executar_tool(
        "expand_observation", {"handle": handle}, _ctx(tmp_path, handles=handles, epoch=1),
    )
    assert stale["ok"] is False
    assert stale["error_code"] == "HANDLE_STALE"
    assert stale["retryable"] is True


def test_query_is_part_of_symbol_relations_observation_identity():
    local = observation_signature("symbol_relations", {"symbol": "x"})
    directed = observation_signature("symbol_relations", {"symbol": "x", "query": "reachability"})
    assert local != directed


def test_capability_contract_exposes_domain_neutral_effect_class():
    catalog = {item["name"]: item for item in tools.gerar_catalogo_tools()}
    assert catalog["read_file"]["effect"] == "observe"
    assert catalog["run_command"]["effect"] == "execute"
    assert catalog["memory_store"]["effect"] == "mutate"
    assert catalog["expand_observation"]["effect"] == "observe"


def test_model_projection_keeps_directed_path_once(tmp_path):
    (tmp_path / "main.py").write_text(
        "def target():\n    return 1\n\nif __name__ == '__main__':\n    target()\n",
        encoding="utf-8",
    )
    raw = tools.executar_tool(
        "symbol_relations", {"symbol": "target", "query": "reachability"}, _ctx(tmp_path),
    )
    model = core_agent._model_tool_result(
        AgentSession("inspect"), "symbol_relations", raw, base_config(),
        {"symbol": "target", "query": "reachability"},
    )
    assert model["observations"][0]["path"][-1] == "main.py::target"
    assert "observations" not in model["detail"]
    assert "coverage" not in model["detail"]
    assert len(json.dumps(model, ensure_ascii=False)) < len(json.dumps(raw, ensure_ascii=False))


def test_snapshot_handles_survive_session_roundtrip():
    session = AgentSession("inspect")
    handle = register_snapshot_handle(
        session.observation_ledger["handles"],
        kind="test_snapshot",
        payload={"items": [{"value": 1}, {"value": 2}]},
        workspace_epoch=session.workspace_epoch,
        source_tool="test_tool",
    )
    restored = AgentSession.from_dict(session.to_dict())
    assert handle["id"] in restored.observation_ledger["handles"]
    assert restored.observation_ledger["handles"][handle["id"]]["payload"]["items"][1]["value"] == 2


def test_missing_target_is_not_declared_complete_when_parse_boundary_exists(tmp_path):
    (tmp_path / "main.py").write_text(
        "def main():\n    return 1\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text("def nope(:\n", encoding="utf-8")
    result = tools.executar_tool(
        "symbol_relations", {"symbol": "missing_target", "query": "reachability"}, _ctx(tmp_path),
    )
    assert result["coverage"]["objective_complete"] is False
    assert result["coverage"]["objective_result"] == "target_not_resolved"
    assert any(item["kind"] == "parse_errors" for item in result["frontiers"])
