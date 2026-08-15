from __future__ import annotations

import json
from pathlib import Path

import pytest

from eyle.capabilities import Provider, build_registry
from eyle.contracts.capability import result
from eyle.core.ecc import catalog
from eyle.core.memory import apply_memory_sidecar, memory_graph_view
from eyle.core.session import AgentSession
from eyle.runtime.ecc_runtime import dispatch
from eyle.runtime.memory_graph import world_scope
from llm.structured import StructuredResponseError, parse_profile_response, schema_for_profile
from tests.canonical import base_config


def _objective(disposition="unchanged", state=None):
    return {"disposition": disposition, "state": state}


def _memory(disposition="unchanged", operations=None, focus=None):
    return {"focus": list(focus or []), "disposition": disposition, "operations": list(operations or [])}


def test_rev25_memory_disposition_is_explicit_llm_judgment_not_runtime_semantics():
    schema = schema_for_profile("ecc")
    for variant in schema["oneOf"]:
        memory = variant["properties"]["memory"]
        assert memory["properties"]["disposition"]["enum"] == ["unchanged", "updated"]
    parsed = parse_profile_response({"type": "concluir", "response": "ok", "objective": _objective(), "memory": _memory()}, "ecc")
    assert parsed["memory"]["disposition"] == "unchanged"
    with pytest.raises(StructuredResponseError, match="updated"):
        parse_profile_response({"type": "concluir", "response": "x", "objective": _objective(), "memory": _memory("updated")}, "ecc")
    with pytest.raises(StructuredResponseError, match="unchanged"):
        parse_profile_response({
            "type": "concluir", "response": "x", "objective": _objective(),
            "memory": _memory("unchanged", [{"op": "remember", "scope": "user", "kind": "fact", "content": "x"}]),
        }, "ecc")


def test_rev25_world_scope_is_opaque_and_not_filesystem_semantics():
    robot = world_scope("robot:home-assistant-01")
    network = world_scope("network:store-77")
    desktop = world_scope("desktop:user-machine")
    assert robot.startswith("world:") and network.startswith("world:") and desktop.startswith("world:")
    assert len({robot, network, desktop}) == 3
    # Core hashes the host identity as opaque text; it does not realpath or parse a domain.
    assert world_scope("robot:./home") != world_scope("robot:home")


def test_rev25_every_physical_material_becomes_evidence_without_memory_update(tmp_path):
    state = {"cam-1": "v1"}

    def observe_fn(arguments, _ctx):
        return result("success", True, True, detail={"frame": 7})

    def observe_material(arguments, _result):
        return [{
            "source_type": "sensor.frame",
            "locator": {"device": arguments["device"], "frame": 7},
            "content": "person at center",
            "content_hash": "frame-hash-7",
        }]

    provider = Provider("sensor", {
        "observe": {
            "description": "Observe one sensor frame.",
            "input_schema": {"type": "object", "properties": {"device": {"type": "string"}}, "required": ["device"], "additionalProperties": False},
            "returns": "One physical sensor frame.",
            "effect": "observe", "fn": observe_fn, "observe": observe_material,
            "produces_grounding": True, "ecc_name": "sense",
            "freshness_token": lambda arguments, _ctx: state.get(arguments.get("device")),
            "freshness_arguments": lambda arguments: {"device": arguments.get("device")},
        }
    })
    registry = build_registry([provider])
    session = AgentSession("where is the person?")
    pc = {"sensor": {}, "core_memory": {"storage_dir": str(tmp_path / "memory"), "world_scope_id": "robot:test"}}
    out = dispatch(
        session, action_kind="explorar", operation="sense", arguments={"device": "cam-1"},
        config=base_config(), provider_context=pc, registry=registry,
        pending_schema_version="9-ecc", validate_pending=lambda value, persisted=False: value,
    )
    assert out.result["ok"] is True
    assert out.result["grounding_ids"] == ["mat-0001"]
    assert out.result["evidence_ids"] == ["ev-0001"]
    assert len(session.evidence) == 1
    assert apply_memory_sidecar(session, _memory(), registry=registry, provider_context=pc)["changed"] is False
    assert memory_graph_view(session, query=session.request, registry=registry, config=base_config(), provider_context=pc)["nodes"] == []


