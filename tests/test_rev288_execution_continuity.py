from __future__ import annotations

import copy
import time
from pathlib import Path

import eyle.core.agent as agent
import llm.executar as llm_mod
from eyle.runtime.execution_context import ExecutionContext
from eyle.runtime.execution_context import (
    EXECUTION_CONTINUITY_SCHEMA_VERSION,
    current_execution,
    validate_execution_continuity_state,
)
from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation
from eyle.core.memory import apply_memory_sidecar, memory_activate_result, release_memory_navigation
from eyle.core.session import AgentSession
from eyle.runtime.memory_graph import memory_db_path
from tests.canonical import standard_registry
import sqlite3
from tests.canonical import base_config, run_agent
from tests.test_ecc_rev21_audit import build, conclude, explore, provider_context


def test_rev288_execution_state_roundtrip_preserves_fuse_deadline_and_usage():
    cfg = base_config()
    cfg["llm"]["generated_token_fuse"] = 123
    cfg["agent"]["task_deadline_seconds"] = 90
    ctx = ExecutionContext.from_config(cfg, execution_id="logical-1", source_job_id=10)
    ctx.completion_tokens_actual = 77
    ctx.reasoning_tokens_actual = 55
    ctx.prompt_tokens_actual = 900
    ctx.prompt_tokens_cached = 400
    ctx.prompt_tokens_uncached = 500
    ctx.agent_turns = 4
    ctx.bind_canonical_request("same request")
    before_deadline = ctx.deadline_wall_time
    state = ctx.continuation_state()
    validate_execution_continuity_state(state)

    # Changing operator config while waiting cannot silently buy a new fuse or deadline.
    changed = copy.deepcopy(cfg)
    changed["llm"]["generated_token_fuse"] = 999999
    changed["agent"]["task_deadline_seconds"] = 999999
    restored = ExecutionContext.from_continuation_state(changed, state, source_job_id=11)
    assert restored.execution_id == "logical-1"
    assert restored.source_job_id == 11
    assert restored.generated_token_limit == 123
    assert restored.completion_tokens_actual == 77
    assert restored.generated_tokens_remaining == 46
    assert restored.reasoning_tokens_actual == 55
    assert restored.deadline_wall_time == before_deadline
    assert restored.resume_count == 1
    assert restored.canonical_request_hash == ctx.canonical_request_hash


def test_rev288_confirmation_resume_keeps_same_logical_execution_and_token_accounting(monkeypatch, tmp_path):
    target = tmp_path / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")
    cfg = base_config()
    cfg["llm"]["generated_token_fuse"] = 100
    cfg["agent"]["task_deadline_seconds"] = 120
    seen = []

    def fake(prompt, config):
        execution = current_execution()
        assert execution is not None
        seen.append((execution.execution_id, execution.source_job_id, execution.completion_tokens_actual, execution.resume_count))
        if len(seen) == 1:
            execution.completion_tokens_actual += 30
            return explore("read_file", {"source": "workspace", "path": "app.py", "line_start": 1, "line_end": 1})
        if len(seen) == 2:
            execution.completion_tokens_actual += 40
            return build("transaction", {"patches": [{"operation": "update", "path": "app.py", "line_start": 1, "line_end": 1, "new_code": "x = 2\n"}]})
        # Resume must continue at 70/100 even though a different physical job id
        # and a changed config are supplied.
        assert execution.generated_token_limit == 100
        assert execution.completion_tokens_actual == 70
        assert execution.generated_tokens_remaining == 30
        assert execution.resume_count == 1
        execution.completion_tokens_actual += 5
        return conclude("done")

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, _, pending, details = run_agent(
        agent, "mude x", cfg, provider_context=provider_context(tmp_path),
        retornar_detalhes=True, execution_id="job-1", source_job_id=1,
    )
    assert status == "confirmation_required"
    assert pending["pending_schema_version"] == PENDING_SCHEMA_VERSION == "11-ecc"
    validate_pending_continuation(pending)
    assert pending["execution_state"]["completion_tokens_actual"] == 70
    assert pending["execution_state"]["generated_token_limit"] == 100
    original_deadline = pending["execution_state"]["deadline_wall_time"]
    assert target.read_text(encoding="utf-8") == "x = 1\n"

    cfg_changed = copy.deepcopy(cfg)
    cfg_changed["llm"]["generated_token_fuse"] = 1000
    cfg_changed["agent"]["task_deadline_seconds"] = 3600
    status2, text2, pending2, details2 = run_agent(
        agent, "mude x", cfg_changed, provider_context=provider_context(tmp_path),
        retomar=pending, resposta_usuario="sim", retornar_detalhes=True,
        execution_id="job-2", source_job_id=2,
    )
    assert (status2, text2, pending2) == ("completed", "done", None)
    assert target.read_text(encoding="utf-8") == "x = 2\n"
    assert details2["execution_id"] == "job-1"
    assert details2["llm_usage"]["generated_token_limit"] == 100
    assert details2["llm_usage"]["generated_tokens"] == 75
    assert details2["llm_usage"]["execution_resume_count"] == 1
    assert seen[2][0] == "job-1" and seen[2][1] == 2
    # Same absolute deadline survived; usage_view only exposes remaining time.
    assert original_deadline == pending["execution_state"]["deadline_wall_time"]


