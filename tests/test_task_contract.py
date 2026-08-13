from __future__ import annotations

import json

import eyle.core.agent as core_agent
from eyle.core.tasks import apply_task_updates, task_state_view
from tests.canonical import agent_final, agent_tools, base_config, task_item, tool_call


def test_task_is_durable_recursive_main_owned_state():
    state, accepted, rejected = apply_task_updates([
        task_item("root", description="Build the feature"),
        task_item("child", parent_id="root", description="Verify the feature"),
    ])
    assert not rejected
    assert [item["id"] for item in state] == ["root", "child"]
    assert all(item["changed"] is True for item in accepted)

    same, accepted2, rejected2 = apply_task_updates([], previous=state)
    assert same == state
    assert accepted2 == [] and rejected2 == []

    updated, accepted3, rejected3 = apply_task_updates([
        task_item(
            "child", parent_id="root", description="Verify the feature",
            status="completed", result="Verification succeeded.",
        )
    ], previous=state)
    assert not rejected3 and accepted3[0]["changed"] is True
    assert updated[1]["status"] == "completed"
    assert updated[1]["result"] == "Verification succeeded."


def test_parent_may_be_created_after_child_in_same_batch():
    state, accepted, rejected = apply_task_updates([
        task_item("child", parent_id="root", description="Child"),
        task_item("root", description="Root"),
    ])
    assert not rejected
    assert {item["id"] for item in accepted} == {"root", "child"}
    assert {item["id"] for item in state} == {"root", "child"}


def test_unknown_parent_self_parent_and_cycles_are_rejected_structurally():
    state, _, rejected = apply_task_updates([
        task_item("orphan", parent_id="missing", description="Orphan"),
    ])
    assert state == []
    assert rejected[0]["reason"].startswith("TASK_PARENT_UNKNOWN")

    state, _, rejected = apply_task_updates([
        task_item("self", parent_id="self", description="Self")
    ])
    assert state == []
    assert rejected[0]["reason"].startswith("TASK_PARENT_SELF_REFERENCE")

    state, _, rejected = apply_task_updates([
        task_item("a", parent_id="b", description="A"),
        task_item("b", parent_id="a", description="B"),
    ])
    assert state == []
    assert {item["id"] for item in rejected} == {"a", "b"}
    assert all(item["reason"].startswith("TASK_PARENT_CYCLE") for item in rejected)


def test_closed_task_requires_result_but_runtime_does_not_auto_close_parent():
    state, _, rejected = apply_task_updates([
        task_item("root", description="Root"),
        task_item("child", parent_id="root", description="Child", status="completed", result="Done."),
    ])
    assert not rejected
    view = task_state_view(state)
    assert view["open_count"] == 1
    assert view["closed_count"] == 1
    assert view["ready_for_final"] is False
    assert state[0]["status"] == "open"

    unchanged, accepted, rejected = apply_task_updates([
        task_item("root", description="Root", status="completed", result="")
    ], previous=state)
    assert unchanged == state
    assert accepted == []
    assert rejected[0]["reason"].startswith("TASK_CLOSED_RESULT_REQUIRED")


def test_open_tasks_block_final_until_main_closes_the_commitment(monkeypatch, tmp_path):
    calls = {"n": 0}
    def fake_call(session, config, project, conversation_context, feedback=""):
        calls["n"] += 1
        if calls["n"] == 1:
            return agent_final("too early", tasks=[task_item("work", description="Required work")]), set()
        assert "FINAL_COMMITMENTS_OPEN" in feedback
        return agent_final(
            "done",
            tasks=[task_item("work", description="Required work", status="completed", result="Completed")],
        ), set()
    monkeypatch.setattr(core_agent, "_call_agent", fake_call)
    status, text, _, details = core_agent.executar_agente(
        "Answer after the required work.", base_config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success" and text == "done"
    assert calls["n"] == 2
    assert details["tasks"][0]["status"] == "completed"
    assert any(
        item.get("decision") == "final" and item.get("outcome") == "rejected"
        and item.get("reason") == "FINAL_COMMITMENTS_OPEN"
        for item in details["decision_history"]
    )


def test_task_state_is_visible_next_turn_and_main_can_close_it(monkeypatch, tmp_path):
    prompts = []

    def fake_agent(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return agent_tools(
                tool_call("calculate", {"expression": "1+1"}),
                tasks=[
                    task_item("root", description="Answer the request"),
                    task_item("check", parent_id="root", description="Check arithmetic"),
                ],
            )
        assert payload["task_state"]["open_count"] == 2
        assert {item["id"] for item in payload["task_state"]["tasks"]} == {"root", "check"}
        return agent_final(
            "2",
            tasks=[
                task_item("check", parent_id="root", description="Check arithmetic", status="completed", result="1+1 evaluated to 2."),
                task_item("root", description="Answer the request", status="completed", result="Answered with the verified result 2."),
            ],
        )

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    status, text, _, details = core_agent.executar_agente(
        "What is 1+1?",
        base_config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success" and text == "2"
    assert len(prompts) == 2
    assert all(item["status"] == "completed" for item in details["tasks"])
    task_events = [item for item in details["decision_history"] if item.get("decision") == "task_update"]
    assert task_events
    assert any((item.get("facts") or {}).get("result") == "1+1 evaluated to 2." for item in task_events)


def test_persisted_task_state_is_strict_and_old_physical_task_id_shape_is_not_accepted():
    from eyle.core.session import AgentSession

    canonical = AgentSession("x", execution_id="job-1").to_dict()
    malformed = dict(canonical)
    malformed["tasks"] = [{
        "id": "bad id", "parent_id": None, "description": "Bad",
        "status": "open", "result": "",
    }]
    try:
        AgentSession.from_dict(malformed)
    except ValueError as error:
        assert str(error) == "SESSION_SCHEMA_INCOMPATIBLE"
    else:
        raise AssertionError("malformed persisted Task must be rejected")

    old = dict(canonical)
    old["task_id"] = old.pop("execution_id")
    old.pop("tasks")
    try:
        AgentSession.from_dict(old)
    except ValueError as error:
        assert str(error) == "SESSION_SCHEMA_INCOMPATIBLE"
    else:
        raise AssertionError("Rev1.2.x session shape must not be migrated")
