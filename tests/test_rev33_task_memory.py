from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from eyle.core.memory import apply_memory_sidecar
from eyle.core.session import AgentSession
from eyle.runtime.memory_graph import (
    MEMORY_GRAPH_SCHEMA_VERSION,
    apply_graph_operations,
    graph_overview,
    memory_db_path,
    node_history,
    node_record,
    world_scope,
)
from llm.structured import parse_ecc_response
from tests.canonical import base_config, standard_registry, select_graph_nodes_for_test


def _ctx(tmp_path: Path) -> dict:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return {
        "standard": {"caminho_origem": str(workspace), "eyle_root": str(tmp_path)},
        "core_memory": {
            "storage_dir": str(tmp_path / "memory"),
            "world_scope_id": f"workspace:{workspace.resolve()}",
        },
    }


def test_rev33_task_is_normal_memory_node_with_mechanical_active_state(tmp_path):
    ctx = _ctx(tmp_path)
    session = AgentSession("build app", execution_id="exec-1")
    learned = apply_memory_sidecar(session, [{
        "op": "remember", "key": "task", "scope": "world", "retention": "temporary",
        "kind": "task", "content": "Build the calculator UI",
        "epistemic": {"nature": "goal", "volatility": "task_state"},
        "recall": {"concepts": ["calculator project"]},
    }], registry=standard_registry(), provider_context=ctx)
    assert learned["ok"] is True
    task_id = learned["aliases"]["task"]
    task = node_record(ctx["core_memory"]["storage_dir"], task_id)
    assert task["kind"] == "task"
    assert task["retention"] == "temporary"
    assert task["task"]["state"] == "active"
    assert task["task"]["state_revision"] == 1
    assert task["task"]["created_execution_id"] == "exec-1"


def test_rev33_task_status_is_separate_from_semantic_node_revision(tmp_path):
    ctx = _ctx(tmp_path); reg = standard_registry()
    session = AgentSession("fix", execution_id="exec-task")
    learned = apply_memory_sidecar(session, [{
        "op":"remember","key":"t","scope":"world","retention":"persistent","kind":"task",
        "content":"Repair connection path","epistemic":{"nature":"goal"},
    }], registry=reg, provider_context=ctx)
    task_id = learned["aliases"]["t"]
    before = node_record(ctx["core_memory"]["storage_dir"], task_id)
    changed = apply_memory_sidecar(session, [{
        "op":"task_status","id":task_id,"expected_state_revision":1,"state":"blocked"
    }], registry=reg, provider_context=ctx)
    assert changed["ok"] is True and changed["task_state_changed"] is True
    blocked = node_record(ctx["core_memory"]["storage_dir"], task_id)
    assert blocked["revision"] == before["revision"]
    assert blocked["content"] == before["content"]
    assert blocked["task"]["state"] == "blocked"
    assert blocked["task"]["state_revision"] == 2

    revised = apply_memory_sidecar(session, [{
        "op":"revise","id":task_id,"expected_revision":blocked["revision"],
        "content":"Repair connection path; root cause identified in transport boundary"
    }], registry=reg, provider_context=ctx)
    assert revised["ok"] is True
    after = node_record(ctx["core_memory"]["storage_dir"], task_id)
    assert after["revision"] == before["revision"] + 1
    assert after["task"]["state_revision"] == 2
    assert "root cause" in after["content"]


def test_rev33_task_resolved_updates_lifecycle_and_history_without_erasing_old_semantics(tmp_path):
    ctx = _ctx(tmp_path); reg = standard_registry()
    session = AgentSession("repair", execution_id="exec-2")
    learned = apply_memory_sidecar(session, [{
        "op":"remember","key":"t","scope":"world","retention":"persistent","kind":"task",
        "content":"Function X is being repaired","epistemic":{"nature":"goal"},
    }], registry=reg, provider_context=ctx)
    task_id = learned["aliases"]["t"]
    current = node_record(ctx["core_memory"]["storage_dir"], task_id)
    apply_memory_sidecar(session, [{
        "op":"revise","id":task_id,"expected_revision":current["revision"],
        "content":"Function X now works through connector Y and verified path Z"
    }], registry=reg, provider_context=ctx)
    apply_memory_sidecar(session, [{
        "op":"task_status","id":task_id,"expected_state_revision":1,"state":"resolved"
    }], registry=reg, provider_context=ctx)
    final = node_record(ctx["core_memory"]["storage_dir"], task_id)
    assert final["task"]["state"] == "resolved"
    assert final["task"]["resolved_at"]
    assert "now works" in final["content"]
    history = node_history(ctx["core_memory"]["storage_dir"], task_id)
    assert any(ev["action"] == "create_node" and "being repaired" in str(ev["payload"]) for ev in history["events"])
    assert any(ev["action"] == "set_task_status" and ev["payload"]["to"] == "resolved" for ev in history["task_events"])


