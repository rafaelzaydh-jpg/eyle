from __future__ import annotations

from tests.canonical import run_agent
from tests.canonical import standard_registry
import json
from pathlib import Path

import pytest

import eyle.core.agent as core_agent
from eyle.core.investigation import apply_investigation_updates
from eyle.core.tasks import apply_task_updates
from eyle.runtime.config import ConfigError, validar_config
from llm.structured import StructuredResponseError, schema_for_profile
from tests.canonical import (
    agent_complete,
    agent_tools,
    base_config,
    investigation_target,
    task_item,
    tool_call,
)


def test_claim_is_physically_removed_from_rev14_contract():
    root = Path(core_agent.__file__).resolve().parents[2]
    assert not (root / "eyle" / "core" / "claim_review.py").exists()
    assert "PROMPT_CLAIM" not in (root / "llm" / "executar.py").read_text(encoding="utf-8")
    assert "claim_verifier" not in (root / "llm" / "structured.py").read_text(encoding="utf-8")
    with pytest.raises(StructuredResponseError) as exc:
        schema_for_profile("claim_verifier")
    assert exc.value.code == "STRUCTURED_PROFILE_UNKNOWN"


def test_claim_config_is_rejected_as_removed_contract():
    cfg = base_config()
    cfg["agent"]["claims"] = {"mode": "fresh"}
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD:agent:claims"):
        validar_config(cfg, standard_registry())


def test_simple_final_uses_one_main_call_and_no_second_llm(monkeypatch, tmp_path):
    calls = []

    def fake_main(prompt, _config):
        calls.append(json.loads(prompt))
        return agent_complete("ok")

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_main)
    status, text, _, details = run_agent(core_agent, 
        "Say ok.", base_config(), provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retornar_detalhes=True,
    )
    assert status == "success" and text == "ok"
    assert len(calls) == 1
    assert all(item.get("decision") != "claim_review" for item in details["decision_history"])


def test_investigation_established_requires_real_material():
    open_state, _, rejected = apply_investigation_updates(
        [investigation_target("inv", status="open")], grounding={}
    )
    assert not rejected
    same, _, rejected = apply_investigation_updates(
        [investigation_target("inv", status="established", reason="done")],
        previous=open_state,
        grounding={},
    )
    assert same == open_state
    assert rejected[0]["reason"] == "INVESTIGATION_ESTABLISHED_GROUNDING_REQUIRED:inv"


def test_task_requires_completion_criteria_and_parent_cannot_close_over_open_child():
    malformed = {
        "id": "x", "parent_id": None, "description": "x",
        "completion_criteria": [], "status": "open", "result": "", "grounding_ids": [],
    }
    state, _, rejected = apply_task_updates([malformed])
    assert state == []
    assert rejected[0]["reason"] == "TASK_COMPLETION_CRITERIA_REQUIRED:x"

    state, _, rejected = apply_task_updates([
        task_item("root", description="root"),
        task_item("child", parent_id="root", description="child"),
    ])
    assert not rejected
    unchanged, _, rejected = apply_task_updates([
        task_item("root", description="root", status="completed", result="done")
    ], previous=state)
    assert unchanged == state
    assert rejected[0]["reason"].startswith("TASK_COMPLETED_WITH_OPEN_CHILDREN:root:child")


def test_committed_investigation_grounding_must_reach_final(monkeypatch, tmp_path):
    (tmp_path / "fact.txt").write_text("alpha\n", encoding="utf-8")
    calls = {"n": 0}

    def fake_main(prompt, _config):
        calls["n"] += 1
        payload = json.loads(prompt)
        if calls["n"] == 1:
            return agent_tools(
                tool_call("read_file", {"path": "fact.txt"}),
                investigation=[investigation_target("inv", goal="Read the fact")],
            )
        if calls["n"] == 2:
            return agent_complete(
                {"answer": "alpha", "grounding_ids": []},
                investigation=[investigation_target(
                    "inv", goal="Read the fact", status="established",
                    grounding_ids=["mat-0001"], reason="Observed fact.txt",
                )],
            )
        assert "COMPLETE_REQUIRED_GROUNDING_MISSING:mat-0001" in str(payload.get("runtime_feedback") or "")
        return agent_complete({"answer": "alpha", "grounding_ids": ["mat-0001"]})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_main)
    status, text, _, details = run_agent(core_agent, 
        "Read the fact.", base_config(), provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retornar_detalhes=True,
    )
    assert status == "success" and text == "alpha"
    assert calls["n"] == 3
    finals = [item for item in details["decision_history"] if item.get("decision") == "complete"]
    assert [item.get("outcome") for item in finals] == ["rejected", "accepted"]


def test_grounded_completed_task_contributes_required_final_material(monkeypatch, tmp_path):
    (tmp_path / "fact.txt").write_text("beta\n", encoding="utf-8")
    calls = {"n": 0}

    def fake_main(prompt, _config):
        calls["n"] += 1
        payload = json.loads(prompt)
        if calls["n"] == 1:
            return agent_tools(
                tool_call("read_file", {"path": "fact.txt"}),
                tasks=[task_item("read", description="Read fact")],
            )
        if calls["n"] == 2:
            return agent_complete(
                {"answer": "beta", "grounding_ids": []},
                tasks=[task_item(
                    "read", description="Read fact", status="completed", result="Read beta",
                    grounding_ids=["mat-0001"],
                )],
            )
        assert "COMPLETE_REQUIRED_GROUNDING_MISSING:mat-0001" in str(payload.get("runtime_feedback") or "")
        return agent_complete({"answer": "beta", "grounding_ids": ["mat-0001"]})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_main)
    status, text, _, details = run_agent(core_agent, 
        "Read the fact.", base_config(), provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retornar_detalhes=True,
    )
    assert status == "success" and text == "beta"
    assert calls["n"] == 3
    assert details["grounding_usage"]["task_grounding_count"] == 1


def test_agent_schema_carries_completion_contract_without_claim_profile():
    schema = schema_for_profile("agent")
    task = schema["properties"]["task_updates"]["items"]
    assert set(task["required"]) == {
        "id", "parent_id", "description", "completion_criteria",
        "status", "result", "grounding_ids",
    }
    assert task["properties"]["completion_criteria"]["minItems"] == 1
    assert task["properties"]["grounding_ids"]["items"]["pattern"] == r"^mat-[0-9]+$"
