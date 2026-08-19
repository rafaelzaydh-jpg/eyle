from __future__ import annotations

from tests.canonical import adapt_legacy_ecc_script
import copy

import eyle.core.agent as agent
from eyle.core.session import AgentSession
from eyle.runtime.continuation import validate_pending_continuation
from eyle.runtime.ecc_runtime import DispatchOutcome
from eyle.runtime.execution_context import ExecutionContext
from eyle.runtime.execution_progress import ExecutionProgress
from eyle.runtime.observation import mechanical_coverage_state, set_pending_results
from tests.canonical import base_config, run_agent


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


def test_rev377_execution_progress_roundtrip_preserves_blocks_and_mechanical_signals():
    tracker = ExecutionProgress()
    first = tracker.observe(
        action_signature="a", results=[{"status": "success", "detail": {"x": 1}}],
        physical_progress=False, task_state_progress=False, reality_epoch=0,
        operation_count=3, provider_tokens_total=1000, coverage_advanced=True,
        physical_mutations=0,
    )
    assert first.meaningful_progress is True
    second = tracker.observe(
        action_signature="a", results=[{"status": "success", "detail": {"x": 1}}],
        physical_progress=False, task_state_progress=False, reality_epoch=0,
        operation_count=1, provider_tokens_total=1400, coverage_advanced=False,
        physical_mutations=0,
    )
    assert second.meaningful_progress is False
    assert tracker.is_blocked("a", 0) is True

    restored = ExecutionProgress.from_dict(tracker.to_dict())
    assert restored.is_blocked("a", 0) is True
    assert restored.convergence_view(1500) == {
        "operations_since_task_state_progress": 4,
        "provider_tokens_since_task_state_progress": 1500,
        "fixed_points_blocked": 1,
        "coverage_advanced": True,
        "physical_mutations": 0,
    }


def test_rev377_checkpoint_session_preserves_hot_pending_delta():
    session = AgentSession("inspect")
    set_pending_results(session, [{
        "operation": "read_file",
        "status": "success",
        "detail": {"content": "hot observation"},
        "frontiers": [{"id": "fr-0001", "status": "open"}],
    }])
    ordinary = session.to_dict()
    checkpoint = session.to_checkpoint_dict()
    assert ordinary["observation_ledger"]["pending_results"] == []
    assert checkpoint["observation_ledger"]["pending_results"][0]["detail"]["content"] == "hot observation"

    restored = AgentSession.from_dict(checkpoint)
    assert restored.observation_ledger["pending_results"][0]["frontiers"][0]["id"] == "fr-0001"


def test_rev377_fixed_point_checkpoint_rehydrates_block_and_continues(monkeypatch, tmp_path):
    decisions = iter([
        _explore("a"),
        _explore("a"),  # proves fixed point -> persisted checkpoint
        _explore("a"),  # after resume, must be mechanically blocked
        _explore("b"),
        _conclude("done"),
    ])
    dispatched = []

    monkeypatch.setattr(agent, "_call_surface_llm", adapt_legacy_ecc_script(lambda prompt, cfg: next(decisions)))
    def fake_dispatch(session, *, operation, **kwargs):
        dispatched.append(operation)
        return DispatchOutcome({
            "operation": operation,
            "status": "success",
            "ok": True,
            "executed": False,
            "changed": False,
            "detail": {"result": operation},
        }, physical_progress=False)

    monkeypatch.setattr(agent, "dispatch", fake_dispatch)

    status, _, pending, _ = run_agent(
        agent, "investigate", base_config(), provider_context=_provider_context(tmp_path),
        execution_id="job-377", retornar_detalhes=True,
    )
    assert status == "recoverable_checkpoint"
    assert pending["continuation_kind"] == "recoverable_execution"
    assert pending["checkpoint_reason"] == "stalled_recoverable"
    validate_pending_continuation(pending)

    restored_session = AgentSession.from_dict(pending["session"])
    restored_progress = ExecutionProgress.from_dict(restored_session.execution_progress)
    signature = next(iter(restored_progress.blocked_actions))
    assert restored_progress.is_blocked(signature, restored_session.reality_epoch)

    status, text, _, details = run_agent(
        agent, "ignored-on-resume", base_config(), provider_context=_provider_context(tmp_path),
        execution_id="job-377", retomar=pending, retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "done")
    # Third "a" was rejected from the restored fixed-point state.
    assert dispatched == ["a", "a", "b"]
    assert details["llm_usage"]["execution_resume_count"] == 1


def test_rev377_budget_salvage_checkpoints_once_then_resumes(monkeypatch, tmp_path):
    decisions = iter([_explore("step"), _conclude("salvaged")])

    def fake_llm(prompt, cfg):
        from eyle.runtime.execution_context import current_execution
        execution = current_execution()
        if execution.provider_total_tokens_actual == 0:
            execution.provider_total_tokens_actual = 130000
        return next(decisions)

    monkeypatch.setattr(agent, "_call_surface_llm", adapt_legacy_ecc_script(fake_llm))
    monkeypatch.setattr(
        agent, "dispatch",
        lambda *args, **kwargs: DispatchOutcome({
            "operation": "step", "status": "success", "ok": True,
            "executed": False, "changed": False, "detail": {"fact": 1},
        }, physical_progress=False),
    )

    status, _, pending, _ = run_agent(
        agent, "investigate", base_config(), provider_context=_provider_context(tmp_path),
        execution_id="job-budget", retornar_detalhes=True,
    )
    assert status == "recoverable_checkpoint"
    assert pending["checkpoint_reason"] == "budget_salvage"
    assert pending["execution_state"]["salvage_checkpoint_emitted"] is True

    status, text, _, details = run_agent(
        agent, "ignored", base_config(), provider_context=_provider_context(tmp_path),
        execution_id="job-budget", retomar=pending, retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "salvaged")
    assert details["llm_usage"]["execution_resume_count"] == 1


def test_rev377_mechanical_file_coverage_merges_exact_intervals_without_semantic_judgment():
    session = AgentSession("read")
    session.observation_ledger["entries"] = {
        "w0:a": {
            "reality_epoch": 0, "turn": 1, "capability": "standard.read_file",
            "frontier_ids": ["fr-0001"],
            "coverage": {
                "scope": {"kind": "file", "source": "workspace", "path": "main.py"},
                "examined": {"line_start": 1, "line_end": 400, "total_lines": 1000},
                "complete": False, "boundaries": [],
            },
        },
        "w0:b": {
            "reality_epoch": 0, "turn": 2, "capability": "standard.continue_observation",
            "frontier_ids": [],
            "coverage": {
                "scope": {"kind": "file", "source": "workspace", "path": "main.py"},
                "examined": {"line_start": 401, "line_end": 800, "total_lines": 1000},
                "complete": False, "boundaries": [],
            },
        },
    }
    session.observation_ledger["frontiers"] = {
        "fr-0001": {
            "id": "fr-0001", "status": "open", "reality_epoch": 0,
            "source_capability": "read_file", "kind": "material_continuation",
        }
    }
    view = mechanical_coverage_state(session)
    file_state = view["files"][0]
    assert file_state["materialized_ranges"] == [[1, 800]]
    assert file_state["materialized_lines"] == 800
    assert file_state["remaining_lines"] == 200
    assert file_state["complete_file_coverage"] is False
    assert file_state["frontier_ids"] == ["fr-0001"]
