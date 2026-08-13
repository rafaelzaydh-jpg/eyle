from tests.canonical import run_agent
import copy
import json

import pytest

from eyle.core import agent
from eyle.core.investigation import (
    apply_investigation_updates,
    established_investigation_grounding_ids,
    investigation_grounding_ids,
    open_investigation_grounding_ids,
)
from eyle.core.session import AgentSession, SESSION_SCHEMA_VERSION
from eyle.core.tasks import apply_task_updates, task_state_view
from eyle.runtime.queue import QUEUE_SCHEMA_VERSION
from llm.executar import PROMPT_AGENTE
from llm.structured import StructuredResponseError, parse_agent_response
from tests.canonical import agent_complete, agent_tools, base_config, investigation_target, task_item, tool_call


def test_established_investigation_requires_semantic_conclusion_after_grounding_exists():
    grounding = {"mat-0001": {"id": "mat-0001"}}
    state, _, _ = apply_investigation_updates(
        [investigation_target(goal="What does X establish?")], grounding=grounding,
    )
    attempted = investigation_target(
        goal="What does X establish?", status="established", grounding_ids=["mat-0001"],
        conclusion="",
    )
    unchanged, _, rejected = apply_investigation_updates(
        [attempted], previous=state, grounding=grounding,
    )
    assert unchanged == state
    assert rejected[0]["reason"].startswith("INVESTIGATION_ESTABLISHED_CONCLUSION_REQUIRED")


def test_open_investigation_can_hold_provisional_conclusion_but_established_conclusion_is_durable():
    grounding = {"mat-0001": {"id": "mat-0001"}}
    provisional = investigation_target(
        goal="Understand X", status="open", grounding_ids=["mat-0001"],
        conclusion="X appears to do A, but one boundary remains unresolved.",
    )
    state, accepted, rejected = apply_investigation_updates([provisional], grounding=grounding)
    assert not rejected and accepted[0]["has_conclusion"] is True
    established = investigation_target(
        goal="Understand X", status="established", grounding_ids=["mat-0001"],
        conclusion="The observed implementation establishes A for the inspected boundary.",
    )
    state, _, rejected = apply_investigation_updates(established and [established], previous=state, grounding=grounding)
    assert not rejected
    assert state[0]["conclusion"].startswith("The observed implementation")


def test_dismissed_investigation_grounding_is_not_completion_grounding():
    grounding = {"mat-0001": {"id": "mat-0001"}, "mat-0002": {"id": "mat-0002"}}
    state, _, rejected = apply_investigation_updates([
        investigation_target(
            target_id="active", goal="A", status="established", grounding_ids=["mat-0001"],
            conclusion="A is established.",
        ),
        investigation_target(
            target_id="discarded", goal="B", status="dismissed", grounding_ids=["mat-0002"],
            conclusion="B was explored but is not needed.", reason="Not needed for delivery.",
        ),
    ], grounding=grounding)
    assert not rejected
    assert investigation_grounding_ids(state) == ["mat-0001"]
    assert open_investigation_grounding_ids(state) == []
    assert established_investigation_grounding_ids(state) == ["mat-0001"]


def test_task_result_remains_the_semantic_completion_of_criteria():
    completed = task_item(
        status="completed",
        description="Assess architecture maturity",
        completion_criteria=["State what works", "State what blocks long-running reliability"],
        result="Observed strengths and blockers were synthesized into a maturity assessment.",
    )
    state, accepted, rejected = apply_task_updates([completed], previous=[], grounding={})
    assert not rejected and accepted[0]["status"] == "completed"
    view = task_state_view(state)
    assert view["tasks"][0]["completion_criteria"] == completed["completion_criteria"]
    assert view["tasks"][0]["result"] == completed["result"]
    assert view["ready_for_final"] is True


def test_structured_contract_rejects_established_investigation_without_conclusion():
    payload = agent_complete("done", investigation=[investigation_target(
        status="established", grounding_ids=["mat-0001"], conclusion="",
    )])
    with pytest.raises(StructuredResponseError, match="conclusion is required"):
        parse_agent_response(payload)


def test_session_and_queue_advance_for_new_persisted_investigation_shape():
    assert SESSION_SCHEMA_VERSION == "2.7.5-r1.5.1"
    # Queue storage shape did not change in Rev1.5; do not bump persistence schemas cosmetically.
    assert QUEUE_SCHEMA_VERSION == "2.7.5-r1.4.3"
    state = AgentSession("x").to_dict()
    old = copy.deepcopy(state)
    old["session_schema_version"] = "2.7.5-r1.4"
    with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
        AgentSession.from_dict(old)

def test_persisted_session_rejects_investigation_without_conclusion():
    state = AgentSession("x").to_dict()
    state["investigation"] = [{
        "id": "T1", "goal": "x", "status": "open", "grounding_ids": [], "reason": "",
    }]
    with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
        AgentSession.from_dict(state)


def test_prompt_expresses_meaning_without_mandating_investigation_or_task():
    assert "Investigation is an optional Main-owned notebook" in PROMPT_AGENTE
    assert "Task is an optional Main-owned commitment" in PROMPT_AGENTE
    assert "Do not create either merely because the structures exist" in PROMPT_AGENTE
    lower = PROMPT_AGENTE.lower()
    assert "must create an investigation" not in lower
    assert "must create a task" not in lower

def test_main_can_still_answer_directly_without_semantic_commitments(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "executar_agente_llm", lambda prompt, cfg: agent_complete("Olá!"))
    status, text, _, details = run_agent(agent, 
        "oi", base_config(), provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retornar_detalhes=True,
    )
    assert status == "success" and text == "Olá!"
    assert details["investigation"] == [] and details["tasks"] == []


def test_established_conclusion_replaces_raw_grounding_pinning_in_projection():
    session = AgentSession("x")
    session.investigation = [investigation_target(
        goal="Understand X", status="established", grounding_ids=["mat-0001"],
        conclusion="X is established from the inspected material.",
    )]
    assert open_investigation_grounding_ids(session.investigation) == []
    assert established_investigation_grounding_ids(session.investigation) == ["mat-0001"]
