from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

from eyle.core.memory import (
    apply_memory_sidecar,
    memory_activate_result,
    memory_continue_result,
    memory_relation_history_result,
)
from eyle.core.session import AgentSession
from eyle.runtime.memory_graph import (
    MEMORY_GRAPH_SCHEMA_VERSION,
    edge_record,
    graph_counts,
    memory_db_path,
    memory_search_backend,
    node_record,
)
from llm.structured import parse_profile_response, schema_for_profile
from tests.canonical import base_config, standard_registry


def _context(root: Path) -> dict:
    return {
        "standard": {"caminho_origem": str(root), "eyle_root": str(root)},
        "core_memory": {
            "storage_dir": str(root.parent / (root.name + "_memory")),
            "world_scope_id": f"workspace:{root.resolve()}",
        },
    }


def _seed(session, context, count: int, *, prefix: str = "alpha"):
    registry = standard_registry()
    delta = [
        {
            "op": "remember",
            "scope": "world",
            "retention": "persistent",
            "kind": "observation",
            "content": f"{prefix} scalable memory item {i}",
            "epistemic": {"nature": "observation", "confidence": 0.8, "volatility": "low"},
            "tags": ["scale", prefix],
        }
        for i in range(count)
    ]
    result = apply_memory_sidecar(session, delta, registry=registry, provider_context=context)
    assert result["ok"] is True
    return result


def test_rev287_memory_graph_v7_adds_scalable_recall_tables_and_fts(tmp_path):
    storage = str(tmp_path / "memory")
    graph_counts(storage)
    assert MEMORY_GRAPH_SCHEMA_VERSION == "2.7.5-r3.7.1-memory-graph-v12"
    conn = sqlite3.connect(memory_db_path(storage))
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"memory_recall_snapshots", "memory_recall_items"}.issubset(tables)
        assert memory_search_backend(storage) in {"fts5", "sql_like"}
        if memory_search_backend(storage) == "fts5":
            assert conn.execute("SELECT name FROM sqlite_master WHERE name='memory_fts'").fetchone()
    finally:
        conn.close()


