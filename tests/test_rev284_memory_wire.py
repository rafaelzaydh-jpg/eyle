from __future__ import annotations

import copy

import pytest

from eyle.runtime.config import validar_config
from llm.executar import PROMPT_ECC
from llm.structured import StructuredResponseError, parse_profile_response, schema_for_profile
from tests.canonical import base_config, standard_registry


def _conclude(memory_delta):
    return {"decision": {"type": "concluir", "response": "ok"}, "memory_delta": memory_delta}


def test_rev284_provider_schema_describes_every_memory_operation_and_support_shape():
    schema = schema_for_profile("ecc")
    item = schema["properties"]["memory_delta"]["items"]
    variants = item["oneOf"]
    assert [v["properties"]["op"]["enum"][0] for v in variants] == [
        "remember", "revise", "relate", "revise_relation", "archive", "supersede", "retire_relation",
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
    parsed = parse_profile_response(raw, "ecc")
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
        "ecc",
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
        "ecc",
    )
    assert parsed["memory_delta"][0]["content"] == content
    assert parsed["memory_delta"][0]["supports"][0]["selector"] == deep


def test_rev284_ambiguous_support_still_fails_closed():
    with pytest.raises(StructuredResponseError) as exc:
        parse_profile_response(
            _conclude([
                {
                    "op": "remember",
                    "arguments": {
                        "scope": "world", "retention": "temporary", "kind": "fact", "content": "x",
                        "supports": ["whatever-this-is"],
                    },
                }
            ]),
            "ecc",
        )
    assert exc.value.code == "EYLE_MEMORY_INVALID"


def test_rev286_prompt_teaches_simple_wire_and_delegates_canonicalization():
    lower = PROMPT_ECC.lower()
    assert 'keep memory_delta simple' in lower
    assert 'preferred remember wire form is flat' in lower
    assert 'eyle deterministically wraps arguments and epistemic metadata' in lower
    assert 'simplest unambiguous wire support' in lower
    assert '"request", "mat-0001", "mem-..." or @key' in lower
    assert 'supports is always a json array' not in lower


def test_rev284_accepts_clean_rev283_config_during_in_place_upgrade():
    config = base_config()
    config["config_schema_version"] = "2.7.5-r2.8.3-ecc"
    config["revision"] = "rev2.8.3-ecc"
    validated = validar_config(copy.deepcopy(config), standard_registry())
    assert validated["config_schema_version"] == "2.7.5-r3-ecc"
    assert validated["revision"] == "rev3-ecc"
