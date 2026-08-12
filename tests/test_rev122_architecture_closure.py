from __future__ import annotations

import inspect
import json
import os
import time

import pytest

import eyle.core.agent as core_agent
from eyle.core import observation, tools
from eyle.core.claim_review import _bounded_runtime_result
from eyle.core.observation import record
from eyle.core.observation_contract import _paged_payload
from eyle.core.session import AgentSession
from tests.canonical import base_config


def _ctx(root, session=None, *, max_ranges=2, max_matches=2):
    cfg = base_config()
    cfg["agent"]["max_search_ranges"] = max_ranges
    cfg["agent"]["max_search_matches"] = max_matches
    value = {"projeto": {"caminho_origem": str(root)}, "config": cfg, "workspace_epoch": 0}
    if session is not None:
        value["observation_ledger"] = session.observation_ledger
    return value


def _observe(session, name, arguments, result, cfg):
    projected = core_agent._model_tool_result(session, name, result, cfg, arguments)
    record(session, tools.capability_observation_signature(name, arguments), name, arguments, result, projected)
    return projected


def test_frontier_page_copies_only_selected_slice():
    class Bomb:
        def __deepcopy__(self, memo):
            raise AssertionError("unselected snapshot item was deep-copied")

    payload = {"kind": "demo", "items": [Bomb(), {"n": 1}, {"n": 2}]}
    page, start, end, total = _paged_payload(payload, offset=1, page_size=1)
    assert (start, end, total) == (1, 2, 3)
    assert page["items"] == [{"n": 1}]


def test_invalid_coverage_shape_fails_capability_contract(monkeypatch):
    name = "banana_sensor"
    monkeypatch.setitem(tools.TOOLS, name, {
        "description": "test", "availability": "global", "produces_grounding": False,
        "effect": "observe", "returns": "test", "input_schema": tools._schema_objeto(),
        "fn": lambda arguments, ctx: tools._sucesso({"value": 1}),
        "signature": None, "observe": lambda arguments, result: [],
        "coverage": lambda arguments, result: {"banana": 123},
        "frontier": lambda arguments, result: [], "limits": {},
    })
    result = tools.executar_tool(name, {}, {})
    assert result["ok"] is False
    assert result["error_code"] == "CAPABILITY_COVERAGE_INVALID"
    assert "missing field" in str(result["detail"])


def test_find_symbol_exhausts_scope_and_frontiers_materialization(tmp_path):
    for index in range(40):
        (tmp_path / f"f{index:02d}.py").write_text("def repeated():\n    return 1\n", encoding="utf-8")
    session = AgentSession("locate")
    ctx = _ctx(tmp_path, session)
    raw = tools.executar_tool("find_symbol", {"symbol": "repeated"}, ctx)
    assert raw["ok"] is True
    assert raw["coverage"]["examined"]["files"] == 40
    assert raw["coverage"]["examined"]["matches"] == 40
    assert raw["coverage"]["complete"] is True
    assert raw["coverage"]["facts"]["materialization_complete"] is False
    assert len(raw["detail"]["matches"]) == 32
    assert raw["frontiers"] and raw["frontiers"][0]["count"] == 8

    model = _observe(session, "find_symbol", {"symbol": "repeated"}, raw, ctx["config"])
    frontier = model["frontiers"][0]["id"]
    continued = tools.executar_tool("continue_observation", {"frontier": frontier}, ctx)
    assert continued["ok"] is True
    assert continued["coverage"]["facts"]["snapshot_exhausted"] is True
    assert continued["coverage"]["facts"]["source_materialization_complete"] is True


