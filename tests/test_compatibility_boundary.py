from __future__ import annotations

import copy
import json

import pytest

from eyle.core import memory as project_memory
from eyle.core.session import AgentSession, SESSION_SCHEMA_VERSION
from eyle.devtools.benchmark_schema import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkSchemaError,
    validate_report,
)
from eyle.devtools.coverage_compare import _case_ok, _cases
from eyle.devtools.token_efficiency import compare_token_efficiency_reports
from eyle.runtime.config import ConfigError, validar_config
from tests.canonical import base_config


def _benchmark_report():
    usage = {
        "llm_calls": 1,
        "llm_requests": 1,
        "prompt_tokens_effective": 100,
        "completion_tokens_actual": 20,
        "total_tokens_effective": 120,
    }
    case = {
        "id": "greeting",
        "status": "success",
        "response": "hello",
        "tools": [],
        "read_ok": True,
        "factual_ok": True,
        "write_ok": True,
        "confirmation_requested": False,
        "unauthorized_write": False,
        "latency_ms": 10.0,
        "token_usage": usage,
        "failure_code": None,
    }
    metrics = {
        "gate_passed": True,
        "gate_scope": "smoke",
        "total_cases": 1,
        "correct_read_tasks": 1,
        "factual_answers_correct": 1,
        "write_checks_passed": 0,
        "write_checks_total": 0,
        "latency_p50_ms": 10.0,
        "latency_p95_ms": 10.0,
        "latency_p99_ms": 10.0,
    }
    return {
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        "revision": "test-revision",
        "cases": ["greeting"],
        "runs": [{"role": "candidate", "model": "test-model", "cases": [case], "metrics": metrics}],
    }


def test_session_requires_exact_top_level_shape():
    state = AgentSession("x").to_dict()
    assert state["session_schema_version"] == SESSION_SCHEMA_VERSION == "2.7.5-r1.4"

    with_extra = copy.deepcopy(state)
    with_extra["mystery_compat_field"] = True
    with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
        AgentSession.from_dict(with_extra)

    missing = copy.deepcopy(state)
    missing.pop("request")
    with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
        AgentSession.from_dict(missing)

    missing_turn = copy.deepcopy(state)
    missing_turn.pop("turn")
    with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
        AgentSession.from_dict(missing_turn)


def test_session_requires_exact_ledger_envelopes():
    state = AgentSession("x").to_dict()
    state["observation_ledger"]["unknown"] = []
    with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
        AgentSession.from_dict(state)

    state = AgentSession("x").to_dict()
    state["observation_ledger"].pop("pending_results")
    with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
        AgentSession.from_dict(state)


def test_memory_kernel_uses_one_sqlite_schema_and_rejects_legacy_json_shape(tmp_path):
    # Rev1.3.6 is a clean break: the old JSON envelope is no longer a readable
    # memory contract and no compatibility loader remains in Core.
    assert not hasattr(project_memory, "_load")
    assert not hasattr(project_memory, "search_memory")
    assert not hasattr(project_memory, "store_memory")
    assert project_memory.MEMORY_SCHEMA_VERSION == "2.7.5-r1.3.6-memory-kernel-v1"

    legacy = tmp_path / "legacy-memory.json"
    legacy.write_text(json.dumps({"schema_version": "2.7.5-r1.4", "entries": []}), encoding="utf-8")
    # A legacy file has no automatic import/migration path into the new Kernel.
    assert legacy.exists()


def test_sandbox_backend_has_one_english_vocabulary_and_is_validated_early():
    for alias in ("processo", "local_confiavel", "totally_unknown"):
        cfg = base_config()
        cfg["agent"]["sandbox"] = {"backend": alias}
        with pytest.raises(ConfigError, match="backend must be one of"):
            validar_config(cfg)

    for backend in ("auto", "microsandbox", "docker", "bwrap", "process", "trusted_local"):
        cfg = base_config()
        cfg["agent"]["sandbox"] = {"backend": backend}
        assert validar_config(cfg)["agent"]["sandbox"]["backend"] == backend


def test_sandbox_oci_image_is_current_and_docker_name_has_no_alias():
    cfg = base_config()
    cfg["agent"]["sandbox"] = {"backend": "microsandbox", "imagem_oci": "python:3.12-slim"}
    assert validar_config(cfg)["agent"]["sandbox"]["imagem_oci"] == "python:3.12-slim"

    legacy = base_config()
    legacy["agent"]["sandbox"] = {"backend": "docker", "imagem_docker": "python:3.12-slim"}
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD:agent.sandbox:imagem_docker"):
        validar_config(legacy)


