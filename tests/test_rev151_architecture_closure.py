from __future__ import annotations

import json
from pathlib import Path

import pytest

import eyle.core.agent as agent
from eyle.capabilities import Provider, build_registry
from eyle.contracts.capability import physical_effect, result
from eyle.host import Host
from eyle.runtime import service
from llm.structured import parse_agent_response, schema_for_profile
from tests.canonical import agent_await_user, agent_complete, agent_tools, base_config, tool_call


def _schema():
    return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def _petbot_provider(state=None):
    state = state if isinstance(state, dict) else {"food": 63, "dispensed": 0}

    def observe_level(arguments, ctx):
        return result(
            "success", True, True,
            detail={"percent": state["food"]},
            observations=[{
                "source_type": "food_level",
                "locator": {"kind": "sensor", "device": "petbot-1", "name": "food"},
                "content": json.dumps({"percent": state["food"]}),
            }],
        )

    def prepare(arguments, ctx):
        return {"ok": True, "question": f"Dispensar {arguments['grams']} g?", "state": {"grams": arguments["grams"]}}

    def confirm(prepared, ctx):
        grams = int(prepared["grams"])
        state["dispensed"] += grams
        return result(
            "success", True, True, changed=True,
            detail={"grams": grams},
            physical_effect_value=physical_effect("petbot.feeder", "dispense", "persistent", changed=True),
        )

    return Provider(
        "petbot",
        {
            "food_level": {
                "description": "Read the connected PetBot food reservoir sensor.",
                "input_schema": _schema(),
                "returns": "Measured food percentage.",
                "effect": "observe",
                "fn": observe_level,
                "produces_grounding": True,
            },
            "dispense_food": {
                "description": "Dispense food through the connected feeder.",
                "input_schema": {
                    "type": "object",
                    "properties": {"grams": {"type": "integer", "minimum": 1, "maximum": 500}},
                    "required": ["grams"],
                    "additionalProperties": False,
                },
                "returns": "Confirmed feeder actuation.",
                "effect": "mutate",
                "confirmation": "required",
                "prepare": prepare,
                "confirm": confirm,
            },
        },
        describe=lambda ctx: {"connected": True, "device": "PetBot"},
    )


def test_registry_namespaces_local_ids_and_allows_same_local_name_across_providers():
    spec = {"description": "status", "input_schema": _schema(), "returns": "status", "effect": "observe", "fn": lambda a, c: result("success", True, True, detail={})}
    registry = build_registry([Provider("petbot", {"status": spec}), Provider("router", {"status": spec})])
    assert registry.names() == ["petbot.status", "router.status"]
    assert registry.spec("status") == {}
    assert registry.spec("petbot.status")["description"] == "status"


def test_registry_rejects_effect_result_incoherence_mechanically():
    observe_bad = Provider("sensor", {"read": {
        "description": "read", "input_schema": _schema(), "returns": "read", "effect": "observe",
        "fn": lambda a, c: result("success", True, True, changed=True, physical_effect_value=physical_effect("sensor", "read", "persistent", changed=True)),
    }})
    mutate_bad = Provider("actuator", {"move": {
        "description": "move", "input_schema": _schema(), "returns": "move", "effect": "mutate",
        "fn": lambda a, c: result("success", True, True, changed=True),
    }})
    r1 = build_registry([observe_bad]).execute("sensor.read", {}, {})
    r2 = build_registry([mutate_bad]).execute("actuator.move", {}, {})
    assert r1["error_code"] == "CAPABILITY_EFFECT_CONTRACT_VIOLATION"
    assert r2["error_code"] == "CAPABILITY_EFFECT_REQUIRED"


def test_investigation_and_task_deltas_are_optional_not_empty_ceremony():
    schema = schema_for_profile("agent")
    assert schema["required"] == ["action"]
    parsed = parse_agent_response({"action": {"kind": "complete", "answer": "ok", "limitations": [], "grounding_ids": [], "effect_ids": []}})
    assert set(parsed) == {"action"}


