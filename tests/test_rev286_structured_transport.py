from __future__ import annotations

import json
from pathlib import Path

import pytest

import eyle.core.agent as agent
from llm.executar import ErroLLM
from llm.structured import (
    StructuredResponseError,
    json_schema_response_format,
    parse_profile_response,
    schema_for_profile,
    wire_schema_for_profile,
)
from tests.canonical import base_config, run_agent


def provider_context(root: Path):
    return {
        "standard": {"caminho_origem": str(root), "eyle_root": str(root)},
        "core_memory": {"storage_dir": str(root / ".memory-test"), "world_scope_id": f"workspace:{root.resolve()}"},
    }


def test_rev375_wire_and_canonical_contracts_are_separate():
    canonical = schema_for_profile("ecc")
    wire = wire_schema_for_profile("ecc")
    assert canonical["additionalProperties"] is False
    assert canonical["required"] == ["decision", "memory_delta"]
    assert "oneOf" in canonical["properties"]["decision"]
    assert "oneOf" in wire
    assert all(branch.get("additionalProperties") is False for branch in wire["oneOf"])
    fmt = json_schema_response_format("ecc")
    assert fmt["json_schema"]["schema"] == wire
    assert fmt["json_schema"]["strict"] is True


def test_rev3751_current_wire_schema_is_the_single_representation_contract():
    wire = wire_schema_for_profile("ecc")
    kinds = []
    for branch in wire["oneOf"]:
        kinds.append(branch["properties"]["type"]["enum"][0])
        assert "memory_delta" in branch["required"]
    assert kinds == ["explorar", "construir", "concluir"]


def test_rev375_core_accepts_current_json_only():
    assert parse_profile_response(
        '{"type":"concluir","response":"ok","memory_delta":[]}', "ecc"
    ) == {"type":"concluir","response":"ok","memory_delta":[]}

    for raw in (
        '```json\n{"type":"concluir","response":"ok","memory_delta":[]}\n```',
        'prose {"type":"concluir","response":"ok","memory_delta":[]}',
        "{'type':'concluir','response':'ok','memory_delta':[]}",
        {"decision": {"type":"concluir","response":"ok"}, "memory_delta": []},
    ):
        with pytest.raises(StructuredResponseError):
            parse_profile_response(raw, "ecc")


def test_rev375_current_wire_rejects_empty_explore():
    wire = wire_schema_for_profile("ecc")
    import jsonschema
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"type":"explorar","operations":[],"memory_delta":[]}, wire)


def test_rev375_ecc_wire_schema_does_not_semantically_validate_memory():
    wire = wire_schema_for_profile("ecc")
    import jsonschema
    # Sidecar validity belongs to Eyle Memory parser after ECC family validity.
    jsonschema.validate({
        "type":"concluir","response":"ok",
        "memory_delta":[{"totally":"invalid-memory-semantics"}],
    }, wire)


def test_rev375_one_fresh_eyle_decision_after_adapter_wire_failure(monkeypatch, tmp_path):
    sequence = iter([
        ErroLLM("adapter candidate invalid", transient=False, error_code="LLM_STRUCTURED_RESPONSE_UNSATISFIED"),
        {"type":"concluir","response":"survived","memory_delta":[]},
    ])
    calls={"n":0}
    def fake(prompt,cfg):
        calls["n"] += 1
        item=next(sequence)
        if isinstance(item,Exception): raise item
        return item
    monkeypatch.setattr(agent,"executar_ecc_llm",fake)
    status,text,pending,details=run_agent(
        agent,"answer",base_config(),provider_context=provider_context(tmp_path),retornar_detalhes=True,
    )
    assert (status,text,pending)==("completed","survived",None)
    assert calls["n"]==2


def test_rev375_second_wire_failure_without_progress_is_terminal(monkeypatch,tmp_path):
    calls={"n":0}
    def fake(prompt,cfg):
        calls["n"] += 1
        raise ErroLLM("bad",transient=False,error_code="LLM_STRUCTURED_RESPONSE_UNSATISFIED")
    monkeypatch.setattr(agent,"executar_ecc_llm",fake)
    status,text,pending,details=run_agent(
        agent,"answer",base_config(),provider_context=provider_context(tmp_path),retornar_detalhes=True,
    )
    assert status=="failed"
    assert details["failure_code"]=="ECC_WIRE_INVALID"
    assert calls["n"]==2


def test_rev375_execution_progress_is_not_a_turn_ceiling():
    source=Path(agent.__file__).read_text(encoding="utf-8")
    progress_source=Path(agent.__file__).parents[1]/"runtime"/"execution_progress.py"
    progress_text=progress_source.read_text(encoding="utf-8")
    assert "ExecutionProgress" in source
    assert "ECC_NO_PROGRESS_UNRECOVERABLE" in source
    assert "NO_PROGRESS_REPEATS_AFTER_WARNING = 2" in progress_text
    assert "MAX_TURNS" not in source
    assert "cognition_episode" not in source


def test_rev375_structured_empty_is_wire_error_not_generic_transport_retry(monkeypatch):
    import llm.executar as llm_mod
    from eyle.runtime.execution_context import ExecutionContext
    monkeypatch.setattr(llm_mod, "_ensure_adapter_ready", lambda config: {"ok": True})

    class Response:
        headers={}
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def close(self): pass
        def read(self):
            return json.dumps({
                "choices":[{"message":{"content":""},"finish_reason":"stop"}],
                "usage":{"prompt_tokens":10,"completion_tokens":1},
                "model":"fake",
            }).encode()

    calls={"n":0}
    def fake_urlopen(req,timeout=None):
        calls["n"]+=1
        return Response()
    monkeypatch.setattr(llm_mod.urllib.request,"urlopen",fake_urlopen)
    cfg=base_config()
    cfg["llm"].update({"base_url":"http://127.0.0.1:8080","model":"auto","retry_max_attempts":3})
    execution=ExecutionContext.from_config(cfg)
    with pytest.raises(ErroLLM) as exc:
        llm_mod._chamar_llm("s","u",cfg,execution=execution,perfil="ecc")
    assert exc.value.error_code=="STRUCTURED_RESPONSE_INVALID:ecc:STRUCTURED_EMPTY"
    assert calls["n"]==1


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