def test_rev33_task_relations_reuse_global_graph_and_do_not_limit_recall(tmp_path):
    storage = str(tmp_path / "memory")
    scope = world_scope("project-current")
    apply_graph_operations(storage, [
        {"op":"create_node","id":"mem-task","scope":scope,"kind":"task","content":"Fix current parser","retention":"temporary"},
        {"op":"create_node","id":"mem-local","scope":scope,"kind":"issue","content":"Current parser rejects alias","retention":"temporary"},
        {"op":"create_edge","source":"mem-task","label":"has_issue","target":"mem-local"},
        {"op":"create_node","id":"mem-old-solution","scope":"user","kind":"solution","content":"Previous project solved parser alias by canonicalizing before validation","retention":"persistent","recall":{"concepts":["parser alias resolution"]}},
    ])
    task = node_record(storage, "mem-task")
    assert any(edge["target"] == "mem-local" and edge["label"] == "has_issue" for edge in task["edges"])
    # Global recall is unchanged by the existence of an active task.
    recalled = select_graph_nodes_for_test(storage, world_scope_value=scope, query="parser alias resolution", scope="all")
    assert "mem-old-solution" in recalled["node_ids"]


def test_rev33_memory_overview_exposes_task_directory_without_bodies(tmp_path):
    storage = str(tmp_path / "memory"); scope = world_scope("project")
    apply_graph_operations(storage, [
        {"op":"create_node","id":"mem-task-a","scope":scope,"kind":"task","content":"SECRET TASK BODY"},
        {"op":"create_node","id":"mem-task-b","scope":scope,"kind":"task","content":"OTHER SECRET"},
        {"op":"set_task_status","id":"mem-task-b","expected_state_revision":1,"state":"resolved"},
    ])
    overview = graph_overview(storage, world_scope_value=scope, scope="all")
    assert overview["tasks"]["total"] == 2
    assert overview["tasks"]["active"] == 1
    assert overview["tasks"]["resolved"] == 1
    assert "SECRET TASK BODY" not in str(overview)


def test_rev33_task_status_wire_is_canonical_and_strict():
    parsed = parse_ecc_response({
        "type":"concluir", "response":"ok",
        "memory_delta":[{
            "op":"task_status",
            "arguments":{"id":"mem-task","expected_state_revision":2,"state":"resolved"},
        }],
    })
    assert parsed["memory_delta"] == [{
        "op":"task_status","id":"mem-task","expected_state_revision":2,"state":"resolved"
    }]


def test_rev33_task_state_revision_conflict_rolls_back(tmp_path):
    storage = str(tmp_path / "memory")
    apply_graph_operations(storage, [{"op":"create_node","id":"mem-task","scope":"user","kind":"task","content":"Task"}])
    with pytest.raises(ValueError, match="MEMORY_TASK_STATE_REVISION_CONFLICT"):
        apply_graph_operations(storage, [{"op":"set_task_status","id":"mem-task","expected_state_revision":99,"state":"resolved"}])
    assert node_record(storage, "mem-task")["task"]["state"] == "active"



def test_rev33_active_task_cannot_be_archived_until_terminal(tmp_path):
    storage = str(tmp_path / "memory")
    apply_graph_operations(storage, [{"op":"create_node","id":"mem-task","scope":"user","kind":"task","content":"Task"}])
    with pytest.raises(ValueError, match="MEMORY_TASK_MUST_BE_TERMINAL"):
        apply_graph_operations(storage, [{"op":"archive_node","id":"mem-task","expected_revision":1}])
    apply_graph_operations(storage, [{"op":"set_task_status","id":"mem-task","expected_state_revision":1,"state":"cancelled"}])
    apply_graph_operations(storage, [{"op":"archive_node","id":"mem-task","expected_revision":1}])
    assert node_record(storage, "mem-task")["status"] == "archived"


def test_rev375_general_memory_change_is_not_execution_progress_contract():
    source = (Path(__file__).parents[1] / "eyle/core/agent.py").read_text(encoding="utf-8")
    marker = "progress_tracker.observe("
    assert marker in source
    call = source[source.index(marker):source.index(marker) + 700]
    assert "task_state_progress=task_state_progress" in call
    assert "memory_changed" not in call
    assert "General Memory edits alone do not count as task progress" in source

def test_rev33_task_can_be_created_and_resolved_in_one_atomic_delta_with_alias(tmp_path):
    ctx = _ctx(tmp_path); reg = standard_registry(); session = AgentSession("instant", execution_id="exec-atomic")
    result = apply_memory_sidecar(session, [
        {"op":"remember","key":"t","scope":"world","retention":"temporary","kind":"task","content":"One-shot task","epistemic":{"nature":"goal"}},
        {"op":"task_status","id":"@t","expected_state_revision":1,"state":"resolved"},
    ], registry=reg, provider_context=ctx)
    assert result["ok"] is True
    task = node_record(ctx["core_memory"]["storage_dir"], result["aliases"]["t"])
    assert task["task"]["state"] == "resolved"
    assert task["task"]["state_revision"] == 2

def test_rev33_no_memory_focus_surface_remains():
    root = Path(__file__).parents[1]
    ecc = (root / "eyle/core/ecc.py").read_text(encoding="utf-8")
    prompt = (root / "llm/executar.py").read_text(encoding="utf-8")
    assert '"operation": "memory_focus"' not in ecc
    assert "- memory_focus:" not in prompt
    assert "Active Projection" in prompt
    assert "HOT/WARM/COLD" in prompt
    assert "hidden working" in prompt