def test_await_user_resolution_becomes_authoritative_request_context(monkeypatch):
    registry = build_registry([])
    prompts = []
    outputs = iter([
        agent_await_user("Qual aplicação?", reason="The user must provide the missing target."),
        agent_complete("Entendi: calculadora simples."),
    ])
    monkeypatch.setattr(agent, "executar_agente_llm", lambda prompt, cfg: prompts.append(json.loads(prompt)) or next(outputs))
    cfg = base_config(); cfg["providers"] = {}
    status, _, pending, _ = agent.executar_agente("O que você quer fazer?", cfg, provider_context={}, retornar_detalhes=True, registry=registry)
    assert status == "await_user"
    status, text, _, _ = agent.executar_agente(
        pending["session"]["request"], cfg, provider_context={}, retomar=pending,
        resposta_usuario="Quero uma calculadora simples.", retornar_detalhes=True, registry=registry,
    )
    assert status == "success" and "calculadora" in text
    assert prompts[-1]["request"] == "O que você quer fazer?"
    assert prompts[-1]["request_context"][-1]["answer"] == "Quero uma calculadora simples."
    assert prompts[-1]["prior_conversation"] == []


def test_confirmation_executes_then_returns_effect_to_main(monkeypatch):
    state = {"food": 63, "dispensed": 0}
    registry = build_registry([_petbot_provider(state)])
    seen = []

    def decide(prompt, cfg):
        payload = json.loads(prompt); seen.append(payload)
        if payload.get("runtime_effects"):
            return agent_complete({"answer": "Feito.", "effect_ids": [payload["runtime_effects"][0]["id"]]})
        return agent_tools(tool_call("petbot.dispense_food", {"grams": 20}))

    monkeypatch.setattr(agent, "executar_agente_llm", decide)
    cfg = base_config(); cfg["providers"] = {"petbot": {}}
    status, _, pending, _ = agent.executar_agente("Dê 20 g.", cfg, provider_context={}, retornar_detalhes=True, registry=registry)
    assert status == "await_user" and state["dispensed"] == 0
    status, text, pending2, details = agent.executar_agente(
        pending["session"]["request"], cfg, provider_context={}, retomar=pending,
        resposta_usuario="confirmar", retornar_detalhes=True, registry=registry,
    )
    assert status == "success" and text == "Feito." and pending2 is None
    assert state["dispensed"] == 20
    assert seen[-1]["runtime_effects"][0]["physical_effect"]["resource"] == "petbot.feeder"
    assert details["reality_epoch"] == 1


def test_petbot_host_runs_end_to_end_through_public_service_without_standard(monkeypatch):
    registry = build_registry([_petbot_provider()])
    host = Host(registry=registry, context_factory=lambda: {"petbot": {"device_id": "petbot-1"}})
    cfg = base_config(); cfg["providers"] = {"petbot": {}}
    prompts = []
    outputs = iter([
        agent_tools(tool_call("petbot.food_level", {})),
        agent_complete({"answer": "O reservatório está em 63%.", "grounding_ids": ["mat-0001"]}),
    ])
    monkeypatch.setattr(service, "HOST", host)
    monkeypatch.setattr(service, "carregar_config", lambda: cfg)
    monkeypatch.setattr(service, "carregar_agent_pendente", lambda: None)
    monkeypatch.setattr(service, "carregar_conversa", lambda: [])
    monkeypatch.setattr(service, "registrar_mensagem", lambda *a, **k: None)
    monkeypatch.setattr(agent, "executar_agente_llm", lambda prompt, config: prompts.append(json.loads(prompt)) or next(outputs))

    response = service.processar("Quanto de ração ainda tem?", registrar_pergunta=False, historico_snapshot=[])
    assert response["status"] == "success"
    assert "63%" in response["resposta"]
    assert response["details"]["capabilities_used"] == ["petbot.food_level"]
    assert {item["name"] for item in prompts[0]["available_capabilities"]} == {"petbot.food_level", "petbot.dispense_food"}
    assert "standard" not in prompts[0]["environment"]["providers"]


def test_architecture_boundaries_are_physically_separate():
    root = Path(__file__).resolve().parents[1]
    core = (root / "eyle/core/agent.py").read_text(encoding="utf-8")
    service_source = (root / "eyle/runtime/service.py").read_text(encoding="utf-8")
    capability_init = (root / "eyle/capabilities/__init__.py").read_text(encoding="utf-8")
    assert "default_registry" not in core + capability_init
    assert "providers.standard" not in service_source and "standard_impl" not in service_source
    for folder in (root / "eyle/providers", root / "eyle/capabilities"):
        for path in folder.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "from eyle.core" not in text and "import eyle.core" not in text, path
