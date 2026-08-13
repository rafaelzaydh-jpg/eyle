from __future__ import annotations

import inspect
from pathlib import Path

from eyle.core import agent, observation, tools


def test_generic_material_accepts_non_file_locator():
    ledger = {"materials": {}}
    ids = observation.register_material_candidates(ledger, [{
        "locator": {"kind": "device", "id": "sensor-7", "channel": "temperature"},
        "content": "23.5 C",
        "source_type": "sensor_read",
    }])
    assert ids == ["mat-0001"]
    material = ledger["materials"]["mat-0001"]
    assert material["locator"]["kind"] == "device"
    assert material["content_hash"]
    assert "file" not in material and "file_hash" not in material




def test_observation_core_does_not_name_public_capabilities():
    source = Path(observation.__file__).read_text(encoding="utf-8")
    for name in tools.TOOLS:
        assert f'"{name}"' not in source
        assert f"'{name}'" not in source
    assert "material_candidates_from_tool" not in source
    assert "def observation_signature(" not in source


def test_agent_has_no_capability_specific_branching():
    source = Path(agent.__file__).read_text(encoding="utf-8")
    for name in tools.TOOLS:
        assert f'if tool == "{name}"' not in source
        assert f'if tool in {{"{name}"' not in source
        assert f'elif tool == "{name}"' not in source


def test_grounding_capabilities_own_material_extraction():
    for name, spec in tools.TOOLS.items():
        if spec.get("produces_grounding"):
            assert callable(spec.get("observe")), name
