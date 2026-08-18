from __future__ import annotations

from pathlib import Path

from eyle.core.memory import apply_memory_sidecar, memory_activate_result, memory_history_result, materialize_explicit_memory_view
from eyle.core.session import AgentSession
from eyle.runtime.memory_graph import (
    MEMORY_GRAPH_SCHEMA_VERSION,
    apply_graph_operations,
    edge_record,
    graph_overview,
    node_record,
    world_scope,
)
from llm.structured import parse_ecc_response
from tests.canonical import base_config, standard_registry, select_graph_nodes_for_test


def _ctx(tmp_path: Path, world: str = "A") -> dict:
    workspace = tmp_path / f"workspace-{world}"
    workspace.mkdir(exist_ok=True)
    return {
        "standard": {"caminho_origem": str(workspace), "eyle_root": str(tmp_path)},
        "core_memory": {
            "storage_dir": str(tmp_path / "memory"),
            "world_scope_id": f"workspace:{workspace.resolve()}",
        },
    }


def test_rev34_identity_is_stable():
    assert MEMORY_GRAPH_SCHEMA_VERSION == "2.7.5-r3.7.1-memory-graph-v12"


def test_rev34_wire_accepts_revision_pinned_memory_and_relation_supports():
    parsed = parse_ecc_response({
        "type": "concluir", "response": "ok", "memory_delta": [
            {"op": "remember", "scope": "user", "retention": "persistent", "kind": "note", "content": "derived",
             "supports": [
                 {"kind": "memory", "memory_id": "mem-source", "revision": 2},
                 {"kind": "relation", "relation_id": "rel-source", "revision": 3},
             ]}
        ]
    })
    supports = parsed["memory_delta"][0]["supports"]
    assert supports == [
        {"kind": "memory", "memory_id": "mem-source", "revision": 2},
        {"kind": "relation", "relation_id": "rel-source", "revision": 3},
    ]


def test_rev34_memory_support_pins_current_revision_and_reports_later_source_revision(tmp_path):
    ctx = _ctx(tmp_path); reg = standard_registry(); session = AgentSession("derive", execution_id="exec-prov")
    first = apply_memory_sidecar(session, [
        {"op": "remember", "key": "a", "scope": "user", "retention": "persistent", "kind": "fact", "content": "A rev1"},
        {"op": "remember", "key": "b", "scope": "user", "retention": "persistent", "kind": "derived", "content": "B from A",
         "supports": [{"kind": "memory", "memory_id": "@a"}]},
    ], registry=reg, provider_context=ctx)
    a, b = first["aliases"]["a"], first["aliases"]["b"]
    raw_b = node_record(ctx["core_memory"]["storage_dir"], b)
    assert raw_b["anchors"][0]["source_ref"] == a
    assert raw_b["anchors"][0]["source_revision"] == 1

    apply_memory_sidecar(session, [
        {"op": "revise", "id": a, "expected_revision": 1, "content": "A rev2"}
    ], registry=reg, provider_context=ctx)
    recall_session = AgentSession("recall")
    recall = memory_activate_result(recall_session, arguments={"ids": [b], "scope": "global"}, registry=reg, config=base_config(), provider_context=ctx)
    view = materialize_explicit_memory_view(recall_session, registry=reg, config=base_config(), provider_context=ctx)
    source = view["nodes"][0]["sources"][0]
    assert source["source_revision"] == 1
    assert source["current_revision"] == 2
    assert source["origin_state"] == "source_revised"
    # Runtime reports lineage drift, not semantic invalidity.
    assert source["status"] == "semantic"


