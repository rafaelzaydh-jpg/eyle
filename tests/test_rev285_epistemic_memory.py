from __future__ import annotations

import copy
from pathlib import Path

from eyle.core.memory import apply_memory_sidecar, memory_activate_result, memory_history_result, materialize_explicit_memory_view
from eyle.core.session import AgentSession
from eyle.runtime.config import validar_config
from eyle.runtime.memory_graph import graph_overview, node_record, world_scope
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


def test_rev285_provider_schema_exposes_open_epistemic_metadata_without_truth_enum():
    schema = schema_for_profile("navigation")
    memory = next(b for b in schema["oneOf"] if "memory_delta" in b["properties"])["properties"]["memory_delta"]["items"]["oneOf"]
    remember = memory[0]["properties"]["arguments"]["properties"]
    epistemic = remember["epistemic"]
    assert set(epistemic["properties"]) == {"nature", "confidence", "volatility", "temporal", "context"}
    assert epistemic["required"] == ["nature"]
    assert "enum" not in epistemic["properties"]["nature"]
    assert "enum" not in epistemic["properties"]["volatility"]
    assert "maxItems" not in next(b for b in schema["oneOf"] if "memory_delta" in b["properties"])["properties"]["memory_delta"]


def test_rev375_wire_parser_preserves_epistemic_classification_with_optional_metadata():
    parsed = parse_profile_response(
        {
            "type": "concluir", "response": "ok",
            "memory_delta": [
                {
                    "op": "remember",
                    "arguments": {
                        "scope": "user",
                        "retention": "persistent",
                        "kind": "user_preference",
                        "content": "User currently prefers short answers in casual chat.",
                        "epistemic": {
                            "nature": "preference",
                            "confidence": 0.72,
                            "volatility": "high",
                            "temporal": {"as_of": "current interaction"},
                            "context": {"setting": "casual_chat"},
                        },
                        "supports": [{"kind": "request"}],
                    },
                },
                {
                    "op": "remember",
                    "arguments": {
                        "scope": "world",
                        "retention": "temporary",
                        "kind": "plain_shape",
                        "content": "Current Memory without epistemic remains accepted.",
                    },
                },
            ],
        },
        "navigation",
    )
    first, second = parsed["memory_delta"]
    assert first["epistemic"]["nature"] == "preference"
    assert first["epistemic"]["confidence"] == 0.72
    assert first["epistemic"]["context"]["setting"] == "casual_chat"
    assert "epistemic" not in second


