from __future__ import annotations

import json

import eyle.core.agent as agent
from eyle.runtime.ecc_runtime import DispatchOutcome, dispatch
from eyle.runtime.execution_context import ExecutionContext
from eyle.runtime.execution_progress import (
    ExecutionProgress,
    NO_PROGRESS_REPEATS_AFTER_WARNING,
)
from eyle.runtime.memory_graph import apply_graph_operations, world_scope
from eyle.core.session import AgentSession
from tests.canonical import base_config, run_agent, standard_registry


def _provider_context(tmp_path):
    return {
        "standard": {"caminho_origem": str(tmp_path), "eyle_root": str(tmp_path)},
        "core_memory": {
            "storage_dir": str(tmp_path / "memory"),
            "world_scope_id": f"workspace:{tmp_path.resolve()}",
        },
    }


def _explore(name="noop", arguments=None):
    return {
        "type": "explorar",
        "operations": [{"operation": name, "arguments": dict(arguments or {})}],
        "memory_delta": [],
    }


def _conclude(text="ok"):
    return {"type": "concluir", "response": text, "memory_delta": []}



def test_rev375_repeated_valid_fixed_point_terminates_mechanically(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_llm(prompt, cfg):
        calls["n"] += 1
        return _explore("noop")

    monkeypatch.setattr(agent, "executar_ecc_llm", fake_llm)
    monkeypatch.setattr(
        agent,
        "dispatch",
        lambda *args, **kwargs: DispatchOutcome({
            "operation": "noop",
            "status": "success",
            "ok": True,
            "executed": False,
            "changed": False,
            "detail": {"same": True},
        }, physical_progress=False),
    )
    status, _, _, details = run_agent(
        agent, "Olá", base_config(), provider_context=_provider_context(tmp_path),
        retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] == "ECC_NO_PROGRESS_UNRECOVERABLE"
    # First result is new information; then one warned replay; next identical
    # replay proves the deterministic fixed point.
    assert calls["n"] == 1 + NO_PROGRESS_REPEATS_AFTER_WARNING


def test_rev375_alternating_fixed_point_is_bounded_without_turn_ceiling(monkeypatch, tmp_path):
    decisions = iter([
        _explore("a"), _explore("b"),
        _explore("a"), _explore("b"), _explore("a"),
        _conclude("must-not-run"),
    ])
    calls = {"n": 0}

    def fake_llm(prompt, cfg):
        calls["n"] += 1
        return next(decisions)

    monkeypatch.setattr(agent, "executar_ecc_llm", fake_llm)

    def fake_dispatch(session, *, operation, **kwargs):
        return DispatchOutcome({
            "operation": operation,
            "status": "success",
            "ok": True,
            "executed": False,
            "changed": False,
            "detail": {"result": operation},
        }, physical_progress=False)

    monkeypatch.setattr(agent, "dispatch", fake_dispatch)
    status, _, _, details = run_agent(
        agent, "investigue", base_config(), provider_context=_provider_context(tmp_path),
        retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] == "ECC_NO_PROGRESS_UNRECOVERABLE"
    assert calls["n"] == 5


def test_rev375_long_cognition_with_new_runtime_information_is_not_cut(monkeypatch, tmp_path):
    decisions = iter([*[_explore(f"step{i}") for i in range(12)], _conclude("done")])
    calls = {"n": 0}

    def fake_llm(prompt, cfg):
        calls["n"] += 1
        return next(decisions)

    monkeypatch.setattr(agent, "executar_ecc_llm", fake_llm)

    def fake_dispatch(session, *, operation, **kwargs):
        return DispatchOutcome({
            "operation": operation,
            "status": "success",
            "ok": True,
            "executed": False,
            "changed": False,
            "detail": {"new_fact": operation},
        }, physical_progress=False)

    monkeypatch.setattr(agent, "dispatch", fake_dispatch)
    status, text, _, details = run_agent(
        agent, "investigue profundamente", base_config(),
        provider_context=_provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "done")
    assert calls["n"] == 13
    assert "failure_code" not in details


def test_rev375_memory_activate_accepts_domain_and_context_key_at_dispatch_boundary(tmp_path):
    registry = standard_registry()
    cfg = base_config()
    ctx = _provider_context(tmp_path)
    scope = world_scope(ctx["core_memory"]["world_scope_id"])
    apply_graph_operations(ctx["core_memory"]["storage_dir"], [
        {
            "op": "create_node", "id": "mem-chat-a", "scope": scope,
            "domain": "chat", "context_key": "conv-a", "kind": "message",
            "content": "alpha conversation",
        },
        {
            "op": "create_node", "id": "mem-chat-b", "scope": scope,
            "domain": "chat", "context_key": "conv-b", "kind": "message",
            "content": "beta conversation",
        },
    ])
    session = AgentSession("recall", execution_id="exec-1")
    outcome = dispatch(
        session,
        action_kind="explorar",
        operation="memory_activate",
        arguments={"domain": "chat", "context_key": "conv-a", "limit": 30},
        config=cfg,
        provider_context=ctx,
        registry=registry,
        pending_schema_version="test",
        validate_pending=lambda value: value,
    )
    assert outcome.result["ok"] is True
    assert session.memory_view["selector"]["domain"] == "chat"
    assert session.memory_view["selector"]["context_key"] == "conv-a"
    assert session.memory_view["node_ids"] == ["mem-chat-a"]
    # Memory navigation is not mislabeled as physical-world progress.
    assert outcome.physical_progress is False


def test_rev375_episode_tracker_treats_already_observed_as_no_progress_even_first_seen():
    episode = ExecutionProgress()
    state = episode.observe(
        action_signature='{"op":"x"}',
        results=[{"operation": "x", "status": "already_observed", "ok": True}],
        physical_progress=False,
        task_state_progress=False,
        reality_epoch=0,
    )
    assert state.meaningful_progress is False
    assert state.no_progress_repeat_count == 1


def test_rev375_first_cognition_is_telemetried_as_normal_not_continuation(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "executar_ecc_llm", lambda prompt, cfg: _conclude("ok"))
    status, _, _, details = run_agent(
        agent, "Olá", base_config(), provider_context=_provider_context(tmp_path),
        retornar_detalhes=True,
    )
    assert status == "completed"
    calls = details["llm_calls"]
    assert len(calls) == 1
    assert calls[0]["prompt"]["cognition_reason"] == "normal"


def test_rev375_real_progress_resets_fixed_point_episode_without_making_old_results_novel_again():
    episode = ExecutionProgress()

    first = episode.observe(
        action_signature='{"op":"a"}',
        results=[{"operation": "a", "status": "success", "detail": {"value": 1}}],
        physical_progress=False,
        task_state_progress=False,
        reality_epoch=0,
    )
    assert first.meaningful_progress is True

    warned = episode.observe(
        action_signature='{"op":"a"}',
        results=[{"operation": "a", "status": "success", "detail": {"value": 1}}],
        physical_progress=False,
        task_state_progress=False,
        reality_epoch=0,
    )
    assert warned.no_progress_repeat_count == 1

    progress = episode.observe(
        action_signature='{"op":"b"}',
        results=[{"operation": "b", "status": "success", "detail": {"value": 2}}],
        physical_progress=False,
        task_state_progress=False,
        reality_epoch=0,
    )
    assert progress.meaningful_progress is True

    # Returning to the old replay is still not novel, but belongs to the new
    # no-progress episode and therefore starts at repeat 1 rather than leaking
    # the prior fixed-point count across real progress.
    after_progress = episode.observe(
        action_signature='{"op":"a"}',
        results=[{"operation": "a", "status": "success", "detail": {"value": 1}}],
        physical_progress=False,
        task_state_progress=False,
        reality_epoch=0,
    )
    assert after_progress.meaningful_progress is False
    assert after_progress.no_progress_repeat_count == 1
    assert after_progress.terminal is False


def test_rev375_wire_retry_has_distinct_token_telemetry():
    execution = ExecutionContext.from_config(base_config())
    normal = execution.begin_call(mode="ecc", turn=1, prompt={"cognition_reason": "normal"})
    execution.add_attempt(normal, {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110})
    retry = execution.begin_call(mode="ecc", turn=2, prompt={"cognition_reason": "wire_retry"})
    execution.add_attempt(retry, {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128})
    view = execution.usage_view()
    assert view["number_of_wire_retries"] == 1
    assert view["normal_cognition_tokens"] == 110
    assert view["wire_retry_tokens"] == 128