def test_rev34_relation_can_be_semantic_support_and_is_revision_pinned(tmp_path):
    ctx = _ctx(tmp_path); reg = standard_registry(); session = AgentSession("rel", execution_id="exec-rel")
    learned = apply_memory_sidecar(session, [
        {"op":"remember","key":"a","scope":"user","retention":"persistent","kind":"fact","content":"A"},
        {"op":"remember","key":"b","scope":"user","retention":"persistent","kind":"fact","content":"B"},
        {"op":"relate","source":"@a","relation":"connects_to","target":"@b"},
    ], registry=reg, provider_context=ctx)
    a = learned["aliases"]["a"]
    relation_id = node_record(ctx["core_memory"]["storage_dir"], a)["edges"][0]["id"]
    derived = apply_memory_sidecar(session, [
        {"op":"remember","key":"d","scope":"user","retention":"persistent","kind":"derived","content":"relation-derived",
         "supports":[{"kind":"relation","relation_id":relation_id}]}
    ], registry=reg, provider_context=ctx)
    d = derived["aliases"]["d"]
    anchor = node_record(ctx["core_memory"]["storage_dir"], d)["anchors"][0]
    assert anchor["anchor_kind"] == "relation"
    assert anchor["source_ref"] == relation_id
    assert anchor["source_revision"] == 1

    apply_memory_sidecar(session, [
        {"op":"revise_relation","id":relation_id,"expected_revision":1,"relation":"integrates_with"}
    ], registry=reg, provider_context=ctx)
    recall_session = AgentSession("recall")
    recall = memory_activate_result(recall_session, arguments={"ids":[d],"scope":"global"}, registry=reg, config=base_config(), provider_context=ctx)
    view = materialize_explicit_memory_view(recall_session, registry=reg, config=base_config(), provider_context=ctx)
    src = view["nodes"][0]["sources"][0]
    assert src["kind"] == "relation" and src["origin_state"] == "source_revised"
    assert src["source_revision"] == 1 and src["current_revision"] == 2


def test_rev34_history_exposes_changeset_origin_and_revision_provenance(tmp_path):
    ctx = _ctx(tmp_path); reg = standard_registry(); session = AgentSession("history", execution_id="exec-history")
    session.turn = 7
    learned = apply_memory_sidecar(session, [
        {"op":"remember","key":"a","scope":"user","retention":"persistent","kind":"fact","content":"source"},
        {"op":"remember","key":"b","scope":"user","retention":"persistent","kind":"fact","content":"derived","supports":[{"kind":"memory","memory_id":"@a"}]},
    ], registry=reg, provider_context=ctx)
    history = memory_history_result(session, arguments={"id": learned["aliases"]["b"]}, provider_context=ctx)["detail"]
    event = history["events"][0]
    assert event["execution_id"] == "exec-history"
    assert event["turn"] == 7
    assert event["provenance"][0]["source_revision"] == 1


def test_rev34_global_scope_reaches_other_world_but_all_preserves_current_world_contract(tmp_path):
    storage = str(tmp_path / "memory")
    world_a = world_scope("project-A")
    world_b = world_scope("project-B")
    apply_graph_operations(storage, [
        {"op":"create_node","id":"mem-a","scope":world_a,"kind":"note","content":"local alpha","recall":{"concepts":["shared resolver"]}},
        {"op":"create_node","id":"mem-b","scope":world_b,"kind":"solution","content":"remote beta solution","recall":{"concepts":["shared resolver"]}},
    ])
    local = select_graph_nodes_for_test(storage, world_scope_value=world_a, query="remote beta solution", scope="all")
    assert "mem-b" not in local["node_ids"]
    global_result = select_graph_nodes_for_test(storage, world_scope_value=world_a, query="remote beta solution", scope="global")
    assert "mem-b" in global_result["node_ids"]
    overview = graph_overview(storage, world_scope_value=world_a, scope="global")
    assert overview["nodes"] == 2


def test_rev34_neighbor_recall_preserves_expansion_origin(tmp_path):
    storage = str(tmp_path / "memory"); scope = world_scope("project")
    apply_graph_operations(storage, [
        {"op":"create_node","id":"mem-a","scope":scope,"kind":"topic","content":"needle topic"},
        {"op":"create_node","id":"mem-b","scope":scope,"kind":"detail","content":"neighbor detail"},
        {"op":"create_edge","id":"rel-ab","source":"mem-a","label":"has_detail","target":"mem-b"},
    ])
    result = select_graph_nodes_for_test(storage, world_scope_value=scope, query="needle", scope="all", include_neighbors=True)
    reason = result["reasons"]["mem-b"]
    assert reason == {"source_kind":"neighbor","from_node":"mem-a","via_relation":"rel-ab"}


def test_rev34_final_config_surface_rejects_active_projection_fossil():
    import pytest
    from eyle.runtime.config import ConfigError, validar_config

    cfg = base_config()
    cfg.setdefault("context_engine", {})["task_active_projection"] = False
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD:context_engine:task_active_projection"):
        validar_config(cfg, standard_registry())