def test_rev285_memory_graph_stores_epistemic_state_independently_from_retention(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("remember")
    result = apply_memory_sidecar(
        session,
        [{
            "op": "remember", "key": "p", "scope": "user", "retention": "persistent",
            "kind": "preference", "content": "User dislikes X at this point in time.",
            "epistemic": {
                "nature": "preference", "confidence": 0.65, "volatility": "high",
                "temporal": {"as_of": "phase-a"}, "context": {"domain": "tooling"},
            },
            "supports": [{"kind": "request"}],
        }],
        registry=registry, provider_context=context,
    )
    assert result["ok"] is True
    node = node_record(context["core_memory"]["storage_dir"], result["aliases"]["p"])
    assert node["retention"] == "persistent"
    assert node["epistemic"]["nature"] == "preference"
    assert node["epistemic"]["volatility"] == "high"
    assert node["epistemic"]["confidence"] == 0.65
    assert node["epistemic"]["temporal"] == {"as_of": "phase-a"}
    assert node["epistemic"]["context"] == {"domain": "tooling"}
    assert node["epistemic"]["last_evidenced_at"] is not None


def test_rev285_temporal_change_preserves_old_state_and_relates_new_state(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("change")
    learned = apply_memory_sidecar(
        session,
        [
            {
                "op": "remember", "key": "old", "scope": "user", "retention": "persistent",
                "kind": "preference", "content": "User disliked Python in the earlier phase.",
                "epistemic": {"nature": "preference", "confidence": 0.9, "volatility": "high", "temporal": {"as_of": "earlier"}},
                "supports": [{"kind": "request"}],
            },
            {
                "op": "remember", "key": "new", "scope": "user", "retention": "persistent",
                "kind": "preference", "content": "User now likes Python.",
                "epistemic": {"nature": "preference", "confidence": 0.9, "volatility": "high", "temporal": {"as_of": "now"}},
                "supports": [{"kind": "request"}],
            },
            {"op": "relate", "source": "@new", "relation": "changed_from", "target": "@old"},
        ],
        registry=registry, provider_context=context,
    )
    assert learned["ok"] is True
    old_id, new_id = learned["aliases"]["old"], learned["aliases"]["new"]
    old = node_record(context["core_memory"]["storage_dir"], old_id)
    new = node_record(context["core_memory"]["storage_dir"], new_id)
    assert old["status"] == "current" and new["status"] == "current"
    assert any(e["label"] == "changed_from" and e["source"] == new_id and e["target"] == old_id for e in new["edges"])


def test_rev285_reassessment_is_visible_in_memory_history(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("history")
    first = apply_memory_sidecar(
        session,
        [{"op": "remember", "key": "h", "scope": "world", "retention": "persistent", "kind": "hypothesis", "content": "Boundary may be faulty.",
          "epistemic": {"nature": "hypothesis", "confidence": 0.25, "volatility": "medium"}}],
        registry=registry, provider_context=context,
    )
    node_id = first["aliases"]["h"]
    apply_memory_sidecar(
        session,
        [{"op": "revise", "id": node_id, "expected_revision": 1,
          "epistemic": {"nature": "hypothesis", "confidence": 0.8, "volatility": "medium", "context": {"after_tests": True}}}],
        registry=registry, provider_context=context,
    )
    history = memory_history_result(session, arguments={"id": node_id}, provider_context=context)
    assert history["ok"] is True
    assert history["detail"]["node"]["epistemic"]["confidence"] == 0.8
    assert [e["action"] for e in history["detail"]["events"]] == ["create_node", "update_node"]
    assert history["detail"]["events"][0]["payload"]["epistemic"]["confidence"] == 0.25
    assert history["detail"]["events"][1]["payload"]["epistemic"]["confidence"] == 0.8


def test_rev285_memory_activate_can_mechanically_filter_epistemic_region(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("seed")
    delta = []
    for i in range(8):
        delta.append({
            "op": "remember", "scope": "world", "retention": "persistent", "kind": "note", "content": f"node {i}",
            "epistemic": {"nature": "hypothesis" if i % 2 == 0 else "observation", "volatility": "high" if i < 4 else "low"},
        })
    assert apply_memory_sidecar(session, delta, registry=registry, provider_context=context)["ok"] is True
    recall = AgentSession("recall hypotheses")
    result = memory_activate_result(
        recall,
        arguments={"natures": ["hypothesis"], "volatilities": ["high"], "limit": 20},
        registry=registry, config=base_config(), provider_context=context,
    )
    assert result["ok"] is True
    nodes = materialize_explicit_memory_view(
        recall, registry=registry, config=base_config(), provider_context=context,
    )["nodes"]
    assert len(nodes) == 2
    assert all(n["epistemic"]["nature"] == "hypothesis" and n["epistemic"]["volatility"] == "high" for n in nodes)


def test_rev285_memory_content_has_no_old_12k_semantic_ceiling(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("large atomic meaning")
    content = "x" * 20000
    result = apply_memory_sidecar(
        session,
        [{"op": "remember", "key": "big", "scope": "world", "retention": "temporary", "kind": "large_note", "content": content,
          "epistemic": {"nature": "observation", "volatility": "unknown"}}],
        registry=registry, provider_context=context,
    )
    assert result["ok"] is True
    assert node_record(context["core_memory"]["storage_dir"], result["aliases"]["big"])["content"] == content


def test_rev285_overview_exposes_epistemic_directory(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("overview")
    apply_memory_sidecar(
        session,
        [
            {"op": "remember", "scope": "world", "retention": "temporary", "kind": "a", "content": "a", "epistemic": {"nature": "observation", "volatility": "low"}},
            {"op": "remember", "scope": "world", "retention": "persistent", "kind": "b", "content": "b", "epistemic": {"nature": "hypothesis", "confidence": 0.4, "volatility": "high"}},
        ], registry=registry, provider_context=context,
    )
    overview = graph_overview(context["core_memory"]["storage_dir"], world_scope_value=world_scope(context["core_memory"]["world_scope_id"]))
    assert {x["nature"] for x in overview["epistemic_natures"]} >= {"observation", "hypothesis"}
    assert {x["volatility"] for x in overview["volatility"]} >= {"low", "high"}
    assert overview["confidence"] == {"classified": 1, "unclassified": 1}


def test_rev285_relations_can_carry_epistemic_state(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("edge epistemic")
    learned = apply_memory_sidecar(
        session,
        [
            {"op": "remember", "key": "a", "scope": "world", "retention": "persistent", "kind": "observation", "content": "A happened", "epistemic": {"nature": "observation", "confidence": 0.99}},
            {"op": "remember", "key": "b", "scope": "world", "retention": "persistent", "kind": "hypothesis", "content": "B may follow", "epistemic": {"nature": "hypothesis", "confidence": 0.4}},
            {"op": "relate", "source": "@a", "relation": "may_cause", "target": "@b", "epistemic": {"nature": "causal_hypothesis", "confidence": 0.35, "volatility": "medium", "context": {"experiment": "first-pass"}}},
        ],
        registry=registry, provider_context=context,
    )
    assert learned["ok"] is True
    a = node_record(context["core_memory"]["storage_dir"], learned["aliases"]["a"])
    relation = next(e for e in a["edges"] if e["label"] == "may_cause")
    from eyle.runtime.memory_graph import edge_record
    full = edge_record(context["core_memory"]["storage_dir"], relation["id"])
    assert full["epistemic"]["nature"] == "causal_hypothesis"
    assert full["epistemic"]["confidence"] == 0.35
    assert full["epistemic"]["context"] == {"experiment": "first-pass"}