def test_rev288_expired_logical_deadline_blocks_confirmation_before_write(monkeypatch, tmp_path):
    target = tmp_path / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")
    cfg = base_config()
    outputs = iter([
        explore("read_file", {"source": "workspace", "path": "app.py", "line_start": 1, "line_end": 1}),
        build("transaction", {"patches": [{"operation": "update", "path": "app.py", "line_start": 1, "line_end": 1, "new_code": "x = 2\n"}]}),
    ])
    monkeypatch.setattr(agent, "executar_ecc_llm", lambda prompt, config: next(outputs))
    status, _, pending, _ = run_agent(
        agent, "mude x", cfg, provider_context=provider_context(tmp_path),
        retornar_detalhes=True, execution_id="logical-deadline", source_job_id=1,
    )
    assert status == "confirmation_required"
    # Simulate the human waiting beyond the original logical deadline while
    # keeping the persisted state structurally valid.
    now = time.time()
    pending = copy.deepcopy(pending)
    pending["execution_state"]["started_wall_time"] = now - 20
    pending["execution_state"]["deadline_wall_time"] = now - 1
    validate_pending_continuation(pending)

    status2, _, pending2, details2 = run_agent(
        agent, "mude x", cfg, provider_context=provider_context(tmp_path),
        retomar=pending, resposta_usuario="sim", retornar_detalhes=True,
        execution_id="new-physical-job", source_job_id=2,
    )
    assert status2 == "failed"
    assert pending2 is None
    assert details2["failure_code"] == "TASK_DEADLINE_EXCEEDED"
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_rev288_pending_execution_state_is_mandatory_and_fail_closed():
    ctx = ExecutionContext.from_config(base_config(), execution_id="logical")
    pending = {
        "pending_schema_version": PENDING_SCHEMA_VERSION,
        "continuation_kind": "capability_confirmation",
        "question": "confirm?",
        "session": {"request": "x"},
        "execution_state": ctx.continuation_state(),
        "capability": "standard.workspace_transaction",
        "provider": "standard",
        "confirmation_id": "ecc-cap-0001",
    }
    validate_pending_continuation(pending)
    bad = dict(pending)
    bad.pop("execution_state")
    try:
        validate_pending_continuation(bad)
    except ValueError as exc:
        assert str(exc) == "PENDING_SCHEMA_INVALID"
    else:
        raise AssertionError("execution continuity must be mandatory")


