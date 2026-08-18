from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from eyle.core.memory import apply_memory_sidecar, memory_activate_result, memory_overview_result
from eyle.core.session import AgentSession
from eyle.runtime.config import validar_config
from eyle.runtime.memory_graph import (
    MEMORY_GRAPH_SCHEMA_VERSION,
    create_recall_snapshot,
    graph_counts,
    graph_overview,
    memory_db_path,
    node_record,
    recall_snapshot_page,
    world_scope,
)
from llm.executar import PROMPT_ECC
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


def test_rev290_wire_supports_main_authored_associative_recall_without_hidden_semantics():
    parsed = parse_profile_response(
        {
            "type": "concluir",
            "response": "ok",
            "memory_delta": [{
                "op": "remember",
                "scope": "user",
                "retention": "persistent",
                "kind": "preference",
                "content": "User likes The Beatles.",
                "nature": "preference",
                "aliases": ["Fab Four", "Beatles"],
                "concepts": ["music taste", "bands"],
                "cues": ["favorite music", "what bands do they like"],
                "support": "request",
            }],
        },
        "ecc",
    )
    memory = parsed["memory_delta"][0]
    assert memory["recall"] == {
        "aliases": ["Fab Four", "Beatles"],
        "concepts": ["music taste", "bands"],
        "cues": ["favorite music", "what bands do they like"],
    }
    assert memory["supports"] == [{"kind": "request"}]
    remember = schema_for_profile("ecc")["properties"]["memory_delta"]["items"]["oneOf"][0]
    assert "recall" in remember["properties"]["arguments"]["properties"]


def test_rev290_associative_recall_is_stored_and_revised_independently_of_epistemology(tmp_path):
    registry = standard_registry(); context = _context(tmp_path); session = AgentSession("seed")
    learned = apply_memory_sidecar(
        session,
        [{
            "op": "remember", "key": "music", "scope": "user", "retention": "persistent",
            "kind": "preference", "content": "User likes The Beatles.",
            "epistemic": {"nature": "preference", "confidence": 0.8, "volatility": "high"},
            "recall": {"aliases": ["Fab Four"], "concepts": ["music taste"], "cues": ["favorite band"]},
            "supports": [{"kind": "request"}],
        }], registry=registry, provider_context=context,
    )
    node_id = learned["aliases"]["music"]
    node = node_record(context["core_memory"]["storage_dir"], node_id)
    assert node["recall"]["aliases"] == ["Fab Four"]
    assert node["epistemic"]["confidence"] == 0.8

    revised = apply_memory_sidecar(
        session,
        [{
            "op": "revise", "id": node_id, "expected_revision": 1,
            "add_recall": {"aliases": ["Beatles"], "cues": ["classic British bands"]},
            "remove_recall": {"cues": ["favorite band"]},
        }], registry=registry, provider_context=context,
    )
    assert revised["ok"] is True
    node = node_record(context["core_memory"]["storage_dir"], node_id)
    assert node["recall"] == {
        "aliases": ["Fab Four", "Beatles"],
        "concepts": ["music taste"],
        "cues": ["classic British bands"],
    }
    assert node["epistemic"]["confidence"] == 0.8


def test_rev290_recall_finds_alias_concept_and_cue_without_embedding_ranker(tmp_path):
    registry = standard_registry(); context = _context(tmp_path); session = AgentSession("seed")
    learned = apply_memory_sidecar(
        session,
        [{
            "op": "remember", "key": "m", "scope": "user", "retention": "persistent",
            "kind": "preference", "content": "User enjoys a particular 1960s band.",
            "epistemic": {"nature": "preference", "volatility": "medium"},
            "recall": {
                "aliases": ["Beatles", "Fab Four"],
                "concepts": ["music taste"],
                "cues": ["which classic bands does the user enjoy"],
            },
        }], registry=registry, provider_context=context,
    )
    node_id = learned["aliases"]["m"]
    for query in ("Fab Four", "music taste", "classic bands"):
        recall = AgentSession(query)
        result = memory_activate_result(
            recall, arguments={"query": query, "limit": 10}, registry=registry,
            config=base_config(), provider_context=context,
        )
        assert result["ok"] is True
        projected = {n["id"]: n for n in result["detail"]["memory_view"]["nodes"]}
        assert node_id in projected
        assert projected[node_id]["recall"]["aliases"] == ["Beatles", "Fab Four"]


def test_rev290_multi_query_variants_union_main_authored_search_paths(tmp_path):
    registry = standard_registry(); context = _context(tmp_path); session = AgentSession("seed")
    learned = apply_memory_sidecar(
        session,
        [
            {"op": "remember", "key": "a", "scope": "user", "retention": "persistent", "kind": "preference", "content": "User likes Beatles", "epistemic": {"nature": "preference"}},
            {"op": "remember", "key": "b", "scope": "user", "retention": "persistent", "kind": "preference", "content": "User likes Michael Jackson", "epistemic": {"nature": "preference"}},
        ], registry=registry, provider_context=context,
    )
    recall = AgentSession("music")
    result = memory_activate_result(
        recall,
        arguments={"queries": ["Beatles", "Michael Jackson"], "limit": 10},
        registry=registry, config=base_config(), provider_context=context,
    )
    ids = {n["id"] for n in result["detail"]["memory_view"]["nodes"]}
    assert {learned["aliases"]["a"], learned["aliases"]["b"]}.issubset(ids)
    assert result["detail"]["matched_nodes"] == 2


