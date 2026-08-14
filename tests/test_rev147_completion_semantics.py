from __future__ import annotations

import json
from pathlib import Path

import pytest

from eyle.core.token_budget import estimate_tokens
from llm.executar import PROMPT_AGENTE
from llm.structured import StructuredResponseError, parse_agent_response, schema_for_profile


def _decision(kind: str):
    return {
        "action": {"kind": kind, "answer": "done", "limitations": [], "grounding_ids": [], "effect_ids": []},
        "investigation_updates": [],
        "task_updates": [],
    }


def test_complete_is_the_only_terminal_action_kind():
    parsed = parse_agent_response(_decision("complete"))
    assert parsed["action"]["kind"] == "complete"
    with pytest.raises(StructuredResponseError) as exc:
        parse_agent_response(_decision("final"))
    assert exc.value.code == "AGENT_ACTION_KIND_INVALID"


def test_agent_schema_exposes_complete_and_not_legacy_final():
    schema = schema_for_profile("agent")
    action_variants = schema["properties"]["action"]["anyOf"]
    kinds = [variant["properties"]["kind"]["enum"][0] for variant in action_variants]
    assert kinds == ["capability_calls", "await_user", "complete"]
    assert "final" not in kinds and "patches" not in kinds

def test_prompt_uses_completion_coordinates_without_adding_mandatory_workflow():
    assert "complete: deliver the terminal answer" in PROMPT_AGENTE.lower()
    assert "completion_mode" not in PROMPT_AGENTE
    assert "A plan is not an effect" in PROMPT_AGENTE
    assert "runtime_effects contains eff-* coordinates" in PROMPT_AGENTE
    lower = PROMPT_AGENTE.lower()
    for forbidden in ("must use a capability", "must create an investigation", "must create a task", "always investigate", "before complete, inspect"):
        assert forbidden not in lower
    assert estimate_tokens(PROMPT_AGENTE, 3) > 570

def test_release_manifest_declares_rev15_completion_coordinates():
    manifest = json.loads(Path("release_manifest.json").read_text(encoding="utf-8"))
    assert manifest["config_schema_version"] == "2.7.5-r1.5.3"
    assert manifest["revision"] == "rev1.5.3-cognitive-task-memory"
    assert manifest["agent_action_kinds"] == ["capability_calls", "await_user", "complete"]
    assert "grounding_ids and effect_ids" in manifest["architecture"]["completion"]

