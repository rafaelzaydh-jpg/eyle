from __future__ import annotations

from pathlib import Path

import eyle.core.agent as core_agent
from eyle.core import observation, tools
from eyle.core.observation import record
from eyle.core.observation_contract import materialize_snapshot_handle, register_snapshot_handle, release_snapshot_handle
from eyle.core.session import AgentSession
from tests.canonical import base_config


def _ctx(root, session=None, *, max_ranges=2, max_matches=2):
    cfg = base_config()
    cfg["agent"]["max_search_ranges"] = max_ranges
    cfg["agent"]["max_search_matches"] = max_matches
    value = {
        "projeto": {"caminho_origem": str(root)},
        "config": cfg,
        "workspace_epoch": 0,
    }
    if session is not None:
        value["observation_ledger"] = session.observation_ledger
    return value


def _observe(session, name, arguments, result, cfg):
    projected = core_agent._model_tool_result(session, name, result, cfg, arguments)
    record(session, tools.capability_observation_signature(name, arguments), name, arguments, result, projected)
    return projected


def test_snapshot_payload_is_stored_once_across_pages_and_gc_after_last_handle():
    ledger = {"handles": {}, "snapshots": {}}
    payload = {"items": [{"n": value} for value in range(9)]}
    first = register_snapshot_handle(
        ledger, kind="demo.items", payload=payload, workspace_epoch=4,
        source_tool="demo", page_size=3,
    )
    assert len(ledger["snapshots"]) == 1
    assert len(ledger["handles"]) == 1
    assert "payload" not in ledger["handles"][first["id"]]

    page1, error = materialize_snapshot_handle(ledger, first["id"], workspace_epoch=4)
    assert error is None
    second = page1["frontiers"][0]["handle"]
    assert len(ledger["snapshots"]) == 1
    assert ledger["handles"][first["id"]]["snapshot_id"] == ledger["handles"][second]["snapshot_id"]
    assert all("payload" not in item for item in ledger["handles"].values())

    release_snapshot_handle(ledger, first["id"])
    assert len(ledger["snapshots"]) == 1
    page2, error = materialize_snapshot_handle(ledger, second, workspace_epoch=4)
    assert error is None
    third = page2["frontiers"][0]["handle"]
    release_snapshot_handle(ledger, second)
    assert len(ledger["snapshots"]) == 1

    page3, error = materialize_snapshot_handle(ledger, third, workspace_epoch=4)
    assert error is None and page3["coverage"]["complete"] is True
    release_snapshot_handle(ledger, third)
    assert ledger["handles"] == {}
    assert ledger["snapshots"] == {}


def test_search_coverage_is_complete_while_materialization_frontier_remains(tmp_path):
    for index in range(8):
        (tmp_path / f"f{index}.py").write_text("needle\n", encoding="utf-8")
    session = AgentSession("search")
    ctx = _ctx(tmp_path, session)
    result = tools.executar_tool("search_code", {"query": "needle"}, ctx)

    coverage = result["coverage"]
    assert coverage["scope"]["kind"] == "literal_search"
    assert coverage["complete"] is True
    assert coverage["examined"]["matches"] == 8
    assert coverage["facts"]["materialization_complete"] is False
    assert result["frontiers"]
    assert len(session.observation_ledger["snapshots"]) == 1


def test_search_frontier_materializes_real_file_material_and_reuses_one_snapshot(tmp_path):
    for index in range(8):
        (tmp_path / f"f{index}.py").write_text("needle\n", encoding="utf-8")
    session = AgentSession("search")
    ctx = _ctx(tmp_path, session)
    args = {"query": "needle"}
    raw = tools.executar_tool("search_code", args, ctx)
    model = _observe(session, "search_code", args, raw, ctx["config"])
    frontier_id = model["frontiers"][0]["id"]
    snapshot_ids = set(session.observation_ledger["snapshots"])
    assert len(snapshot_ids) == 1

    continued = tools.executar_tool("continue_observation", {"frontier": frontier_id}, ctx)
    assert continued["ok"] is True
    assert continued["observations"]
    first = continued["observations"][0]
    assert first["source_capability"] == "search_code"
    assert first["locator"]["kind"] == "file"
    assert "needle" in first["content"]
    assert set(session.observation_ledger["snapshots"]) == snapshot_ids
    assert all("payload" not in item for item in session.observation_ledger["handles"].values())


def test_every_grounding_capability_owns_material_coverage_and_frontier_projection():
    for name, spec in tools.TOOLS.items():
        if not spec.get("produces_grounding"):
            continue
        assert callable(spec.get("observe")), name
        assert callable(spec.get("coverage")), name
        assert callable(spec.get("frontier")), name
        assert "signature" in spec, name


def test_observation_is_locator_generic_not_file_aware():
    source = Path(observation.__file__).read_text(encoding="utf-8")
    forbidden = (
        "read_file", "search_code", "list_tree", "find_symbol", "symbol_relations",
        "ler_faixa_projeto", "file_hash", "source_hash", 'locator.get("kind") == "file"',
    )
    for token in forbidden:
        assert token not in source


def test_every_capability_owns_complete_physical_output_hook_surface():
    for name, spec in tools.TOOLS.items():
        assert callable(spec.get("fn")), name
        assert "signature" in spec, name  # callable or explicit None: capability owns memoization policy
        assert callable(spec.get("observe")), name
        assert callable(spec.get("coverage")), name
        assert callable(spec.get("frontier")), name


def test_new_non_file_capability_plugs_into_physical_contract_without_core_branch(monkeypatch):
    name = "sensor_scan"

    def execute(arguments, ctx):
        return tools._sucesso({"device": arguments["device"], "temperature_c": 23.5, "sample": 7})

    def observe(arguments, result):
        detail = result["detail"]
        return [{
            "locator": {"kind": "device", "id": detail["device"], "channel": "temperature"},
            "source_version": str(detail["sample"]),
            "content": str(detail["temperature_c"]),
            "source_type": "sensor_sample",
        }]

    def coverage(arguments, result):
        return {
            "scope": {"kind": "device", "id": arguments["device"]},
            "examined": {"channels": 1},
            "complete": True,
            "boundaries": [],
        }

    spec = {
        "description": "Test-only physical sensor capability.",
        "availability": "global",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "One sensor sample.",
        "input_schema": tools._schema_objeto({"device": {"type": "string", "minLength": 1}}, ["device"]),
        "fn": execute,
        "signature": lambda arguments: "sensor:" + arguments["device"],
        "observe": observe,
        "coverage": coverage,
        "frontier": lambda arguments, result: [],
        "limits": {},
        "effect": "observe",
    }
    monkeypatch.setitem(tools.TOOLS, name, spec)

    raw = tools.executar_tool(name, {"device": "sensor-7"}, {})
    assert raw["ok"] is True
    assert raw["coverage"]["complete"] is True
    assert raw["observations"][0]["locator"]["kind"] == "device"
    assert raw["observations"][0]["source_capability"] == name

    ledger = observation.empty_ledger()
    ids = observation.register_material_candidates(ledger, raw["observations"])
    assert ids == ["mat-0001"]
    assert ledger["materials"]["mat-0001"]["source_version"] == "7"