def test_rev25_generic_provider_selector_and_freshness_anchor_are_domain_neutral(tmp_path):
    state = {"router-7": "cfg-1"}

    def fn(arguments, _ctx):
        return result("success", True, True, detail={"sessions": 2})

    def observe(arguments, _result):
        return [{
            "source_type": "network.sessions",
            "locator": {"device": arguments["device"], "snapshot": "s-1"},
            "content": "alice=gold; bob=basic",
            "content_hash": "sessions-hash",
        }]

    def select(material, selector):
        customer = selector.get("customer") if isinstance(selector, dict) else None
        if customer not in {"alice", "bob"}:
            raise ValueError("EVIDENCE_SELECTOR_INVALID")
        return {
            "locator": {**material["locator"], "customer": customer},
            "content_hash": f"sessions-hash:{customer}",
        }

    provider = Provider("network", {
        "sessions": {
            "description": "Observe live sessions.",
            "input_schema": {"type": "object", "properties": {"device": {"type": "string"}}, "required": ["device"], "additionalProperties": False},
            "returns": "Current sessions.", "effect": "observe", "fn": fn, "observe": observe,
            "produces_grounding": True, "ecc_name": "sessions", "evidence_selector": select,
            "freshness_token": lambda arguments, _ctx: state.get(arguments.get("device")),
            "freshness_arguments": lambda arguments: {"device": arguments.get("device")},
        }
    })
    registry = build_registry([provider])
    session = AgentSession("remember Alice's package")
    pc = {"network": {}, "core_memory": {"storage_dir": str(tmp_path / "memory"), "world_scope_id": "network:store-77"}}
    dispatch(
        session, action_kind="explorar", operation="sessions", arguments={"device": "router-7"},
        config=base_config(), provider_context=pc, registry=registry,
        pending_schema_version="9-ecc", validate_pending=lambda value, persisted=False: value,
    )
    sidecar = _memory("updated", [{
        "op": "remember", "scope": "world", "kind": "customer_package",
        "content": "Alice is currently on Gold", "tags": ["alice", "gold"],
        "supports": [{"kind": "material", "material_id": "mat-0001", "selector": {"customer": "alice"}}],
    }])
    outcome = apply_memory_sidecar(session, sidecar, registry=registry, provider_context=pc)
    assert outcome["ok"] is True and outcome["changed"] is True
    fresh = memory_graph_view(session, query="Alice", registry=registry, config=base_config(), provider_context=pc)
    node = fresh["nodes"][0]
    assert node["freshness"] == "fresh"
    assert node["sources"][0]["locator"] == {"customer": "alice", "device": "router-7", "snapshot": "s-1"}

    state["router-7"] = "cfg-2"
    stale = memory_graph_view(session, query="Alice", registry=registry, config=base_config(), provider_context=pc)
    assert stale["nodes"][0]["freshness"] in {"stale", "degraded"}


def test_rev25_core_surface_has_only_ecc_and_provider_body_operations(tmp_path):
    provider = Provider("robot", {
        "pose": {
            "description": "Read pose.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "returns": "Pose.", "effect": "observe", "fn": lambda a, c: result("success", True, True),
            "ecc_name": "pose",
        }
    })
    registry = build_registry([provider])
    available = registry.available_names({"config": base_config(), "provider_context": {"robot": {}}})
    surface = catalog(registry, base_config(), available)
    assert set(surface) == {"guidance", "explorar", "construir"}
    assert [item["operation"] for item in surface["explorar"]] == ["pose", "recall"]
    assert surface["construir"] == []
