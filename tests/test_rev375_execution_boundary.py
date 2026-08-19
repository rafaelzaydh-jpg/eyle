from __future__ import annotations

from tests.canonical import adapt_legacy_ecc_script
import json

import eyle.core.agent as agent
from eyle.runtime.ecc_runtime import DispatchOutcome, dispatch, _compact_cached
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



def test_rev376_repeated_valid_fixed_point_is_blocked_and_task_can_recover(monkeypatch, tmp_path):
    decisions = iter([
        _explore("noop"),
        _explore("noop"),
        _explore("noop"),
        _conclude("recovered"),
    ])
    calls = {"llm": 0, "dispatch": 0}

    def fake_llm(prompt, cfg):
        calls["llm"] += 1
        return next(decisions)

    def fake_dispatch(*args, **kwargs):
        calls["dispatch"] += 1
        return DispatchOutcome({
            "operation": "noop",
            "status": "success",
            "ok": True,
            "executed": False,
            "changed": False,
            "detail": {"same": True},
        }, physical_progress=False)

    monkeypatch.setattr(agent, "_call_surface_llm", adapt_legacy_ecc_script(fake_llm))
    monkeypatch.setattr(agent, "dispatch", fake_dispatch)
    status, text, _, details = run_agent(
        agent, "Olá", base_config(), provider_context=_provider_context(tmp_path),
        retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "recovered")
    # First call is novel, second proves no-progress and blocks the signature.
    # The third identical decision is rejected mechanically without dispatch.
    assert calls == {"llm": 4, "dispatch": 2}
    assert "failure_code" not in details



def test_rev376_blocked_fixed_point_does_not_block_alternative_path(monkeypatch, tmp_path):
    decisions = iter([
        _explore("a"),
        _explore("a"),
        _explore("a"),  # blocked, no dispatch
        _explore("b"),  # genuinely new path clears recovery episode
        _conclude("done"),
    ])
    calls = {"llm": 0, "dispatch": []}

    def fake_llm(prompt, cfg):
        calls["llm"] += 1
        return next(decisions)

    def fake_dispatch(session, *, operation, **kwargs):
        calls["dispatch"].append(operation)
        return DispatchOutcome({
            "operation": operation,
            "status": "success",
            "ok": True,
            "executed": False,
            "changed": False,
            "detail": {"result": operation},
        }, physical_progress=False)

    monkeypatch.setattr(agent, "_call_surface_llm", adapt_legacy_ecc_script(fake_llm))
    monkeypatch.setattr(agent, "dispatch", fake_dispatch)
    status, text, _, details = run_agent(
        agent, "investigue", base_config(), provider_context=_provider_context(tmp_path),
        retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "done")
    assert calls["dispatch"] == ["a", "a", "b"]
    assert "failure_code" not in details



def test_rev375_long_cognition_with_new_runtime_information_is_not_cut(monkeypatch, tmp_path):
    decisions = iter([*[_explore(f"step{i}") for i in range(12)], _conclude("done")])
    calls = {"n": 0}

    def fake_llm(prompt, cfg):
        calls["n"] += 1
        return next(decisions)

    monkeypatch.setattr(agent, "_call_surface_llm", adapt_legacy_ecc_script(fake_llm))

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
    monkeypatch.setattr(agent, "_call_surface_llm", adapt_legacy_ecc_script(lambda prompt, cfg: _conclude("ok")))
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


def test_rev376_compact_replay_preserves_open_frontier_and_evidence_coordinates():
    session = AgentSession("read a large file", execution_id="exec-replay")
    session.observation_ledger["frontiers"]["fr-0001"] = {
        "id": "fr-0001",
        "handle": "h-1",
        "reality_epoch": 0,
        "source_capability": "standard.read_file",
        "kind": "file_page",
        "count": 2,
        "status": "open",
    }
    session.observation_ledger["frontiers"]["fr-0002"] = {
        "id": "fr-0002",
        "handle": "h-2",
        "reality_epoch": 0,
        "source_capability": "standard.read_file",
        "kind": "file_page",
        "count": 1,
        "status": "consumed",
    }
    entry = {
        "grounding_ids": ["mat-0001"],
        "frontier_ids": ["fr-0001", "fr-0002"],
        "coverage": {"path": "main.py", "line_start": 1, "line_end": 400},
        "turn": 1,
        "replay_result": {
            "evidence_ids": ["ev-0001"],
            "frontiers": [{"id": "fr-0001"}, {"id": "fr-0002"}],
        },
    }
    replay = _compact_cached(session, entry, "read_file")
    assert replay["status"] == "already_observed"
    assert replay["evidence_ids"] == ["ev-0001"]
    assert [item["id"] for item in replay["frontiers"]] == ["fr-0001"]
    assert "continue" in replay["message"]


def test_rev376_progress_blocks_local_action_without_terminal_task_failure():
    episode = ExecutionProgress()
    first = episode.observe(
        action_signature='{"op":"x"}',
        results=[{"operation": "x", "status": "already_observed", "ok": True}],
        physical_progress=False,
        task_state_progress=False,
        reality_epoch=0,
    )
    assert first.meaningful_progress is False
    assert first.terminal is False
    assert episode.is_blocked('{"op":"x"}', 0) is True

    blocked = episode.observe(
        action_signature='{"op":"x"}',
        results=[{"operation": "x", "status": "recovery_required", "ok": True}],
        physical_progress=False,
        task_state_progress=False,
        reality_epoch=0,
    )
    assert blocked.meaningful_progress is False
    assert blocked.terminal is False

    progress = episode.observe(
        action_signature='{"op":"y"}',
        results=[{"operation": "y", "status": "success", "detail": {"new": 1}}],
        physical_progress=False,
        task_state_progress=False,
        reality_epoch=0,
    )
    assert progress.meaningful_progress is True
    assert episode.is_blocked('{"op":"x"}', 0) is False


def test_rev376_budget_salvage_is_injected_before_budget_is_exhausted(monkeypatch, tmp_path):
    decisions = iter([_explore("step"), _conclude("salvaged")])
    seen_feedback = []

    def fake_llm(prompt, cfg):
        from eyle.runtime.execution_context import current_execution
        seen_feedback.append(list((prompt.dynamic or {}).get("runtime_feedback") or []))
        execution = current_execution()
        if len(seen_feedback) == 1:
            # Simulate provider usage after the first cognition so the next turn
            # enters the final 15% while still leaving room to conclude.
            execution.provider_total_tokens_actual = 130000
        return next(decisions)

    monkeypatch.setattr(agent, "_call_surface_llm", adapt_legacy_ecc_script(fake_llm))
    monkeypatch.setattr(
        agent, "dispatch",
        lambda *args, **kwargs: DispatchOutcome({
            "operation": "step",
            "status": "success",
            "ok": True,
            "executed": False,
            "changed": False,
            "detail": {"fact": 1},
        }, physical_progress=False),
    )
    status, text, _, _ = run_agent(
        agent, "investigue", base_config(), provider_context=_provider_context(tmp_path),
        retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "salvaged")
    assert any(item.get("code") == "BUDGET_SALVAGE" for item in seen_feedback[1])
