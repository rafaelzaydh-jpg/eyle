from __future__ import annotations

import copy

import pytest

from eyle.runtime.config import validar_config
from llm.executar import PROMPT_ECC
from llm.structured import StructuredResponseError, parse_profile_response, schema_for_profile
from tests.canonical import base_config, standard_registry


def _conclude(memory_delta):
    return {"type": "concluir", "response": "ok", "memory_delta": memory_delta}


def test_rev284_provider_schema_describes_every_memory_operation_and_support_shape():
    schema = schema_for_profile("navigation")
    branch = next(b for b in schema["oneOf"] if "memory_delta" in b.get("properties", {}))
    item = branch["properties"]["memory_delta"]["items"]
    variants = item["oneOf"]
    assert [v["properties"]["op"]["enum"][0] for v in variants] == [
        "remember", "revise", "relate", "revise_relation", "task_status", "archive", "supersede", "retire_relation",
    ]
    remember_args = variants[0]["properties"]["arguments"]
    supports = remember_args["properties"]["supports"]
    assert supports["oneOf"][0]["type"] == "array"
    support_variants = supports["oneOf"][0]["items"]["oneOf"]
    assert [v["properties"]["kind"]["enum"][0] for v in support_variants[:3]] == [
        "request", "memory", "material",
    ]
    assert "memory_id" in support_variants[1]["properties"]
    assert "material_id" in support_variants[2]["properties"]
    assert "selector" in support_variants[2]["properties"]
    assert any(v.get("type") == "string" for v in support_variants)


def test_rev284_safe_support_wire_aliases_normalize_before_memory_graph():
    raw = _conclude([
        {
            "op": "remember",
            "arguments": {
                "scope": "world", "retention": "temporary", "kind": "fact", "content": "a",
                "supports": "request",
            },
        },
        {
            "op": "remember",
            "arguments": {
                "scope": "world", "retention": "temporary", "kind": "fact", "content": "b",
                "supports": "mat-0007",
            },
        },
        {
            "op": "remember",
            "arguments": {
                "scope": "world", "retention": "temporary", "kind": "fact", "content": "c",
                "supports": {"memory_id": "mem-abc"},
            },
        },
    ])
    parsed = parse_profile_response(raw, "navigation")
    assert parsed["memory_delta"][0]["supports"] == [{"kind": "request"}]
    assert parsed["memory_delta"][1]["supports"] == [{"kind": "material", "material_id": "mat-0007"}]
    assert parsed["memory_delta"][2]["supports"] == [{"kind": "memory", "memory_id": "mem-abc"}]


def test_rev284_flattened_memory_action_is_a_boundary_alias_not_internal_shape():
    parsed = parse_profile_response(
        _conclude([
            {
                "op": "remember",
                "scope": "user",
                "retention": "persistent",
                "kind": "preference",
                "content": "prefers concise output",
                "supports": [{"kind": "request"}],
            }
        ]),
        "navigation",
    )
    assert parsed["memory_delta"] == [
        {
            "op": "remember",
            "scope": "user",
            "retention": "persistent",
            "kind": "preference",
            "content": "prefers concise output",
            "supports": [{"kind": "request"}],
        }
    ]


def test_rev284_opaque_material_selector_and_memory_content_have_no_hidden_local_size_ceiling():
    deep = current = {}
    for i in range(12):
        current[f"level_{i}"] = {}
        current = current[f"level_{i}"]
    current["body"] = "x" * 9000
    content = "knowledge " + ("y" * 20000)
    parsed = parse_profile_response(
        _conclude([
            {
                "op": "remember",
                "arguments": {
                    "scope": "world", "retention": "temporary", "kind": "fact", "content": content,
                    "supports": {"kind": "material", "material_id": "mat-0012", "selector": deep},
                },
            }
        ]),
        "navigation",
    )
    assert parsed["memory_delta"][0]["content"] == content
    assert parsed["memory_delta"][0]["supports"][0]["selector"] == deep


def test_rev371_ambiguous_memory_support_rejects_sidecar_not_valid_ecc():
    parsed = parse_profile_response(
        _conclude([
            {
                "op": "remember",
                "arguments": {
                    "scope": "world", "retention": "temporary", "kind": "fact", "content": "x",
                    "supports": ["whatever-this-is"],
                },
            }
        ]),
        "navigation",
    )
    assert parsed["type"] == "concluir"
    assert parsed["response"] == "ok"
    assert parsed["memory_delta"] == []
    assert parsed["memory_error"]["code"] == "EYLE_MEMORY_INVALID"


def test_rev4_prompt_keeps_memory_semantic_sidecar_out_of_planner_role():
    lower = PROMPT_ECC.lower()
    assert "memory_delta stores reusable learning" in lower
    assert "memory is continuous learning, not a planner or hidden working set" in lower
    assert "task_binding" in lower
    assert "do not persist transient tool-next-step reasoning as task content" in lower

