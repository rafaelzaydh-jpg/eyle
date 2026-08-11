from __future__ import annotations

import copy

from eyle.core.decision import empty_ledger as empty_decisions, record_rejection, repeated_rejection_count, history_view
from eyle.core.execution_context import ExecutionContext
from eyle.core.observation import record as record_observation, set_pending_results, persisted_view as persisted_observations
from eyle.core.session import AgentSession, SESSION_SCHEMA_VERSION
from eyle.runtime.config import ConfigError, validar_config
from tests.canonical import base_config


def test_session_persists_only_canonical_state_owners():
    state = AgentSession("x").to_dict()
    assert state["session_schema_version"] == SESSION_SCHEMA_VERSION == "5.7.5"
    assert set(state) == {
        "session_schema_version", "request", "task_id", "turn", "workspace_epoch",
        "observation_ledger", "decision_ledger", "evidence_ledger", "investigation",
        "claim_review", "conversation_background", "write_transaction",
    }
    for removed in (
        "tool_history", "latest_tool_results", "decision_history", "prompt_snapshots",
        "llm_responses", "patch_failures", "write_validation", "repeated_rejected_decisions",
    ):
        assert removed not in state


def test_llm_call_ledger_owns_prompt_and_all_provider_attempts_together():
    execution = ExecutionContext.from_config(base_config())
    call = execution.begin_call(mode="agent", turn=3, prompt={"estimated_tokens": 1000})
    execution.add_attempt(call, {"prompt_tokens": 1100, "completion_tokens": 20})
    execution.add_attempt(call, {"prompt_tokens": 1100, "completion_tokens": 24})
    ledger = execution.ledger_view()
    assert len(ledger) == 1
    assert ledger[0]["prompt"]["estimated_tokens"] == 1000
    assert [item["physical_attempt"] for item in ledger[0]["attempts"]] == [1, 2]
    assert execution.llm_request_count == 2


def test_decision_ledger_is_history_and_rejection_identity_at_once():
    ledger = empty_decisions()
    state = {"workspace_epoch": 0}
    first = record_rejection(ledger, turn=1, code="X", payload={"a": 1}, objective_state=state, decision="final", repeated_outcome="stalled")
    second = record_rejection(ledger, turn=2, code="X", payload={"a": 1}, objective_state=state, decision="final", repeated_outcome="stalled")
    assert (first, second) == (1, 2)
    history = history_view(ledger)
    assert [item["outcome"] for item in history] == ["rejected", "stalled"]
    assert repeated_rejection_count(ledger) == 1


def test_observation_persistence_keeps_identity_not_hot_source_or_pending_batch():
    session = AgentSession("x")
    model_result = {
        "tool":"read_file", "status":"success", "ok":True, "executed":True,
        "detail":{"file":"app.py", "numbered_content":"1: SECRET_SOURCE", "content":"SECRET_SOURCE"},
        "evidence_ids":["ev-0001"],
    }
    runtime_result = {
        "status":"success", "ok":True, "executed":True, "changed":False,
        "detail":{"file":"app.py", "line_start":1, "line_end":1, "total_lines":1, "file_hash":"h", "content":"SECRET_SOURCE"},
    }
    record_observation(
        session, "file:app.py:1:1", "read_file", {"path":"app.py","line_start":1,"line_end":1},
        runtime_result, model_result,
        public_arguments={"path":"app.py","line_start":1,"line_end":1},
        public_result={"status":"success","ok":True,"executed":True,"file":"app.py","lines":[1,1]},
    )
    set_pending_results(session, [model_result])
    persisted = persisted_observations(session.observation_ledger)
    serialized = repr(persisted)
    assert "SECRET_SOURCE" not in serialized
    assert persisted["pending_results"] == []
    assert persisted["entries"]
    assert persisted["events"][0]["evidence_ids"] == ["ev-0001"]


def test_execution_context_does_not_mutate_configuration_into_runtime_state():
    cfg = base_config()
    before = copy.deepcopy(cfg)
    ExecutionContext.from_config(cfg, task_id="t1", source_job_id=9)
    assert cfg == before
    assert "_runtime_agent_budget" not in cfg


def test_removed_runtime_budget_is_not_a_hidden_compatibility_key():
    cfg = base_config()
    cfg["_runtime_agent_budget"] = {}
    try:
        validar_config(cfg)
    except ConfigError as error:
        assert "UNKNOWN_CONFIG_FIELD:root:_runtime_agent_budget" in str(error)
    else:
        raise AssertionError("legacy runtime budget must be rejected")
