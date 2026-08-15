from __future__ import annotations

import json
from pathlib import Path

import pytest

import eyle.core.agent as agent
from eyle.core.session import AgentSession, SESSION_SCHEMA_VERSION
from llm.structured import StructuredResponseError, parse_profile_response, schema_for_profile
from tests.canonical import base_config, run_agent, standard_registry


def memory(operations=None):
    ops = list(operations or [])
    return {"focus": [], "disposition": "updated" if ops else "unchanged", "operations": ops}


def objective(disposition="unchanged", state=None):
    return {"disposition": disposition, "state": state}


def state(summary, status="active", children=None, constraints=None):
    return {
        "summary": summary,
        "status": status,
        "children": list(children or []),
        "constraints": list(constraints or []),
    }


def conclude(text, *, obj=None, mem=None):
    return {
        "type": "concluir",
        "response": text,
        "objective": obj or objective(),
        "memory": mem or memory(),
    }


def explore(operation, arguments, *, obj=None, mem=None):
    return {
        "type": "explorar",
        "operation": operation,
        "arguments": dict(arguments),
        "objective": obj or objective(),
        "memory": mem or memory(),
    }


def provider_context(root: Path):
    return {
        "standard": {"caminho_origem": str(root)},
        "core_memory": {
            "storage_dir": str(root.parent / f"{root.name}_objective_memory"),
            "world_scope_id": f"workspace:{root.resolve()}",
        },
    }


def test_rev252_schema_keeps_three_ecc_moves_and_adds_objective_sidecar():
    schema = schema_for_profile("ecc")
    assert [v["properties"]["type"]["enum"][0] for v in schema["oneOf"]] == ["explorar", "construir", "concluir"]
    for variant in schema["oneOf"]:
        assert "objective" in variant["required"]
        assert variant["properties"]["objective"]["properties"]["disposition"]["enum"] == ["unchanged", "updated", "cleared"]
    parsed = parse_profile_response(conclude("oi"), "ecc")
    assert parsed["objective"] == {"disposition": "unchanged", "state": None}
    with pytest.raises(StructuredResponseError, match="objective"):
        parse_profile_response({"type": "concluir", "response": "x", "memory": memory()}, "ecc")


def test_rev252_session_persists_objective_separately_from_canonical_request():
    obj = state("Organizar documentos", children=[{"key": "xml", "description": "Separar XML", "status": "active"}])
    session = AgentSession("Ah, e deixa os XML separados.")
    session.objective_state = obj
    restored = AgentSession.from_dict(session.to_dict())
    assert SESSION_SCHEMA_VERSION == "2.7.5-r2.5.2-ecc"
    assert restored.request == "Ah, e deixa os XML separados."
    assert restored.objective_state == obj


def test_rev252_pure_conversation_can_have_no_objective_and_still_update_user_memory(monkeypatch, tmp_path):
    prompts = []
    def fake(prompt, _cfg):
        prompts.append(json.loads(prompt))
        return conclude(
            "Entendi 😄",
            obj=objective(),
            mem=memory([{
                "op": "remember", "scope": "user", "kind": "preference",
                "content": "O usuário não gosta de carrapatos.", "tags": ["preference", "carrapatos"],
                "supports": [{"kind": "request"}],
            }]),
        )
    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, pending, details = run_agent(
        agent, "Eu não gosto de carrapatos kk", base_config(),
        provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text, pending) == ("completed", "Entendi 😄", None)
    assert prompts[0]["current_request"] == "Eu não gosto de carrapatos kk"
    assert prompts[0]["objective_state"] is None
    assert details["objective_present"] is False
    assert details["physical_capability_calls"] == 0
    assert details["memory_nodes"] == 1


def test_rev252_compound_request_can_hold_semantic_subobjectives_without_runtime_plan(monkeypatch, tmp_path):
    request = "Faça o calculo de 28x12+2², produza a arquitetura de um site de vendas enquanto descobre quem vai inventar a maquina do tempo."
    compound = state(
        "Satisfazer a solicitação composta do usuário.",
        status="active",
        children=[
            {"key": "calc", "description": "Calcular 28×12+2²", "status": "resolved", "outcome": "340"},
            {"key": "site", "description": "Produzir arquitetura de um site de vendas", "status": "active"},
            {"key": "time", "description": "Determinar quem inventará a máquina do tempo", "status": "epistemically_unresolvable"},
        ],
        constraints=[],
    )
    prompts = []
    def fake(prompt, _cfg):
        prompts.append(json.loads(prompt))
        if len(prompts) == 1:
            return explore("inspect_project", {"source": "workspace"}, obj=objective("updated", compound))
        # Runtime does not inspect semantic statuses or require all children to be "resolved".
        return conclude("340; arquitetura proposta; inventor futuro não é determinável.", obj=objective())
    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, _, details = run_agent(
        agent, request, base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert status == "completed"
    assert text.startswith("340")
    assert prompts[0]["current_request"] == request and prompts[0]["objective_state"] is None
    assert prompts[1]["current_request"] == request
    assert prompts[1]["objective_state"] == compound
    assert details["objective_present"] is True
    assert details["objective_children"] == 3


def test_rev252_concluir_is_not_gated_by_objective_status(monkeypatch, tmp_path):
    still_active = state("Responder algo", status="active")
    monkeypatch.setattr(
        agent, "executar_ecc_llm",
        lambda _prompt, _cfg: conclude("feito", obj=objective("updated", still_active)),
    )
    status, text, _, details = run_agent(
        agent, "responda", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "feito")
    assert details["objective_present"] is True


def test_rev252_objective_can_be_cleared_mechanically(monkeypatch, tmp_path):
    first = state("Objetivo temporário", status="active")
    prompts = []
    def fake(prompt, _cfg):
        prompts.append(json.loads(prompt))
        if len(prompts) == 1:
            return explore("inspect_project", {"source": "workspace"}, obj=objective("updated", first))
        assert prompts[-1]["objective_state"] == first
        return conclude("ok", obj=objective("cleared", None))
    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, _, _, details = run_agent(
        agent, "teste", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert status == "completed"
    assert details["objective_present"] is False
