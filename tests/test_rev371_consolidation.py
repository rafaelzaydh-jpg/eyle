from __future__ import annotations

import json
import sqlite3

from eyle.core import agent
from eyle.core.memory import materialize_explicit_memory_view
from eyle.core.session import AgentSession
from eyle.devtools.cognitive_floor import measure_static_cognitive_floor
from eyle.devtools.migrate_memory_v11_to_v12 import migrate_memory_v11_to_v12
from eyle.runtime.context_materializer import materialize_conversation, materialize_latest_observations
from eyle.runtime.memory_graph import (
    MEMORY_GRAPH_SCHEMA_VERSION,
    apply_graph_operations,
    ingest_chat_message,
    memory_db_path,
    node_record,
    world_scope,
)
from llm.executar import PROMPT_ECC
from tests.canonical import base_config, standard_registry, select_graph_nodes_for_test


def _ctx(tmp_path):
    return {
        "standard": {"caminho_origem": str(tmp_path), "eyle_root": str(tmp_path)},
        "core_memory": {
            "storage_dir": str(tmp_path / "memory"),
            "world_scope_id": f"workspace:{tmp_path.resolve()}",
        },
    }


def test_rev371_v12_adds_domain_without_changing_scope_semantics(tmp_path):
    storage = str(tmp_path / "memory")
    scope = world_scope("abc")
    apply_graph_operations(storage, [
        {"op": "create_node", "id": "mem-k", "scope": scope, "kind": "fact", "content": "Keep scope"},
        {"op": "create_node", "id": "mem-t", "scope": scope, "kind": "task", "content": "T"},
    ])
    knowledge = node_record(storage, "mem-k")
    task = node_record(storage, "mem-t")
    assert MEMORY_GRAPH_SCHEMA_VERSION == "2.7.5-r3.7.1-memory-graph-v12"
    assert knowledge["scope"] == scope and knowledge["domain"] == "knowledge" and knowledge["context_key"] is None
    assert task["scope"] == scope and task["domain"] == "task" and task["context_key"] == "mem-t"
    selected = select_graph_nodes_for_test(storage, world_scope_value=scope, query="Keep", ids=[], tags=[], scope="all", include_neighbors=False)
    assert selected["node_ids"] == ["mem-k"]


def test_rev371_v11_to_v12_migration_is_mechanical(tmp_path):
    storage = str(tmp_path / "memory")
    apply_graph_operations(storage, [
        {"op": "create_node", "id": "mem-old", "scope": "user", "kind": "fact", "content": "Keep exactly"},
        {"op": "create_node", "id": "mem-task", "scope": "user", "kind": "task", "content": "Task exactly"},
    ])
    db = memory_db_path(storage)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP INDEX IF EXISTS idx_memory_nodes_domain_context_status_updated")
        conn.execute("ALTER TABLE memory_nodes DROP COLUMN context_key")
        conn.execute("ALTER TABLE memory_nodes DROP COLUMN domain")
        conn.execute("UPDATE memory_meta SET value='2.7.5-r3.6-memory-graph-v11' WHERE key='schema_version'")
        conn.commit()
    finally:
        conn.close()
    migrated = migrate_memory_v11_to_v12(storage)
    assert migrated["status"] == "migrated"
    old = node_record(storage, "mem-old")
    task = node_record(storage, "mem-task")
    assert old["content"] == "Keep exactly" and old["scope"] == "user"
    assert old["domain"] == "knowledge" and old["context_key"] is None
    assert task["content"] == "Task exactly" and task["domain"] == "task" and task["context_key"] == "mem-task"


def test_rev371_runtime_ingested_chat_is_exact_and_recallable(tmp_path):
    storage = str(tmp_path / "memory")
    scope = world_scope("abc")
    node_id = ingest_chat_message(
        storage,
        world_scope_value=scope,
        conversation_id="conv-1",
        message_id=7,
        role="user",
        content="quero falar sobre dinheiro",
        timestamp="2026-08-18T10:00:00",
    )
    node = node_record(storage, node_id)
    assert node["domain"] == "chat"
    assert node["context_key"] == "conv-1"
    assert node["content"] == "quero falar sobre dinheiro"
    found = select_graph_nodes_for_test(storage, world_scope_value=scope, query="dinheiro", ids=[], tags=[], scope="all", include_neighbors=False)
    assert node_id in found["node_ids"]