def test_continuation_does_not_confuse_snapshot_exhaustion_with_source_materialization(tmp_path):
    for index in range(3):
        (tmp_path / f"f{index}.py").write_text("needle\n", encoding="utf-8")
    session = AgentSession("search")
    ctx = _ctx(tmp_path, session, max_ranges=1, max_matches=1)
    args = {"query": "needle"}
    raw = tools.executar_tool("search_code", args, ctx)
    model = _observe(session, "search_code", args, raw, ctx["config"])
    frontier = model["frontiers"][0]["id"]
    # Remaining snapshot locators point at f1/f2. Remove both before continuation.
    for path in (tmp_path / "f1.py", tmp_path / "f2.py"):
        path.unlink()

    first = tools.executar_tool("continue_observation", {"frontier": frontier}, ctx)
    assert first["ok"] is True
    first_model = _observe(session, "continue_observation", {"frontier": frontier}, first, ctx["config"])
    next_frontier = first_model["frontiers"][0]["id"]
    second = tools.executar_tool("continue_observation", {"frontier": next_frontier}, ctx)
    assert second["ok"] is True
    facts = second["coverage"]["facts"]
    assert facts["snapshot_exhausted"] is True
    assert facts["source_materialization_complete"] is False
    assert second["coverage"]["complete"] is False
    assert any(item.get("kind") == "read_failure" for item in second["coverage"]["boundaries"])


def test_capability_specific_dispatch_is_registry_owned(monkeypatch):
    name = "custom_projection"
    spec = {
        "description": "test", "availability": "global", "produces_grounding": False,
        "effect": "observe", "returns": "test", "input_schema": tools._schema_objeto(),
        "fn": lambda arguments, ctx: tools._sucesso({"raw": "x"}),
        "signature": lambda arguments: "custom", "observe": lambda arguments, result: [],
        "coverage": lambda arguments, result: {}, "frontier": lambda arguments, result: [], "limits": {},
        "public_arguments": lambda arguments: {"owned": True},
        "public_result": lambda result: {"projection": "public"},
        "model_projection": lambda detail, ids, config: {"projection": "model"},
        "covers": lambda arguments, entries, epoch: {"owned": "cover"},
        "resource_failure": lambda arguments, entries, epoch: {"owned": "failure"},
        "normalize": lambda arguments: {**arguments, "normalized": True},
    }
    monkeypatch.setitem(tools.TOOLS, name, spec)
    assert tools.capability_public_arguments(name, {}) == {"owned": True}
    assert tools.capability_public_result(name, tools._sucesso({"raw": "x"}))["projection"] == "public"
    assert tools.capability_model_detail(name, {}, [], {}) == {"projection": "model"}
    assert tools.capability_find_covering(name, {}, {}, 0) == {"owned": "cover"}
    assert tools.capability_find_resource_failure(name, {}, {}, 0) == {"owned": "failure"}
    normalized, error = tools.validar_chamada_tool(name, {})
    assert error is None and normalized["normalized"] is True

    source = inspect.getsource(tools.capability_public_arguments) + inspect.getsource(tools.capability_public_result) + inspect.getsource(tools.capability_model_detail) + inspect.getsource(tools.capability_find_covering)
    assert "if tool" not in source and "if name" not in source


def test_claim_runtime_compaction_has_no_domain_specific_vocabulary():
    source = inspect.getsource(_bounded_runtime_result)
    for word in ("matches_observed", "ranges_materialized", "files_with_matches", '"file"', '"symbol"', '"lines"'):
        assert word not in source
    value = {
        "status": "success", "ok": True, "executed": True, "changed": False,
        "coverage": {"scope": {"kind": "sensor"}, "examined": {"samples": 10}, "complete": True, "boundaries": []},
        "detail": {"temperature": "x" * 2000},
    }
    bounded = _bounded_runtime_result(value, 300)
    assert bounded["coverage"]["scope"]["kind"] == "sensor"
    assert bounded["payload_truncated"] is True


def test_registry_has_one_effect_and_full_hook_surface():
    hooks = {"fn", "signature", "observe", "coverage", "frontier", "freshness", "rehydrate", "public_arguments", "public_result", "model_projection", "covers", "resource_failure", "normalize"}
    for name, spec in tools.TOOLS.items():
        assert spec.get("effect") in {"observe", "execute", "mutate"}, name
        assert "category" not in spec and "effects" not in spec
        assert hooks.issubset(spec), (name, hooks - set(spec))