def test_rev287_recall_frontier_is_db_cursor_not_full_id_snapshot(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    _seed(AgentSession("seed"), context, 120)
    session = AgentSession("recall alpha")
    first = memory_activate_result(
        session,
        arguments={"query": "alpha", "limit": 7},
        registry=registry,
        config=base_config(),
        provider_context=context,
    )
    assert first["ok"] is True
    assert first["detail"]["matched_nodes"] == 120
    assert len(first["detail"]["memory_view"]["nodes"]) == 7
    assert first["coverage"]["facts"]["db_cursor"] is True
    assert first["frontiers"][0]["count"] == 113
    snapshots = session.observation_ledger["snapshots"]
    assert len(snapshots) == 1
    payload = next(iter(snapshots.values()))["payload"]
    cursor = payload["memory_cursor"]
    assert set(cursor) == {"snapshot_id", "after_ordinal", "page_size", "selector", "selection"}
    assert "items" not in payload
    assert "node_ids" not in str(payload)


def test_rev287_recall_snapshot_is_exact_when_new_matching_memory_appears(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    _seed(AgentSession("seed"), context, 40)
    session = AgentSession("recall")
    first = memory_activate_result(
        session, arguments={"query": "alpha", "limit": 10},
        registry=registry, config=base_config(), provider_context=context,
    )
    assert first["detail"]["selected_nodes"] == 40
    frontier = first["frontiers"][0]["id"]

    # New nodes are real Memory, but they are beyond this already-defined Frontier.
    _seed(AgentSession("new facts"), context, 10)
    second = memory_continue_result(
        session, frontier_id=frontier, registry=registry, config=base_config(), provider_context=context,
    )
    assert second["ok"] is True
    assert second["coverage"]["examined"]["matches"] == 40
    assert second["coverage"]["facts"]["materialized_nodes"] == 20
    assert second["coverage"]["facts"]["remaining_nodes"] == 20


def test_rev287_fts_index_tracks_revision_without_semantic_rewrite(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("seed")
    made = apply_memory_sidecar(
        session,
        [{
            "op": "remember", "key": "x", "scope": "world", "retention": "persistent",
            "kind": "observation", "content": "orion unique old phrase",
            "epistemic": {"nature": "observation", "volatility": "low"},
        }],
        registry=registry, provider_context=context,
    )
    node_id = made["aliases"]["x"]
    recall = AgentSession("recall")
    old = memory_activate_result(recall, arguments={"query": "orion", "limit": 10}, registry=registry, config=base_config(), provider_context=context)
    assert old["detail"]["matched_nodes"] == 1
    revised = apply_memory_sidecar(
        session,
        [{"op": "revise", "id": node_id, "expected_revision": 1, "content": "vega unique new phrase"}],
        registry=registry, provider_context=context,
    )
    assert revised["ok"] is True
    old_after = memory_activate_result(AgentSession("old"), arguments={"query": "orion", "limit": 10}, registry=registry, config=base_config(), provider_context=context)
    new_after = memory_activate_result(AgentSession("new"), arguments={"query": "vega", "limit": 10}, registry=registry, config=base_config(), provider_context=context)
    assert old_after["detail"]["matched_nodes"] == 0
    assert new_after["detail"]["matched_nodes"] == 1


def test_rev287_relation_can_be_epistemically_revised_and_history_preserved(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("relation")
    made = apply_memory_sidecar(
        session,
        [
            {"op": "remember", "key": "a", "scope": "world", "retention": "persistent", "kind": "observation", "content": "A"},
            {"op": "remember", "key": "b", "scope": "world", "retention": "persistent", "kind": "hypothesis", "content": "B"},
            {"op": "relate", "source": "@a", "relation": "supports", "target": "@b", "epistemic": {"nature": "support_hypothesis", "confidence": 0.25, "volatility": "high"}, "supports": [{"kind": "request"}]},
        ],
        registry=registry, provider_context=context,
    )
    relation_id = node_record(context["core_memory"]["storage_dir"], made["aliases"]["a"])["edges"][0]["id"]
    changed = apply_memory_sidecar(
        session,
        [{
            "op": "revise_relation", "id": relation_id, "expected_revision": 1,
            "epistemic": {"nature": "support_hypothesis", "confidence": 0.85, "volatility": "medium", "context": {"after_tests": True}},
        }],
        registry=registry, provider_context=context,
    )
    assert changed["ok"] is True
    relation = edge_record(context["core_memory"]["storage_dir"], relation_id)
    assert relation["revision"] == 2
    assert relation["epistemic"]["confidence"] == 0.85
    assert relation["last_evidenced_at"] is not None
    history = memory_relation_history_result(session, arguments={"id": relation_id}, provider_context=context)
    assert history["ok"] is True
    assert [event["action"] for event in history["detail"]["events"]] == ["create_edge", "update_edge"]
    assert history["detail"]["events"][0]["payload"]["epistemic"]["confidence"] == 0.25
    assert history["detail"]["events"][1]["payload"]["epistemic"]["confidence"] == 0.85


def test_rev287_wire_supports_revise_relation_without_closed_epistemic_ontology():
    schema = schema_for_profile("ecc")
    ops = {
        item["properties"]["op"]["enum"][0]
        for item in schema["properties"]["memory_delta"]["items"]["oneOf"]
    }
    assert "revise_relation" in ops
    parsed = parse_profile_response(
        {
            "type": "concluir", "response": "ok",
            "memory_delta": [{
                "op": "update_edge", "id": "rel-demo", "expected_revision": 2,
                "nature": "causal_guess", "confidence": 0.4, "volatility": "very_contextual",
            }],
        },
        "ecc",
    )
    item = parsed["memory_delta"][0]
    assert item["op"] == "revise_relation"
    assert item["epistemic"]["nature"] == "causal_guess"
    assert item["epistemic"]["volatility"] == "very_contextual"


def test_rev287_sql_like_fallback_searches_tags_too(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("seed fallback")
    made = apply_memory_sidecar(
        session,
        [{
            "op": "remember", "scope": "world", "retention": "persistent",
            "kind": "observation", "content": "content without the lookup token",
            "tags": ["rare-tag-token"],
        }],
        registry=registry, provider_context=context,
    )
    assert made["ok"] is True
    storage = context["core_memory"]["storage_dir"]
    conn = sqlite3.connect(memory_db_path(storage))
    try:
        conn.execute("INSERT OR REPLACE INTO memory_meta(key,value) VALUES('fts5_available','0')")
        conn.commit()
    finally:
        conn.close()
    recalled = memory_activate_result(
        AgentSession("fallback recall"), arguments={"query": "rare-tag-token", "limit": 10},
        registry=registry, config=base_config(), provider_context=context,
    )
    assert recalled["ok"] is True
    assert recalled["detail"]["matched_nodes"] == 1
    assert recalled["coverage"]["facts"]["search_backend"] == "sql_like"


