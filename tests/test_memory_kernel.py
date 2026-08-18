from __future__ import annotations

from pathlib import Path

import pytest

from tests.canonical import select_graph_nodes_for_test

from eyle.runtime.memory_graph import (
    MEMORY_GRAPH_SCHEMA_VERSION,
    apply_graph_operations,
    graph_counts,
    graph_overview,
    graph_records,
    memory_db_path,
    node_record,
    world_scope,
)


def test_memory_graph_atomic_nodes_edges_and_supersession(tmp_path):
    storage = str(tmp_path / "memory")
    apply_graph_operations(storage, [
        {"op": "create_node", "id": "mem-core", "scope": "world:x", "kind": "component", "content": "Core", "tags": ["ecc"]},
        {"op": "create_node", "id": "mem-runtime", "scope": "world:x", "kind": "component", "content": "Runtime"},
        {"op": "create_edge", "id": "rel-core-runtime", "source": "mem-core", "label": "coordinates", "target": "mem-runtime"},
    ])
    assert graph_counts(storage) == {"nodes": 2, "persistent_nodes": 2, "temporary_nodes": 0, "edges": 1, "isolated_nodes": 0}
    old = node_record(storage, "mem-core")
    apply_graph_operations(storage, [
        {"op": "create_node", "id": "mem-core2", "scope": "world:x", "kind": "component", "content": "New Core"},
        {"op": "supersede_node", "id": "mem-core", "expected_revision": old["revision"], "replacement": "mem-core2"},
    ])
    assert node_record(storage, "mem-core")["status"] == "superseded"
    assert any(edge["label"] == "superseded_by" for edge in node_record(storage, "mem-core")["edges"])


def test_memory_graph_revision_conflict_rolls_back_whole_delta(tmp_path):
    storage = str(tmp_path / "memory")
    apply_graph_operations(storage, [{"op": "create_node", "id": "mem-a", "scope": "world:x", "kind": "fact", "content": "A"}])
    with pytest.raises(ValueError, match="MEMORY_CONFLICT"):
        apply_graph_operations(storage, [
            {"op": "create_node", "id": "mem-b", "scope": "world:x", "kind": "fact", "content": "B"},
            {"op": "update_node", "id": "mem-a", "expected_revision": 999, "content": "bad"},
        ])
    with pytest.raises(ValueError, match="MEMORY_NODE_NOT_FOUND"):
        node_record(storage, "mem-b")


def test_explicit_selection_has_no_topology_fallback(tmp_path):
    storage = str(tmp_path / "memory")
    scope = world_scope("world-a")
    apply_graph_operations(storage, [
        {"op":"create_node","id":"mem-cat","scope":"user","kind":"preference","content":"User likes cats","tags":["cats"]},
        {"op":"create_node","id":"mem-dog","scope":scope,"kind":"fact","content":"Project has dog worker","tags":["worker"]},
        {"op":"create_node","id":"mem-bridge","scope":scope,"kind":"component","content":"Highly connected bridge"},
        {"op":"create_edge","source":"mem-bridge","label":"connects","target":"mem-dog"},
    ])
    selected = select_graph_nodes_for_test(storage, world_scope_value=scope, query="cats", ids=[], tags=[], scope="all", include_neighbors=False)
    assert selected["node_ids"] == ["mem-cat"]
    empty = select_graph_nodes_for_test(storage, world_scope_value=scope, query="banana", ids=[], tags=[], scope="all", include_neighbors=False)
    assert empty["node_ids"] == []
    assert "topology" not in str(empty).lower()


def test_user_scope_crosses_worlds_but_world_scope_does_not(tmp_path):
    storage = str(tmp_path / "memory")
    a, b = world_scope("a"), world_scope("b")
    apply_graph_operations(storage, [
        {"op":"create_node","id":"mem-user","scope":"user","kind":"preference","content":"User likes cats","tags":["cats"]},
        {"op":"create_node","id":"mem-a","scope":a,"kind":"fact","content":"Only world A","tags":["cats"]},
    ])
    selected = select_graph_nodes_for_test(storage, world_scope_value=b, query="cats", ids=[], tags=[], scope="all", include_neighbors=False)
    assert selected["node_ids"] == ["mem-user"]


def test_10000_nodes_selection_is_bounded_only_at_materialization_layer(tmp_path):
    storage = str(tmp_path / "memory")
    scope = world_scope("project")
    apply_graph_operations(storage, [
        {"op":"create_node","id":f"mem-{i:05d}","scope":scope,"kind":"symbol","content":f"worker node {i}","tags":["worker"]}
        for i in range(10_000)
    ])
    selected = select_graph_nodes_for_test(storage, world_scope_value=scope, query="worker", ids=[], tags=[], scope="world", include_neighbors=False)
    assert len(selected["node_ids"]) == 10_000
    assert selected["selection"]["scoped_nodes"] == 10_000
    records = graph_records(storage, selected["node_ids"][:30])
    assert len(records["nodes"]) == 30


def test_overview_is_read_only_and_body_free(tmp_path):
    storage = str(tmp_path / "memory")
    scope = world_scope("project")
    apply_graph_operations(storage, [{"op":"create_node","id":"mem-secret","scope":scope,"kind":"fact","content":"SECRET BODY","tags":["secret"]}])
    before = node_record(storage, "mem-secret")
    overview = graph_overview(storage, world_scope_value=scope, scope="all")
    after = node_record(storage, "mem-secret")
    assert "SECRET BODY" not in str(overview)
    assert before == after


def test_memory_graph_schema_identity(tmp_path):
    storage = str(tmp_path / "memory")
    path = Path(memory_db_path(storage))
    assert not path.exists()
    assert graph_counts(storage) == {"nodes": 0, "persistent_nodes": 0, "temporary_nodes": 0, "edges": 0, "isolated_nodes": 0}
    assert path.exists()
    assert MEMORY_GRAPH_SCHEMA_VERSION == "2.7.5-r3.7.1-memory-graph-v12"


def test_temporary_and_persistent_are_one_graph_with_retention(tmp_path):
    storage = str(tmp_path / "memory")
    scope = world_scope("project")
    apply_graph_operations(storage, [
        {"op":"create_node","id":"mem-temporary","scope":scope,"kind":"active_topic","content":"Editing calc.py","retention":"temporary"},
        {"op":"create_node","id":"mem-durable","scope":"user","kind":"preference","content":"User likes pizza","retention":"persistent"},
        {"op":"create_edge","id":"rel-temporary-durable","source":"mem-temporary","label":"related_to","target":"mem-durable"},
    ])
    assert node_record(storage, "mem-temporary")["retention"] == "temporary"
    assert node_record(storage, "mem-durable")["retention"] == "persistent"
    assert graph_counts(storage)["temporary_nodes"] == 1
    assert graph_counts(storage)["persistent_nodes"] == 1


def test_revise_can_promote_same_temporary_node_to_persistent(tmp_path):
    storage = str(tmp_path / "memory")
    apply_graph_operations(storage, [{"op":"create_node","id":"mem-x","scope":"user","kind":"fact","content":"User has five dogs","retention":"temporary"}])
    before = node_record(storage, "mem-x")
    apply_graph_operations(storage, [{"op":"update_node","id":"mem-x","expected_revision":before["revision"],"retention":"persistent"}])
    after = node_record(storage, "mem-x")
    assert after["id"] == before["id"]
    assert after["retention"] == "persistent"
    assert after["revision"] == before["revision"] + 1


