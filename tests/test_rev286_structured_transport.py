from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import eyle.core.agent as agent
from llm.executar import ErroLLM, PROMPT_ECC
from llm.structured import (
    StructuredResponseError,
    canonicalize_wire_response,
    contract_instruction,
    json_schema_response_format,
    parse_profile_response,
    schema_for_profile,
    wire_schema_for_profile,
)
from eyle.runtime.config import validar_config
from tests.canonical import base_config, run_agent, standard_registry


def provider_context(root: Path):
    return {
        "standard": {"caminho_origem": str(root), "eyle_root": str(root)},
        "core_memory": {"storage_dir": str(root / ".memory-test"), "world_scope_id": f"workspace:{root.resolve()}"},
    }


def test_rev286_wire_and_canonical_contracts_are_separate():
    canonical = schema_for_profile("ecc")
    wire = wire_schema_for_profile("ecc")
    assert canonical["additionalProperties"] is False
    assert canonical["required"] == ["decision", "memory_delta"]
    assert "oneOf" in canonical["properties"]["decision"]
    assert wire["type"] == "object"
    assert wire["additionalProperties"] is True
    assert "oneOf" not in wire["properties"]["decision"]
    fmt = json_schema_response_format("ecc")
    assert fmt["json_schema"]["schema"] == wire
    assert fmt["json_schema"]["strict"] is False


def test_rev286_main_is_told_to_emit_basic_wire_not_internal_perfection():
    instruction = contract_instruction("ecc").lower()
    assert "do the basic semantic work" in instruction
    assert "eyle will canonicalize" in instruction
    assert "preferred wire form is flat" in instruction
    assert "do not spend cognition trying to serialize eyle internals perfectly" in instruction
    assert "on_success" not in instruction
    assert "basic semantic work" in (PROMPT_ECC + " " + contract_instruction("ecc")).lower()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"type":"concluir","response":"ok"}', {"type":"concluir","response":"ok","memory_delta":[]}),
        ('```json\n{"type":"concluir","answer":"ok"}\n```', {"type":"concluir","response":"ok","memory_delta":[]}),
        ('prose before {"type":"concluir","response":"ok"} prose after', {"type":"concluir","response":"ok","memory_delta":[]}),
        ("{'type':'concluir','response':'ok'}", {"type":"concluir","response":"ok","memory_delta":[]}),
        ({"output":{"type":"concluir","response":"ok"}}, {"type":"concluir","response":"ok","memory_delta":[]}),
    ],
)
def test_rev286_deterministic_wire_recovery(raw, expected):
    assert parse_profile_response(raw, "ecc") == expected


def test_rev286_flat_memory_and_support_aliases_become_canonical_without_llm_repair():
    raw = {
        "type": "concluir",
        "response": "ok",
        "memory_delta": [{
            "op": "remember",
            "scope": "user",
            "retention": "permanent",
            "kind": "preference",
            "content": "Likes a thing",
            "support": "request",
            "nature": "observation",
            "confidence": 0.7,
        }],
    }
    canonical = canonicalize_wire_response(raw)
    item = canonical["memory_delta"][0]
    assert item["op"] == "remember"
    assert item["arguments"]["retention"] == "persistent"
    assert item["arguments"]["supports"] == "request"
    assert item["arguments"]["epistemic"] == {"nature": "observation", "confidence": 0.7}
    parsed = parse_profile_response(raw, "ecc")
    learned = parsed["memory_delta"][0]
    assert learned["supports"] == [{"kind": "request"}]
    assert learned["epistemic"]["nature"] == "observation"


def test_rev286_retired_on_success_is_dropped_at_wire_boundary():
    raw = {"decision": {"type": "construir", "operation": "transaction", "arguments": {}, "on_success": "done"}}
    canonical = canonicalize_wire_response(raw)
    assert canonical == {"decision": {"type": "construir", "operation": "transaction", "arguments": {}}, "memory_delta": []}
    parsed = parse_profile_response(raw, "ecc")
    assert parsed == {"type": "construir", "operation": "transaction", "arguments": {}, "memory_delta": []}


def test_rev286_canonicalizer_never_invents_missing_semantics():
    with pytest.raises(StructuredResponseError) as exc:
        parse_profile_response({"type": "concluir"}, "ecc")
    assert exc.value.code == "ECC_RESPONSE_INVALID"
    with pytest.raises(StructuredResponseError):
        parse_profile_response({"type": "explorar", "operations": []}, "ecc")