def test_rev290_relation_label_filter_is_mechanical_consolidation_navigation(tmp_path):
    registry = standard_registry(); context = _context(tmp_path); session = AgentSession("seed")
    learned = apply_memory_sidecar(
        session,
        [
            {"op": "remember", "key": "obs", "scope": "world", "retention": "persistent", "kind": "observation", "content": "A reset occurred", "epistemic": {"nature": "observation"}},
            {"op": "remember", "key": "hyp", "scope": "world", "retention": "persistent", "kind": "hypothesis", "content": "Boundary may be unstable", "epistemic": {"nature": "hypothesis", "confidence": 0.4}},
            {"op": "remember", "key": "other", "scope": "world", "retention": "persistent", "kind": "note", "content": "Unrelated", "epistemic": {"nature": "observation"}},
            {"op": "relate", "source": "@obs", "relation": "supports", "target": "@hyp", "epistemic": {"nature": "support_relation"}},
        ], registry=registry, provider_context=context,
    )
    recall = AgentSession("relations")
    result = memory_activate_result(
        recall, arguments={"relation_labels": ["supports"], "limit": 10}, registry=registry,
        config=base_config(), provider_context=context,
    )
    ids = {n["id"] for n in result["detail"]["memory_view"]["nodes"]}
    assert ids == {learned["aliases"]["obs"], learned["aliases"]["hyp"]}


def test_rev290_overview_exposes_factual_consolidation_map_without_semantic_ranking(tmp_path):
    registry = standard_registry(); context = _context(tmp_path); session = AgentSession("seed")
    learned = apply_memory_sidecar(
        session,
        [
            {"op": "remember", "key": "a", "scope": "world", "retention": "persistent", "kind": "observation", "content": "A", "epistemic": {"nature": "observation"}, "recall": {"concepts": ["alpha"]}},
            {"op": "remember", "key": "b", "scope": "world", "retention": "persistent", "kind": "hypothesis", "content": "B", "epistemic": {"nature": "hypothesis"}},
            {"op": "relate", "source": "@a", "relation": "supports", "target": "@b"},
            {"op": "remember", "key": "c", "scope": "world", "retention": "temporary", "kind": "note", "content": "C", "epistemic": {"nature": "observation"}},
        ], registry=registry, provider_context=context,
    )
    apply_memory_sidecar(
        session,
        [{"op": "revise", "id": learned["aliases"]["b"], "expected_revision": 1, "epistemic": {"nature": "hypothesis", "confidence": 0.7}}],
        registry=registry, provider_context=context,
    )
    overview = memory_overview_result(AgentSession("overview"), arguments={}, provider_context=context)
    detail = overview["detail"]
    assert detail["consolidation"]["isolated_nodes"] == 1
    assert detail["consolidation"]["revised_nodes"] == 1
    assert detail["consolidation"]["associatively_described_nodes"] == 1
    assert {x["relation"] for x in detail["relation_labels"]} == {"supports"}
    assert "semantic_score" not in json.dumps(detail)


def test_rev290_prompt_makes_association_main_authored_and_not_evidence():
    lowered = PROMPT_ECC.lower()
    assert "associative recall cues" in lowered
    assert "retrieval hints only" in lowered
    assert "not evidence" in lowered
    assert "never invents/ranks semantic associations" in lowered


def test_rev290_sql_fallback_searches_main_authored_associative_recall(tmp_path):
    registry = standard_registry(); context = _context(tmp_path); session = AgentSession("fallback-seed")
    learned = apply_memory_sidecar(
        session,
        [{"op": "remember", "key": "x", "scope": "world", "retention": "persistent", "kind": "note", "content": "opaque body", "recall": {"aliases": ["unique-fallback-alias"]}}],
        registry=registry, provider_context=context,
    )
    db = memory_db_path(context["core_memory"]["storage_dir"])
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT OR REPLACE INTO memory_meta(key,value) VALUES('fts5_available','0')")
        conn.commit()
    finally:
        conn.close()
    result = memory_activate_result(
        AgentSession("fallback-query"), arguments={"query": "unique-fallback-alias", "limit": 10},
        registry=registry, config=base_config(), provider_context=context,
    )
    ids = {n["id"] for n in result["detail"]["memory_view"]["nodes"]}
    assert learned["aliases"]["x"] in ids
    assert result["detail"]["search_backend"] == "sql_like"


def test_rev290_memory_tags_have_no_small_semantic_count_or_length_ceiling(tmp_path):
    long_tag = "semantic-tag-" + ("x" * 600)
    tags = [f"tag-{i}" for i in range(75)] + [long_tag]
    parsed = parse_profile_response({
        "type": "concluir", "response": "ok",
        "memory_delta": [{"op": "remember", "scope": "user", "retention": "persistent", "kind": "note", "content": "tag stress", "tags": tags}],
    }, "ecc")
    assert parsed["memory_delta"][0]["tags"][-1] == long_tag
    registry = standard_registry(); context = _context(tmp_path); session = AgentSession("tag-seed")
    learned = apply_memory_sidecar(session, parsed["memory_delta"], registry=registry, provider_context=context)
    stored = node_record(context["core_memory"]["storage_dir"], learned["affected"][0]["id"])
    assert len(stored["tags"]) == 76
    assert long_tag in stored["tags"]