def test_benchmark_schema_rejects_language_aliases_and_missing_gates():
    report = _benchmark_report()
    assert validate_report(report) is report
    assert _case_ok(report["runs"][0]["cases"][0]) is True

    portuguese = copy.deepcopy(report)
    run = portuguese["runs"][0]
    run["papel"] = run.pop("role")
    with pytest.raises(BenchmarkSchemaError, match="BENCHMARK_SCHEMA_INVALID"):
        validate_report(portuguese)

    missing_gate = copy.deepcopy(report)
    missing_gate["runs"][0]["cases"][0].pop("read_ok")
    with pytest.raises(BenchmarkSchemaError, match="BENCHMARK_SCHEMA_INVALID"):
        _cases(missing_gate)


def test_benchmark_schema_version_and_token_shape_are_exact():
    report = _benchmark_report()
    wrong = copy.deepcopy(report)
    wrong["benchmark_schema_version"] = "0"
    with pytest.raises(BenchmarkSchemaError, match="BENCHMARK_SCHEMA_INCOMPATIBLE"):
        compare_token_efficiency_reports(wrong, report)

    old_counter_location = copy.deepcopy(report)
    case = old_counter_location["runs"][0]["cases"][0]
    case["prompt_tokens_effective"] = case["token_usage"]["prompt_tokens_effective"]
    with pytest.raises(BenchmarkSchemaError, match="BENCHMARK_SCHEMA_INVALID"):
        compare_token_efficiency_reports(old_counter_location, report)


def test_search_code_backends_share_one_canonical_order_and_truncation(monkeypatch, tmp_path):
    from eyle.core import tools

    (tmp_path / "app.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "docs.md").write_text("needle\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("needle\n", encoding="utf-8")
    files, protected, scope = tools._searchable_files(str(tmp_path))
    assert protected == 0
    assert scope["resolution_complete"] is True

    fallback = tools._search_matches_fallback(str(tmp_path), "needle", files)
    raw = [
        {"file": "tests/test_app.py", "linha": 1, "coluna": 1},
        {"file": "docs.md", "linha": 1, "coluna": 1},
        {"file": "app.py", "linha": 1, "coluna": 1},
    ]
    monkeypatch.setattr(tools, "_run_rg_json_files", lambda root, query, selected: list(raw))
    ripgrep = tools._search_matches_with_rg(str(tmp_path), "needle", files)

    assert ripgrep == fallback
    assert [item["file"] for item in ripgrep] == ["app.py", "docs.md", "tests/test_app.py"]
    # Runtime keeps the complete physical match universe before bounded materialization.


def test_conversation_message_has_one_core_shape():
    import eyle.core.agent as core_agent

    canonical = core_agent._conversation_history({
        "recent_messages": [{"role": "user", "content": "hello"}],
    })
    assert canonical["messages"] == [{"role": "user", "content": "hello"}]

    legacy = core_agent._conversation_history({
        "recent_messages": [{"role": "user", "text": "legacy"}],
    })
    assert legacy["messages"] == []


def test_diagnostic_helpers_are_not_public_main_tools():
    from eyle.core import tools
    assert "agent_info" not in tools.TOOLS
    assert "execution_trace" not in tools.TOOLS

def test_pending_continuation_is_exact_versioned_english_contract():
    from eyle.core.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation

    canonical = {
        "pending_schema_version": PENDING_SCHEMA_VERSION,
        "continuation_kind": "user_input",
        "question": "Which class?",
        "session": {"request": "task"},
        "clarification": {"question": "Which class?", "missing_information": "class name"},
    }
    assert validate_pending_continuation(canonical) is canonical

    legacy = dict(canonical)
    legacy["pergunta_ao_usuario"] = legacy.pop("question")
    with pytest.raises(ValueError, match="PENDING_SCHEMA_INVALID"):
        validate_pending_continuation(legacy)

    missing_version = dict(canonical)
    missing_version.pop("pending_schema_version")
    with pytest.raises(ValueError, match="PENDING_SCHEMA_INCOMPATIBLE"):
        validate_pending_continuation(missing_version)


def test_python_minimum_has_no_pre_38_shlex_or_process_kill_fallbacks():
    from pathlib import Path

    sources = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("eyle/core/editing.py", "eyle/core/sandbox.py", "eyle/runtime/worker.py")
    )
    assert 'hasattr(shlex, "join")' not in sources
    assert 'hasattr(process, "kill")' not in sources


def test_runtime_rejects_and_removes_old_pending_shape(monkeypatch, tmp_path):
    import eyle.runtime.service as service_mod

    path = tmp_path / "agent_pending.json"
    path.write_text(json.dumps({
        "continuation_kind": "user_input",
        "pergunta_ao_usuario": "legacy",
        "estado": {"request": "legacy"},
    }), encoding="utf-8")
    monkeypatch.setattr(service_mod, "AGENT_PENDENTE_PATH", str(path))

    assert service_mod.carregar_agent_pendente() is None
    assert not path.exists()
