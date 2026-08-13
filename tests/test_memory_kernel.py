from __future__ import annotations

import sqlite3

import pytest

from eyle.core.memory import (
    activate_memory,
    apply_memory_changeset,
    continue_memory_view,
    memory_history,
    memory_record,
)
from eyle.core.memory_store import MEMORY_SCHEMA_VERSION, memory_db_path


def test_memory_kernel_atomic_changeset_relations_and_supersession(tmp_path):
    base = str(tmp_path / "state")
    root = str(tmp_path / "project")
    first = apply_memory_changeset(base, root, [{
        "op": "create_memory", "id": "mem-old", "region": "project:eyle",
        "content": "EvidenceLedger exists.", "tags": ["architecture", "evidence"],
        "provenance": {"kind": "user", "ref": "msg-1"},
    }])
    assert first["count"] == 1
    old = memory_record(base, root, "mem-old")

    change = apply_memory_changeset(base, root, [
        {
            "op": "create_memory", "id": "mem-new", "region": "project:eyle",
            "content": "Material direct grounding replaced EvidenceLedger.",
            "tags": ["architecture", "grounding"],
            "provenance": {"kind": "observation", "ref": "mat-42"},
        },
        {
            "op": "supersede_memory", "id": "mem-old",
            "expected_revision": old["revision"], "superseded_by": "mem-new",
        },
    ])
    assert change["count"] == 2
    old = memory_record(base, root, "mem-old")
    assert old["status"] == "superseded"
    assert any(rel["label"] == "superseded_by" and rel["target"] == "mem-new" for rel in old["relations"])
    assert memory_record(base, root, "mem-new")["status"] == "current"


def test_memory_kernel_revision_conflict_rolls_back_whole_changeset(tmp_path):
    base = str(tmp_path / "state")
    root = str(tmp_path / "project")
    apply_memory_changeset(base, root, [{
        "op": "create_memory", "id": "mem-a", "region": "project:test", "content": "A",
    }])
    with pytest.raises(ValueError, match="MEMORY_CONFLICT"):
        apply_memory_changeset(base, root, [
            {"op": "create_memory", "id": "mem-b", "region": "project:test", "content": "B"},
            {"op": "update_memory", "id": "mem-a", "expected_revision": 999, "content": "bad"},
        ])
    with pytest.raises(ValueError, match="MEMORY_NOT_FOUND"):
        memory_record(base, root, "mem-b")
    assert memory_record(base, root, "mem-a")["content"] == "A"


def test_memory_kernel_cross_region_relation_activation(tmp_path):
    base = str(tmp_path / "state")
    root = str(tmp_path / "project")
    apply_memory_changeset(base, root, [
        {"op": "create_memory", "id": "mem-project", "region": "project:eyle", "content": "Rev architecture", "tags": ["architecture"]},
        {"op": "create_memory", "id": "mem-user", "region": "user", "content": "Avoid backward compatibility", "tags": ["preference"]},
        {"op": "create_relation", "id": "rel-pref", "source": "mem-user", "label": "relevant_to", "target": "mem-project"},
    ])
    view = activate_memory(base, root, region="project:eyle", related_to=["mem-project"], limit=10)
    ids = {item["id"] for item in view["memories"]}
    assert "mem-project" in ids
    assert "mem-user" in ids
    assert view["memory_coverage"]["regions"] == ["project:eyle"]

    related_only = activate_memory(base, root, related_to=["mem-project"], limit=10)
    assert {item["id"] for item in related_only["memories"]} == {"mem-project", "mem-user"}
    assert related_only["memory_frontier"] is None


