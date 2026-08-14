from __future__ import annotations

import inspect

import pytest

from eyle.capabilities.registry import CapabilityRegistry, Provider
from eyle.contracts.capability import capability_public_contract
from eyle.providers import standard
from llm.executar import PROMPT_AGENTE


def _schema():
    return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def _result():
    from eyle.contracts.capability import result
    return result("success", True, True)


def test_main_prompt_teaches_generic_causal_effect_literacy_without_domain_routing():
    lower = PROMPT_AGENTE.lower()
    required = (
        "capability success is not automatically task success",
        "resource identifies what was affected",
        "persistence identifies how long",
        "temporary, isolated, simulated, or different-resource effect",
        "establishes and does_not_establish",
        "do not present the objective as completed merely because a command/call succeeded",
        "complete does not end the conversation",
    )
    for phrase in required:
        assert phrase in lower
    for domain_name in ("run_command", "workspace_transaction", "password_generator", "petbot", "router.restart"):
        assert domain_name not in lower
    assert "if the request contains" not in lower


def test_provider_causal_boundary_is_projected_to_main_catalog():
    registry = CapabilityRegistry()
    registry.register(Provider("demo", {
        "simulate": {
            "description": "Simulate a device action.",
            "input_schema": _schema(),
            "returns": "Simulation result.",
            "effect": "execute",
            "confirmation": "none",
            "establishes": ["Behavior inside the simulator."],
            "does_not_establish": ["Persistent state of the external device."],
            "fn": lambda arguments, ctx: _result(),
        }
    }))
    [item] = registry.catalog()
    assert item["name"] == "demo.simulate"
    assert item["establishes"] == ["Behavior inside the simulator."]
    assert item["does_not_establish"] == ["Persistent state of the external device."]


def test_provider_causal_boundary_shape_is_mechanically_validated():
    registry = CapabilityRegistry()
    with pytest.raises(ValueError, match="CAPABILITY_CAUSAL_DESCRIPTION_INVALID"):
        registry.register(Provider("bad", {
            "x": {
                "description": "Bad causal contract.",
                "input_schema": _schema(),
                "returns": "Nothing.",
                "effect": "observe",
                "confirmation": "none",
                "establishes": "not-a-list",
                "fn": lambda arguments, ctx: _result(),
            }
        }))


def test_standard_run_command_explicitly_cannot_establish_persistent_workspace_mutation():
    spec = standard.CAPABILITIES["run_command"]
    public = capability_public_contract("standard.run_command", "standard", spec, {})
    assert public["effect"] == "execute"
    assert any("isolated" in text.lower() for text in public["establishes"])
    denied = " ".join(public["does_not_establish"]).lower()
    assert "persistent" in denied
    assert "real workspace" in denied
    caveats = " ".join(public["caveats"]).lower()
    assert "real workspace" in caveats
    assert "persistent workspace mutation" in caveats


def test_standard_workspace_transaction_explicitly_establishes_persistent_real_workspace_effect():
    spec = standard.CAPABILITIES["workspace_transaction"]
    public = capability_public_contract("standard.workspace_transaction", "standard", spec, {})
    assert public["effect"] == "mutate"
    assert public["confirmation"] == "required"
    established = " ".join(public["establishes"]).lower()
    assert "real user workspace" in established
    assert "persistent" in established
    denied = " ".join(public["does_not_establish"]).lower()
    assert "before explicit confirmation" in denied


def test_complete_runtime_remains_coordinate_validator_not_prose_classifier():
    # Rev1.5.2 teaches causal interpretation to Main but deliberately does not
    # reintroduce a semantic prose judge into Runtime.
    from eyle.core import validation
    source = inspect.getsource(validation.validate_complete).lower()
    for forbidden in ("workspace", "sandbox", "created", "persist", "regex", "re.search"):
        assert forbidden not in source
