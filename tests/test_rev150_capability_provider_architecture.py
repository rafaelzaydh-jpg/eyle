from __future__ import annotations

from tests.canonical import run_agent
import json

import eyle.core.agent as agent
from eyle.capabilities import Provider, build_registry
from eyle.contracts.capability import physical_effect, result
from llm.executar import PROMPT_AGENTE
from llm.structured import schema_for_profile
from tests.canonical import agent_complete, agent_tools, base_config, tool_call


def _petbot_provider():
    state = {"food_level": 63, "dispensed": 0}

    def describe(ctx):
        return {"connected": True, "device": "PetBot", "sensors": ["food_level"], "actuators": ["feeder"]}

    def food_level(arguments, ctx):
        return result(
            "success", True, True,
            detail={"percent": state["food_level"]},
            observations=[{
                "source_type": "petbot.food_level",
                "locator": {"kind": "device_sensor", "device": "petbot-1", "sensor": "food_level"},
                "content": json.dumps({"percent": state["food_level"]}),
            }],
        )

    def prepare(arguments, ctx):
        grams = int(arguments["grams"])
        return {"ok": True, "question": f"Dispensar {grams} g de ração?", "state": {"grams": grams}}

    def confirm(prepared, ctx):
        grams = int(prepared["grams"])
        state["dispensed"] += grams
        return result(
            "success", True, True, changed=True,
            detail={"grams": grams, "total_dispensed": state["dispensed"]},
            physical_effect_value=physical_effect("petbot.feeder", "dispense", "persistent", changed=True),
        )

    capabilities = {
        "food_level": {
            "description": "Read the current food level measured by the PetBot reservoir sensor.",
            "effect": "observe",
            "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            "returns": "Current reservoir level as a measured percentage.",
            "caveats": ["A sensor reading describes the measured device state at execution time."],
            "produces_grounding": True,
            "fn": food_level,
        },
        "dispense_food": {
            "description": "Dispense a requested amount of food through the connected PetBot feeder.",
            "effect": "mutate",
            "input_schema": {
                "type": "object",
                "properties": {"grams": {"type": "integer", "minimum": 1, "maximum": 500}},
                "required": ["grams"], "additionalProperties": False,
            },
            "returns": "The amount dispensed and a persistent physical effect for the feeder.",
            "caveats": ["Requires explicit confirmation before the actuator is triggered."],
            "confirmation": "required",
            "prepare": prepare,
            "confirm": confirm,
        },
    }
    return Provider("petbot", capabilities, describe=describe), state


def test_core_contract_has_only_capabilities_await_and_complete():
    schema = schema_for_profile("agent")
    action = schema["properties"]["action"]
    kinds = [branch["properties"]["kind"]["enum"][0] for branch in action["anyOf"]]
    assert kinds == ["capability_calls", "await_user", "complete"]
    assert "patches" not in json.dumps(schema)
    assert "tool_calls" not in json.dumps(schema)


def test_main_prompt_is_domain_neutral_and_provider_driven():
    lowered = PROMPT_AGENTE.lower()
    assert "capabilities come from independent providers" in lowered
    assert "those descriptions are the authority" in lowered
    assert "if you are unsure whether you possess enough information" in lowered
    for domain_name in ("search_code", "read_file", "workspace_transaction", "petbot", "router", "python"):
        assert domain_name not in lowered


def test_provider_supplies_its_own_model_contract_and_environment():
    provider, _ = _petbot_provider()
    registry = build_registry([provider])
    catalog = {item["name"]: item for item in registry.catalog(base_config())}
    assert catalog["petbot.food_level"]["provider"] == "petbot"
    assert "reservoir sensor" in catalog["petbot.food_level"]["purpose"]
    assert catalog["petbot.dispense_food"]["confirmation"] == "required"
    assert registry.environment({})["providers"]["petbot"]["device"] == "PetBot"


def test_agent_can_observe_an_unknown_domain_without_core_changes(monkeypatch):
    provider, _ = _petbot_provider()
    registry = build_registry([provider])
    outputs = iter([
        agent_tools(tool_call("petbot.food_level", {})),
        agent_complete({"answer": "O reservatório está em 63%.", "grounding_ids": ["mat-0001"]}),
    ])
    monkeypatch.setattr(agent, "executar_agente_llm", lambda prompt, cfg: next(outputs))
    status, answer, _, details = run_agent(agent, 
        "Quanto de ração ainda tem?", base_config(), provider_context={}, retornar_detalhes=True, registry=registry,
    )
    assert status == "success"
    assert "63%" in answer
    assert details["observation_ledger_size"] == 1
    assert details["grounding_count_total"] == 1


def test_generic_confirmation_produces_effect_for_unknown_domain(monkeypatch):
    provider, state = _petbot_provider()
    registry = build_registry([provider])
    def decide(prompt, cfg):
        payload = json.loads(prompt)
        if payload.get("runtime_effects"):
            effect_id = payload["runtime_effects"][0]["id"]
            return agent_complete({"answer": "Ração dispensada.", "effect_ids": [effect_id]})
        return agent_tools(tool_call("petbot.dispense_food", {"grams": 40}))
    monkeypatch.setattr(agent, "executar_agente_llm", decide)
    status, question, pending, _ = run_agent(agent, 
        "Dê 40 g de ração.", base_config(), provider_context={}, retornar_detalhes=True, registry=registry,
    )
    assert status == "await_user"
    assert pending["continuation_kind"] == "capability_confirmation"
    assert pending["provider"] == "petbot"
    assert state["dispensed"] == 0

    status2, answer2, pending2, details2 = run_agent(agent, 
        "Dê 40 g de ração.", base_config(), provider_context={}, retomar=pending,
        resposta_usuario="confirmar", retornar_detalhes=True, registry=registry,
    )
    assert status2 == "success"
    assert pending2 is None
    assert state["dispensed"] == 40
    assert details2["observation_ledger_size"] == 1
    effects = details2["physical_effects"]
    assert effects and effects[0]["physical_effect"] == {
        "resource": "petbot.feeder", "operation": "dispense", "persistence": "persistent", "changed": True,
    }
    assert details2["reality_epoch"] == 1