def test_memory_kernel_10000_nodes_stay_bounded_and_frontier_continues(tmp_path):
    base = str(tmp_path / "state")
    root = str(tmp_path / "project")
    operations = [
        {
            "op": "create_memory", "id": f"mem-{index:05d}", "region": "project:scale",
            "content": f"node {index}", "tags": ["scale"],
        }
        for index in range(10_000)
    ]
    result = apply_memory_changeset(base, root, operations)
    assert result["count"] == 10_000

    view = activate_memory(base, root, region="project:scale", tags=["scale"], limit=30)
    assert len(view["memories"]) == 30
    assert view["memory_coverage"]["examined"]["ordered_candidates"] == 10_000
    assert view["memory_coverage"]["complete"] is False
    frontier = view["memory_frontier"]
    assert frontier and frontier["remaining_count"] == 9_970

    second = continue_memory_view(base, root, frontier["id"], limit=30)
    assert len(second["memories"]) == 30
    assert second["memory_frontier"]["remaining_count"] == 9_940
    assert {item["id"] for item in view["memories"]}.isdisjoint({item["id"] for item in second["memories"]})


def test_memory_kernel_history_is_append_only_and_survives_reopen(tmp_path):
    base = str(tmp_path / "state")
    root = str(tmp_path / "project")
    apply_memory_changeset(base, root, [{
        "op": "create_memory", "id": "mem-history", "region": "knowledge", "content": "v1",
    }])
    node = memory_record(base, root, "mem-history")
    apply_memory_changeset(base, root, [{
        "op": "update_memory", "id": "mem-history", "expected_revision": node["revision"],
        "content": "v2", "add_tags": ["current"],
    }])
    reopened = memory_record(base, root, "mem-history")
    assert reopened["content"] == "v2"
    assert reopened["revision"] == 2
    history = memory_history(base, root, "mem-history")
    assert [item["action"] for item in history[:2]] == ["update_memory", "create_memory"]


def test_memory_kernel_rejects_incompatible_sqlite_schema(tmp_path):
    base = str(tmp_path / "state")
    root = str(tmp_path / "project")
    path = memory_db_path(base, root)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE memory_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        conn.execute("INSERT INTO memory_meta(key,value) VALUES('schema_version','old')")
        conn.execute("INSERT INTO memory_meta(key,value) VALUES('project_root',?)", (root,))
    with pytest.raises(ValueError, match="MEMORY_SCHEMA_INCOMPATIBLE"):
        activate_memory(base, root)
    assert MEMORY_SCHEMA_VERSION == "2.7.5-r1.3.6-memory-kernel-v1"


def test_memory_tools_allow_semantic_memory_without_grounding_and_continue_frontier(monkeypatch, tmp_path):
    from eyle.core import tools

    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(tools, "MEMORY_DIR", str(tmp_path / "state"))
    context = {"projeto": {"caminho_origem": str(root)}, "grounding": {}}
    for index in range(35):
        stored = tools.executar_tool(
            "memory_store",
            {
                "text": f"decision {index}",
                "meta": {"region": "project:test", "tags": ["decision"]},
            },
            context,
        )
        assert stored["ok"] is True

    first = tools.executar_tool(
        "memory_search",
        {"seed": {"region": "project:test", "tags": ["decision"]}, "limit": 10},
        context,
    )
    assert first["ok"] is True
    first_view = first["detail"]["view"]
    assert len(first_view["memories"]) == 10
    frontier = first_view["memory_frontier"]
    assert frontier and frontier["remaining_count"] == 25

    second = tools.executar_tool("memory_search", {"frontier": frontier["id"], "limit": 10}, context)
    assert second["ok"] is True
    second_view = second["detail"]["view"]
    assert len(second_view["memories"]) == 10
    assert {item["id"] for item in first_view["memories"]}.isdisjoint(
        {item["id"] for item in second_view["memories"]}
    )


def test_memory_tool_nested_contract_is_validated(monkeypatch, tmp_path):
    from eyle.core import tools

    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(tools, "MEMORY_DIR", str(tmp_path / "state"))
    context = {"projeto": {"caminho_origem": str(root)}, "grounding": {}}
    result = tools.executar_tool(
        "memory_store",
        {"text": "x", "meta": {"region": "project:test", "mystery": True}},
        context,
    )
    assert result["ok"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert "meta" in str(result["detail"])