def test_rev371_conversation_materializes_by_tokens_not_fixed_message_count():
    cfg = base_config()
    cfg["context_engine"]["conversation_materialization_tokens"] = 55
    context = {
        "conversation_id": "conv-1",
        "total_messages": 20,
        "recent_messages": [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i} " + ("x" * 30)}
            for i in range(20)
        ],
    }
    view = materialize_conversation(context, cfg)
    assert 0 < view["history_messages_materialized"] < 20
    assert view["history_messages_omitted"] == 20 - view["history_messages_materialized"]
    assert view["older_history"]["available"] is True
    assert view["older_history"]["continuation"] == "memory_activate"
    assert view["messages"][-1]["content"].startswith("m19 ")


def test_rev371_prompt_contains_current_conversation_but_not_automatic_global_memory(tmp_path):
    registry = standard_registry()
    cfg = base_config()
    ctx = _ctx(tmp_path)
    scope = world_scope(ctx["core_memory"]["world_scope_id"])
    apply_graph_operations(ctx["core_memory"]["storage_dir"], [
        {"op": "create_node", "id": "mem-auto", "scope": scope, "kind": "fact", "content": "SHOULD_NOT_AUTO_PROJECT", "retention": "temporary"},
    ])
    session = AgentSession("e ele ajuda?")
    prompt, _ = agent._compile_prompt(
        session, cfg, ctx,
        {"conversation_id": "conv-1", "total_messages": 2, "recent_messages": [
            {"role": "user", "content": "quero falar sobre dinheiro"},
            {"role": "assistant", "content": "dinheiro pode ajudar em algumas condições"},
        ]},
        registry,
    )
    assert "dinheiro" in prompt.dynamic_text
    assert "SHOULD_NOT_AUTO_PROJECT" not in prompt.wire_text
    assert prompt.dynamic["memory_view"]["nodes"] == []


def test_rev371_explicit_memory_activation_still_materializes(tmp_path):
    registry = standard_registry()
    cfg = base_config()
    ctx = _ctx(tmp_path)
    scope = world_scope(ctx["core_memory"]["world_scope_id"])
    apply_graph_operations(ctx["core_memory"]["storage_dir"], [
        {"op": "create_node", "id": "mem-explicit", "scope": scope, "kind": "fact", "content": "EXPLICIT_BODY"},
    ])
    session = AgentSession("x")
    session.memory_view["node_ids"] = ["mem-explicit"]
    view = materialize_explicit_memory_view(session, registry=registry, config=cfg, provider_context=ctx)
    assert [node["id"] for node in view["nodes"]] == ["mem-explicit"]
    assert view["nodes"][0]["content"] == "EXPLICIT_BODY"


def test_rev371_memory_graph_size_does_not_change_trivial_prompt(tmp_path):
    registry = standard_registry()
    cfg = base_config()
    ctx = _ctx(tmp_path)
    session = AgentSession("oi")
    prompt_empty, _ = agent._compile_prompt(session, cfg, ctx, {"recent_messages": [], "total_messages": 0}, registry)
    scope = world_scope(ctx["core_memory"]["world_scope_id"])
    apply_graph_operations(ctx["core_memory"]["storage_dir"], [
        {"op": "create_node", "id": f"mem-{i:05d}", "scope": scope, "kind": "fact", "content": f"node {i}", "retention": "temporary"}
        for i in range(10000)
    ])
    prompt_large, _ = agent._compile_prompt(AgentSession("oi"), cfg, ctx, {"recent_messages": [], "total_messages": 0}, registry)
    assert len(prompt_large.wire_text) == len(prompt_empty.wire_text)
    assert "node 9999" not in prompt_large.wire_text


def test_rev371_protocol_repair_does_not_rematerialize_observation_bodies():
    cfg = base_config()
    observations = [{"detail": {"content": "x" * 10000}, "status": "success"}]
    assert materialize_latest_observations(observations, cfg, repair=True) == []
    assert materialize_latest_observations(observations, cfg, repair=False)


def test_rev371_static_cognitive_floor_is_composed_and_bounded(tmp_path):
    registry = standard_registry()
    cfg = base_config()
    floor = measure_static_cognitive_floor(cfg, registry, _ctx(tmp_path))
    assert floor["components"]["system"]["characters"] == len(PROMPT_ECC.rstrip())
    assert floor["characters"] == sum(v["characters"] for v in floor["components"].values())
    # Release gate: the static floor is intentionally measured as a composition.
    assert floor["estimated_tokens"] < 3500