def test_rev288_terminal_memory_navigation_cleanup_releases_abandoned_db_snapshot(tmp_path):
    context = provider_context(tmp_path)
    registry = standard_registry()
    seed = AgentSession("seed")
    delta = [
        {
            "op": "remember", "scope": "world", "retention": "persistent",
            "kind": "observation", "content": f"cleanup target {i}",
            "epistemic": {"nature": "observation", "confidence": 0.8, "volatility": "low"},
        }
        for i in range(40)
    ]
    assert apply_memory_sidecar(seed, delta, registry=registry, provider_context=context)["ok"] is True
    session = AgentSession("recall")
    first = memory_activate_result(
        session, arguments={"query": "cleanup", "limit": 5}, registry=registry,
        config=base_config(), provider_context=context,
    )
    assert first["ok"] is True and first.get("frontiers")
    storage = context["core_memory"]["storage_dir"]
    conn = sqlite3.connect(memory_db_path(storage))
    try:
        assert conn.execute("SELECT COUNT(*) FROM memory_recall_snapshots").fetchone()[0] == 1
    finally:
        conn.close()
    cleaned = release_memory_navigation(session, context)
    assert cleaned["released"] == 1
    conn = sqlite3.connect(memory_db_path(storage))
    try:
        assert conn.execute("SELECT COUNT(*) FROM memory_recall_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM memory_recall_items").fetchone()[0] == 0
    finally:
        conn.close()


def test_rev288_confirmation_pending_keeps_memory_cursor_until_logical_task_terminates(monkeypatch, tmp_path):
    # This property is enforced by lifecycle placement: cleanup is terminal-only.
    # Prove the helper itself is not called when the agent returns confirmation_required.
    target = tmp_path / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")
    cfg = base_config()
    calls = {"cleanup": 0}
    original_cleanup = agent.release_memory_navigation
    monkeypatch.setattr(agent, "release_memory_navigation", lambda session, ctx: calls.__setitem__("cleanup", calls["cleanup"] + 1) or {"released": 0})
    outputs = iter([
        explore("read_file", {"source": "workspace", "path": "app.py", "line_start": 1, "line_end": 1}),
        build("transaction", {"patches": [{"operation": "update", "path": "app.py", "line_start": 1, "line_end": 1, "new_code": "x = 2\n"}]}),
    ])
    monkeypatch.setattr(agent, "executar_ecc_llm", lambda prompt, config: next(outputs))
    status, _, pending, _ = run_agent(
        agent, "mude x", cfg, provider_context=provider_context(tmp_path),
        retornar_detalhes=True, execution_id="pending-cleanup", source_job_id=1,
    )
    assert status == "confirmation_required" and pending is not None
    assert calls["cleanup"] == 0
    monkeypatch.setattr(agent, "release_memory_navigation", original_cleanup)


def test_rev288_accepts_clean_rev287_config_identity():
    from eyle.runtime.config import validar_config
    config = base_config()
    config["config_schema_version"] = "2.7.5-r2.8.7-ecc"
    config["revision"] = "rev2.8.7-ecc"
    validated = validar_config(copy.deepcopy(config), standard_registry())
    assert validated["config_schema_version"] == "2.7.5-r3-ecc"
    assert validated["revision"] == "rev3-ecc"


def test_rev288_config_closes_direct_provider_bypass():
    from eyle.runtime.config import ConfigError, validar_config
    for bad in (
        "https://api.deepseek.com/v1",
        "http://127.0.0.1:8000/v1",
        "http://localhost:11434",
    ):
        config = base_config()
        config["llm"]["base_url"] = bad
        try:
            validar_config(config, standard_registry())
        except ConfigError as exc:
            assert "Adapter local" in str(exc)
        else:
            raise AssertionError(f"direct/provider bypass accepted: {bad}")
    for good in ("http://127.0.0.1:8080", "http://localhost:8080/v1", "http://[::1]:8080"):
        config = base_config()
        config["llm"]["base_url"] = good
        assert validar_config(config, standard_registry())["llm"]["base_url"] == good
