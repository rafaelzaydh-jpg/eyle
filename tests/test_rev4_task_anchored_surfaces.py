from __future__ import annotations

import copy

import eyle.core.agent as agent
from eyle.core.memory import apply_memory_sidecar, apply_task_binding, materialize_active_task
from eyle.core.session import AgentSession, SESSION_SCHEMA_VERSION
from eyle.runtime.ecc_runtime import DispatchOutcome
from llm.structured import (
    StructuredResponseError,
    parse_profile_response,
    wire_schema_for_profile,
)
from tests.canonical import base_config, run_agent, standard_registry


def _pc(tmp_path):
    return {
        "standard": {"caminho_origem": str(tmp_path), "eyle_root": str(tmp_path)},
        "core_memory": {
            "storage_dir": str(tmp_path / "memory"),
            "world_scope_id": f"workspace:{tmp_path.resolve()}",
        },
    }


def test_rev4_session_persists_active_task_and_protocol_surface():
    session = AgentSession("x", execution_id="job-4")
    session.active_task_id = "mem-task"
    session.cognitive_surface = "explore"
    state = session.to_checkpoint_dict()
    assert state["session_schema_version"] == SESSION_SCHEMA_VERSION == "2.7.5-r4.0.0-ecc"
    restored = AgentSession.from_dict(state)
    assert restored.active_task_id == "mem-task"
    assert restored.cognitive_surface == "explore"


def test_rev4_task_binding_is_exact_main_authored_and_projection_is_compact(tmp_path):
    reg = standard_registry()
    ctx = _pc(tmp_path)
    session = AgentSession("fix auth")
    made = apply_memory_sidecar(session, [{
        "op": "remember", "key": "task", "scope": "world", "retention": "persistent",
        "kind": "task", "content": "Corrigir autenticação sem alterar API pública.",
        "supports": [{"kind": "request"}],
    }], registry=reg, provider_context=ctx)
    task_id = made["aliases"]["task"]
    bound = apply_task_binding(session, {"action": "bind", "ref": "@task"}, aliases=made["aliases"], provider_context=ctx)
    assert bound["ok"] and session.active_task_id == task_id

    view = materialize_active_task(session, ctx)
    assert set(view) == {"id", "available", "revision", "state", "state_revision", "content"}
    assert view["id"] == task_id and view["state"] == "active"
    assert "edges" not in view and "relations" not in view


def test_rev4_runtime_never_auto_selects_task(tmp_path):
    reg = standard_registry()
    ctx = _pc(tmp_path)
    seed = AgentSession("seed")
    apply_memory_sidecar(seed, [{
        "op": "remember", "scope": "world", "retention": "persistent",
        "kind": "task", "content": "Uma task existente.", "supports": [{"kind": "request"}],
    }], registry=reg, provider_context=ctx)
    fresh = AgentSession("request parecido")
    assert fresh.active_task_id is None
    assert materialize_active_task(fresh, ctx) == {}


def test_rev4_task_binding_sidecar_failure_does_not_veto_navigation():
    parsed = parse_profile_response({
        "type": "explorar",
        "memory_delta": [],
        "task_binding": {"action": "bind", "ref": "not-a-memory-id"},
    }, "navigation")
    assert parsed["type"] == "explorar"
    assert parsed["task_binding"] is None
    assert parsed["task_binding_error"]["code"] == "EYLE_TASK_BINDING_INVALID"


def test_rev4_surface_wire_contracts_are_physically_separate():
    nav = wire_schema_for_profile("navigation")
    explore = wire_schema_for_profile("explore")
    build = wire_schema_for_profile("build")
    encoded_nav = str(nav)
    encoded_explore = str(explore)
    encoded_build = str(build)
    assert "'operations'" not in encoded_nav
    assert "'operation'" not in encoded_nav
    assert "'operations'" in encoded_explore
    assert "'response'" not in encoded_explore
    assert "'operation'" in encoded_build
    assert "'operations'" not in encoded_build


def test_rev4_prompt_catalog_follows_explicit_surface(tmp_path):
    reg = standard_registry()
    ctx = _pc(tmp_path)
    cfg = base_config()
    session = AgentSession("inspect", execution_id="p")

    nav, _ = agent._compile_prompt(session, cfg, ctx, {"recent_messages": []}, reg)
    assert set(nav.stable) == {"ecc_navigation", "runtime_environment"}
    assert "explore_operations" not in nav.stable and "build_operations" not in nav.stable

    session.cognitive_surface = "explore"
    explore, _ = agent._compile_prompt(session, cfg, ctx, {"recent_messages": []}, reg)
    assert "explore_operations" in explore.stable
    assert "build_operations" not in explore.stable
    assert all("operation" in item for item in explore.stable["explore_operations"]["operations"])

    session.cognitive_surface = "build"
    build, _ = agent._compile_prompt(session, cfg, ctx, {"recent_messages": []}, reg)
    assert "build_operations" in build.stable
    assert "explore_operations" not in build.stable


def test_rev4_trivial_request_is_one_navigation_cognition_without_task(monkeypatch, tmp_path):
    calls = []
    def fake(surface, prompt, cfg):
        calls.append((surface, copy.deepcopy(prompt.stable)))
        return {"type": "concluir", "response": "Rev4", "memory_delta": [], "task_binding": None}
    monkeypatch.setattr(agent, "_call_surface_llm", fake)

    status, text, pending, details = run_agent(
        agent, "qual tua versão?", base_config(), provider_context=_pc(tmp_path),
        execution_id="trivial", retornar_detalhes=True,
    )
    assert (status, text, pending) == ("completed", "Rev4", None)
    assert [item[0] for item in calls] == ["navigation"]
    assert details.get("active_task_id") is None
    assert details["llm_usage"]["navigation_calls"] == 1
    assert details["llm_usage"]["explore_calls"] == 0


