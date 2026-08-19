from __future__ import annotations

import json
from pathlib import Path

from llm.executar import PROMPT_ECC
from llm.structured import json_schema_response_format
from server import server
from tests.canonical import base_config

ROOT = Path(__file__).resolve().parents[1]


def _payload():
    return {
        "model": "auto",
        "messages": [
            {"role": "system", "content": "EYLE SEMANTICS ONLY"},
            {"role": "user", "content": '{"ecc_operations":{"example":true}}'},
            {"role": "user", "content": "Olá"},
        ],
        "response_format": json_schema_response_format("navigation"),
        "reasoning_mode": "off",
        "stream": False,
    }


def test_rev3751_exact_caller_schema_is_delivered_once_without_eyle_semantic_special_cases():
    payload = _payload()
    body, _, schema = server.prepare_upstream(payload)
    assert schema == payload["response_format"]["json_schema"]["schema"]
    text = "\n".join(str(message.get("content")) for message in body["messages"])
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert text.count(encoded) == 1
    assert text.count("JSON_SCHEMA=") == 1

    source = Path(server.__file__).read_text(encoding="utf-8")
    schema_instruction = source[source.index("def _schema_instruction"):source.index("def _attach_schema_instruction")]
    for semantic_token in ('"explorar"', '"construir"', '"concluir"', "memory_delta"):
        assert semantic_token not in schema_instruction


def test_rev3751_core_has_no_duplicate_provider_wire_instruction():
    executar = (ROOT / "llm" / "executar.py").read_text(encoding="utf-8")
    structured = (ROOT / "llm" / "structured.py").read_text(encoding="utf-8")
    agent = (ROOT / "eyle" / "core" / "agent.py").read_text(encoding="utf-8")
    assert "contract_instruction(perfil)" not in executar
    assert "def contract_instruction(" not in structured
    assert "contract_instruction" not in agent
    assert "Runtime canonicalizes wire details" not in PROMPT_ECC
    assert "Protocol repair fixes serialization" not in PROMPT_ECC


def test_rev3751_repair_messages_are_schema_candidate_and_error_only():
    schema = _payload()["response_format"]["json_schema"]["schema"]
    messages = server._repair_messages(schema, '{"type":"bad"}', ["$.type: invalid"])
    assert [message["role"] for message in messages] == ["system", "assistant", "user"]
    joined = "\n".join(message["content"] for message in messages)
    assert "JSON_SCHEMA=" in joined
    assert '{"type":"bad"}' in joined
    assert "$.type: invalid" in joined
    assert "EYLE SEMANTICS ONLY" not in joined
    assert "ecc_operations" not in joined


def test_rev3751_repair_candidate_compaction_preserves_json_value():
    raw = '```json\n{\n  "b": 2,\n  "a": 1\n}\n```'
    data = {
        "choices": [{"message": {"content": raw}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    value, errors, _ = server.normalize_structured(
        data,
        {"type": "object", "properties": {"required": {"type": "string"}}, "required": ["required"]},
    )
    assert errors
    compact = server._repair_candidate_text(value, raw)
    assert json.loads(compact) == {"a": 1, "b": 2}
    assert "\n" not in compact
    assert "```" not in compact


def test_rev3751_truncation_is_not_format_repair_source_contract():
    source = Path(server.__file__).read_text(encoding="utf-8")
    assert '_finish_reason(first.data) == "length"' in source
    assert '"adapter_output_truncated"' in source


def test_rev3751_does_not_add_a_new_token_ceiling():
    cfg = base_config()
    llm = cfg.get("llm") or {}
    for forbidden in (
        "max_completion_tokens_per_call",
        "generated_token_fuse",
        "output_token_ceiling",
        "repair_token_ceiling",
    ):
        assert forbidden not in llm
