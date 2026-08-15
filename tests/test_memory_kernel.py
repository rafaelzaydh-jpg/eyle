from __future__ import annotations

from pathlib import Path

import pytest

from eyle.runtime.memory_graph import (
    MEMORY_GRAPH_SCHEMA_VERSION,
    apply_graph_operations,
    graph_counts,
    memory_db_path,
    node_record,
    world_scope,
    retrieve_graph,
)


def test_memory_graph_atomic_nodes_edges_and_supersession(tmp_path):
    storage = str(tmp_path / "memory")
    apply_graph_operations(storage, [
        {"op": "create_node", "id": "mem-core", "scope": "world:x", "kind": "component", "content": "Core", "tags": ["ecc"]},
        {"op": "create_node", "id": "mem-runtime", "scope": "world:x", "kind": "component", "content": "Runtime"},
        {"op": "create_edge", "id": "rel-core-runtime", "source": "mem-core", "label": "coordinates", "target": "mem-runtime"},
    ])
    assert graph_counts(storage) == {"nodes": 2, "edges": 1, "isolated_nodes": 0}
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
    assert node_record(storage, "mem-a")["content"] == "A"


def test_memory_graph_topology_exposes_bridge_without_semantic_verdict(tmp_path):
    storage = str(tmp_path / "memory")
    scope = world_scope(str(tmp_path / "project"))
    ops = []
    for name in ("core", "runtime", "llm", "ghost", "helper1", "helper2"):
        ops.append({"op": "create_node", "id": f"mem-{name}", "scope": scope, "kind": "component", "content": name, "tags": [name]})
    ops += [
        {"op": "create_edge", "source": "mem-core", "label": "connects", "target": "mem-runtime"},
        {"op": "create_edge", "source": "mem-core", "label": "connects", "target": "mem-llm"},
        {"op": "create_edge", "source": "mem-core", "label": "connects", "target": "mem-ghost"},
        {"op": "create_edge", "source": "mem-ghost", "label": "calls", "target": "mem-helper1"},
        {"op": "create_edge", "source": "mem-ghost", "label": "calls", "target": "mem-helper2"},
    ]
    apply_graph_operations(storage, ops)
    view = retrieve_graph(storage, world_scope_value=scope, query="core ghost", limit=10)
    by_id = {node["id"]: node for node in view["nodes"]}
    assert by_id["mem-core"]["topology"]["articulation_point"] is True
    assert by_id["mem-ghost"]["topology"]["articulation_point"] is True
    assert "connectivity_score" in by_id["mem-core"]["topology"]
    assert by_id["mem-helper1"]["topology"]["degree"] == 1


def test_memory_graph_user_scope_is_retrievable_across_world_scopes(tmp_path):
    storage = str(tmp_path / "memory")
    scope_a = world_scope(str(tmp_path / "project-a")); scope_b = world_scope(str(tmp_path / "project-b"))
    apply_graph_operations(storage, [
        {"op": "create_node", "id": "mem-user", "scope": "user", "kind": "preference", "content": "User likes dogs", "tags": ["dogs"]},
        {"op": "create_node", "id": "mem-a", "scope": scope_a, "kind": "world", "content": "Only world A"},
    ])
    view = retrieve_graph(storage, world_scope_value=scope_b, query="dogs", limit=10)
    assert [node["id"] for node in view["nodes"]] == ["mem-user"]


def test_memory_graph_10000_nodes_retrieval_is_bounded(tmp_path):
    storage = str(tmp_path / "memory")
    scope = world_scope(str(tmp_path / "project"))
    apply_graph_operations(storage, [
        {"op": "create_node", "id": f"mem-{i:05d}", "scope": scope, "kind": "symbol", "content": f"worker node {i}", "tags": ["worker"]}
        for i in range(10_000)
    ])
    view = retrieve_graph(storage, world_scope_value=scope, query="worker", limit=30)
    assert len(view["nodes"]) == 30
    assert view["retrieval"]["candidate_nodes"] == 10_000


def test_memory_graph_schema_identity(tmp_path):
    storage = str(tmp_path / "memory")
    path = Path(memory_db_path(storage))
    assert not path.exists()  # path resolution alone must not create state
    assert graph_counts(storage) == {"nodes": 0, "edges": 0, "isolated_nodes": 0}
    assert path.exists()
    assert MEMORY_GRAPH_SCHEMA_VERSION == "2.7.5-r2.5-memory-graph-v2"