def test_rev37_same_structured_fingerprint_is_bounded_to_two_repairs(monkeypatch, tmp_path):
    sequence = iter([
        ErroLLM("bad1", transient=False, error_code="STRUCTURED_RESPONSE_INVALID:ecc:ECC_RESPONSE_INVALID"),
        ErroLLM("bad2", transient=False, error_code="STRUCTURED_RESPONSE_INVALID:ecc:ECC_RESPONSE_INVALID"),
        ErroLLM("bad3", transient=False, error_code="STRUCTURED_RESPONSE_INVALID:ecc:ECC_RESPONSE_INVALID"),
        {"type": "concluir", "response": "must-not-run", "memory_delta": []},
    ])

    calls = {"n": 0}

    def fake(prompt, cfg):
        calls["n"] += 1
        item = next(sequence)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, pending, details = run_agent(
        agent, "answer", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert status == "failed"
    assert pending is None
    assert details["failure_code"] == "ECC_PROTOCOL_UNRECOVERABLE"
    assert details["physical_capability_calls"] == 0
    assert calls["n"] == 3


def test_rev286_old_adapter_failed_closed_can_be_cognitively_recovered(monkeypatch, tmp_path):
    sequence = iter([
        ErroLLM("old adapter failed closed", transient=False, error_code="LLM_STRUCTURED_RESPONSE_UNSATISFIED"),
        {"type": "concluir", "response": "survived", "memory_delta": []},
    ])

    def fake(prompt, cfg):
        item = next(sequence)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, pending, details = run_agent(
        agent, "answer", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text, pending) == ("completed", "survived", None)
    assert details["physical_capability_calls"] == 0


def test_rev37_protocol_bound_is_episode_specific_not_cognitive_ceiling():
    source = Path(agent.__file__).read_text(encoding="utf-8")
    assert "protocol_failures" in source
    assert "ECC_PROTOCOL_UNRECOVERABLE" in source
    assert "repeat_count > 2" in source
    assert "while True" in source


def test_rev286_structured_empty_is_protocol_error_not_generic_transport_retry(monkeypatch):
    import llm.executar as llm_mod
    from eyle.runtime.execution_context import ExecutionContext

    # This Rev2.8.6 regression test targets empty structured content only.
    # Rev2.8.8 handshake behavior is covered independently.
    monkeypatch.setattr(llm_mod, "_ensure_adapter_handshake", lambda config: {"ok": True})

    class Response:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def close(self): pass
        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
                "model": "fake",
            }).encode()

    calls = {"n": 0}
    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        return Response()

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    cfg = base_config()
    cfg["llm"].update({"base_url": "http://127.0.0.1:8080", "model": "auto", "retry_max_attempts": 3})
    execution = ExecutionContext.from_config(cfg)
    with pytest.raises(ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", cfg, execution=execution, perfil="ecc")
    assert exc.value.error_code == "STRUCTURED_RESPONSE_INVALID:ecc:STRUCTURED_EMPTY"
    assert calls["n"] == 1


def test_rev37_valid_ecc_survives_invalid_memory_parser_sidecar():
    parsed = parse_profile_response({
        "type": "concluir",
        "response": "delivered",
        "memory_delta": [{"op": "remember", "scope": "not-a-scope", "content": ""}],
    }, "ecc")
    assert parsed["type"] == "concluir"
    assert parsed["response"] == "delivered"
    assert parsed["memory_delta"] == []
    assert parsed["memory_error"]["code"].startswith("EYLE_MEMORY_")


def test_rev37_memory_parser_rejection_does_not_trigger_new_llm_call(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake(prompt, cfg):
        calls["n"] += 1
        return {
            "type": "concluir",
            "response": "delivered",
            "memory_delta": [],
            "memory_error": {"code": "EYLE_MEMORY_INVALID", "detail": "bad sidecar"},
        }

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, pending, details = run_agent(
        agent, "answer", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text, pending) == ("completed", "delivered", None)
    assert calls["n"] == 1
    assert details["memory_rejection_events"] == 1
    assert "EYLE_MEMORY_INVALID" in details["memory_rejection_reasons"]


def test_rev37_memory_graph_rejection_does_not_trigger_new_llm_call(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_llm(prompt, cfg):
        calls["n"] += 1
        return {"type": "concluir", "response": "delivered", "memory_delta": [{"op": "archive", "id": "mem-missing"}]}

    def reject_sidecar(*args, **kwargs):
        return {"ok": False, "changed": False, "task_state_changed": False, "error_code": "MEMORY_NODE_NOT_FOUND", "detail": "missing"}

    monkeypatch.setattr(agent, "executar_ecc_llm", fake_llm)
    monkeypatch.setattr(agent, "apply_memory_sidecar", reject_sidecar)
    status, text, pending, details = run_agent(
        agent, "answer", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text, pending) == ("completed", "delivered", None)
    assert calls["n"] == 1
    assert details["memory_rejection_events"] == 1
    assert "MEMORY_NODE_NOT_FOUND" in details["memory_rejection_reasons"]