def test_rev4_explore_surface_returns_to_navigation_before_conclusion(monkeypatch, tmp_path):
    scripted = iter([
        ("navigation", {"type": "explorar", "memory_delta": [], "task_binding": None}),
        ("explore", {"operations": [{"operation": "probe", "arguments": {}}], "memory_delta": [], "task_binding": None}),
        ("explore", {"return_to_ecc": True, "memory_delta": [], "task_binding": None}),
        ("navigation", {"type": "concluir", "response": "done", "memory_delta": [], "task_binding": None}),
    ])
    seen = []
    def fake(surface, prompt, cfg):
        expected, result = next(scripted)
        assert surface == expected
        seen.append(surface)
        return result
    monkeypatch.setattr(agent, "_call_surface_llm", fake)
    monkeypatch.setattr(agent, "dispatch", lambda *a, **kw: DispatchOutcome({
        "operation": "probe", "status": "success", "ok": True,
        "executed": True, "changed": False, "detail": {"fact": 1},
    }, physical_progress=True))

    status, text, _, details = run_agent(
        agent, "investigue", base_config(), provider_context=_pc(tmp_path),
        execution_id="explore-flow", retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "done")
    assert seen == ["navigation", "explore", "explore", "navigation"]
    assert details["llm_usage"]["navigation_calls"] == 2
    assert details["llm_usage"]["explore_calls"] == 2
    assert details["llm_usage"]["surface_transitions"] == 2


def test_rev4_build_surface_returns_to_navigation_after_mutation_attempt(monkeypatch, tmp_path):
    scripted = iter([
        ("navigation", {"type": "construir", "memory_delta": [], "task_binding": None}),
        ("build", {"operation": "mut", "arguments": {}, "memory_delta": [], "task_binding": None}),
        ("navigation", {"type": "concluir", "response": "done", "memory_delta": [], "task_binding": None}),
    ])
    def fake(surface, prompt, cfg):
        expected, result = next(scripted)
        assert surface == expected
        return result
    monkeypatch.setattr(agent, "_call_surface_llm", fake)
    monkeypatch.setattr(agent, "dispatch", lambda *a, **kw: DispatchOutcome({
        "operation": "mut", "status": "success", "ok": True,
        "executed": True, "changed": True,
        "physical_effect": {"kind": "workspace_change"}, "detail": {"changed": True},
    }, physical_progress=True))

    status, text, _, details = run_agent(
        agent, "mude", base_config(), provider_context=_pc(tmp_path),
        execution_id="build-flow", retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "done")
    assert details["llm_usage"]["navigation_calls"] == 2
    assert details["llm_usage"]["build_calls"] == 1
    assert details["cognitive_surface"] == "navigation"


def test_rev4_same_call_task_creation_and_binding_anchors_explore_surface(monkeypatch, tmp_path):
    ctx = _pc(tmp_path)
    seen = []
    def fake(surface, prompt, cfg):
        seen.append(surface)
        if surface == "navigation" and len(seen) == 1:
            return {
                "type": "explorar",
                "memory_delta": [{
                    "op": "remember",
                    "key": "current_task",
                    "scope": "world",
                    "retention": "persistent",
                    "kind": "task",
                    "content": "Investigar a autenticação sem alterar o workspace.",
                    "supports": [{"kind": "request"}],
                }],
                "task_binding": {"action": "bind", "ref": "@current_task"},
            }
        if surface == "explore":
            view = prompt.dynamic["active_task"]
            assert view["available"] is True
            assert view["state"] == "active"
            assert view["content"] == "Investigar a autenticação sem alterar o workspace."
            assert set(view) == {"id", "available", "revision", "state", "state_revision", "content"}
            return {"return_to_ecc": True, "memory_delta": []}
        assert surface == "navigation"
        return {"type": "concluir", "response": "done", "memory_delta": []}

    monkeypatch.setattr(agent, "_call_surface_llm", fake)
    status, text, _, details = run_agent(
        agent, "investigue auth", base_config(), provider_context=ctx,
        execution_id="task-anchor", retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "done")
    assert seen == ["navigation", "explore", "navigation"]
    assert details["active_task_id"].startswith("mem-")
    assert details["llm_usage"]["task_bind_count"] == 1


def test_rev4_surface_catalog_reduction_is_measured_without_semantic_routing(tmp_path):
    import json
    from eyle.core.ecc import catalog, navigation_directory, surface_catalog

    reg = standard_registry()
    cfg = base_config()
    available = reg.names()
    full = catalog(reg, cfg, available, memory_enabled=True)
    navigation = navigation_directory(reg, cfg, available, memory_enabled=True)
    explore = surface_catalog(reg, cfg, available, surface="explore", memory_enabled=True)
    build = surface_catalog(reg, cfg, available, surface="build", memory_enabled=True)

    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    assert size(navigation) <= size(full) * 0.25
    assert size(explore) < size(full)
    assert size(build) < size(full)


def test_rev4_specialized_surfaces_keep_current_request_until_benchmark_allows_more_compression(tmp_path):
    reg = standard_registry()
    cfg = base_config()
    session = AgentSession("semantic anchor", execution_id="anchor")
    session.cognitive_surface = "explore"
    prompt, _ = agent._compile_prompt(session, cfg, _pc(tmp_path), {"recent_messages": []}, reg)
    assert prompt.dynamic["current_request"] == "semantic anchor"
