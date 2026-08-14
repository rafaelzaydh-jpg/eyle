from __future__ import annotations

from tests.canonical import run_agent
import json
from pathlib import Path

import pytest

import eyle.core.agent as agent
import eyle.providers.standard as tools
from eyle.core.validation import validate_complete
from llm.executar import PROMPT_AGENTE
from llm.structured import StructuredResponseError, parse_agent_response, schema_for_profile
from tests.canonical import agent_complete, agent_tools, base_config, tool_call, standard_registry


def _decision(action):
    return {"action": action, "investigation_updates": [], "task_updates": []}


def test_complete_schema_uses_coordinates_without_completion_mode():
    schema = schema_for_profile("agent")
    complete = next(v for v in schema["properties"]["action"]["anyOf"] if v["properties"]["kind"]["enum"] == ["complete"])
    assert set(complete["required"]) == {"kind", "answer", "limitations", "grounding_ids", "effect_ids"}
    assert "completion_mode" not in json.dumps(complete)
    parsed = parse_agent_response(_decision({"kind": "complete", "answer": "oi", "limitations": [], "grounding_ids": [], "effect_ids": []}))
    assert parsed["action"]["kind"] == "complete"

def test_validate_complete_validates_coordinate_identity_without_semantic_classification():
    materials = {"mat-0001": {"id": "mat-0001"}}
    effects = {"eff-0001": {"id": "eff-0001", "physical_effect": {"resource": "x", "operation": "y", "persistence": "call", "changed": False}}}
    ok, reason, *_ = validate_complete({"answer": "oi", "limitations": [], "grounding_ids": [], "effect_ids": []}, materials, effects)
    assert ok is True and reason == "ok"
    ok, reason, *_ = validate_complete({"answer": "vi", "limitations": [], "grounding_ids": ["mat-9999"], "effect_ids": []}, materials, effects)
    assert ok is False and reason.startswith("COMPLETE_UNKNOWN_GROUNDING:")
    ok, reason, *_ = validate_complete({"answer": "executei", "limitations": [], "grounding_ids": [], "effect_ids": ["eff-9999"]}, materials, effects)
    assert ok is False and reason.startswith("COMPLETE_UNKNOWN_EFFECT:")
    ok, reason, *_ = validate_complete({"answer": "executei", "limitations": [], "grounding_ids": ["mat-0001"], "effect_ids": ["eff-0001"]}, materials, effects)
    assert ok is True and reason == "ok"

def test_direct_complete_still_needs_no_tool_or_artificial_commitment(monkeypatch, tmp_path):
    prompts = []
    monkeypatch.setattr(
        agent, "executar_agente_llm",
        lambda prompt, cfg: prompts.append(json.loads(prompt)) or agent_complete("4"),
    )
    status, text, pending, details = run_agent(agent, 
        "quanto é 2+2?", base_config(),
        provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retornar_detalhes=True,
    )
    assert (status, text, pending) == ("success", "4", None)
    assert details["capability_calls"] == 0
    assert details["physical_effects"] == []
    assert len(prompts) == 1


def test_runtime_does_not_semantically_classify_complete_prose(monkeypatch, tmp_path):
    # Rev1.5 deliberately removes the direct/observed/effect classifier. Main owns
    # whether a coordinate is semantically needed; Runtime validates coordinates only.
    monkeypatch.setattr(agent, "executar_agente_llm", lambda prompt, cfg: agent_complete({"answer": "Resposta sem coordenadas."}))
    status, text, pending, details = run_agent(agent, 
        "responda", base_config(), provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retornar_detalhes=True,
    )
    assert (status, text, pending) == ("success", "Resposta sem coordenadas.", None)
    accepted = [item for item in details["decision_history"] if item.get("decision") == "complete" and item.get("outcome") == "accepted"]
    assert accepted

def test_prompt_teaches_reality_and_full_provider_contracts_without_cost_quota(monkeypatch, tmp_path):
    assert "WORLD AND CAPABILITIES" in PROMPT_AGENTE
    assert "A requested call is not evidence that it executed" in PROMPT_AGENTE
    assert "Never silently turn remembered, inferred, planned or generated content" in PROMPT_AGENTE
    assert "Capabilities come from independent providers" in PROMPT_AGENTE
    assert "completion_mode" not in PROMPT_AGENTE

    seen = []
    monkeypatch.setattr(agent, "executar_agente_llm", lambda prompt, cfg: seen.append(json.loads(prompt)) or agent_complete("ok"))
    status, *_ = run_agent(agent, "oi", base_config(), provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retornar_detalhes=True)
    assert status == "success"
    catalog = seen[0]["available_capabilities"]
    expected = agent._available_capabilities(base_config(), {"standard": {"caminho_origem": str(tmp_path)}}, standard_registry())
    assert {item["name"] for item in catalog} == expected
    assert all(set(("name", "provider", "purpose", "effect", "inputs", "returns", "caveats", "limits", "confirmation")) <= set(item) for item in catalog)
    assert "active_tools" not in seen[0]

def test_rev15_identity_and_no_task_wide_token_fuse():
    manifest = json.loads(Path("release_manifest.json").read_text(encoding="utf-8"))
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert manifest["config_schema_version"] == "2.7.5-r1.5.3"
    assert manifest["revision"] == "rev1.5.3-cognitive-task-memory"
    assert "max_total_tokens" not in config["agent"]
    source = Path("llm/executar.py").read_text(encoding="utf-8")
    assert "MAX_TOTAL_TOKENS_EXCEEDED" not in source

