from __future__ import annotations

import json
import pytest
from pathlib import Path

import eyle.core.agent as agent
from eyle.capabilities import Provider, build_registry
from eyle.contracts.capability import result
from eyle.core.ecc import operation_map
from tests.canonical import base_config, run_agent, standard_registry


def memory_from_learned(learned=None):
    operations=[]
    for item in learned or []:
        support=dict(item.get("support") or {})
        supports=[]
        if support.get("material_id"):
            support.pop("line_start", None); support.pop("line_end", None)
            supports.append({"kind":"material", **support})
        operations.append({
            "op":"remember", "scope":"world", "kind":"fact",
            "content":str(item.get("statement") or "fact"), "supports":supports,
        })
    return operations


def explore(operation, arguments=None, learned=None):
    return {"type": "explorar", "operations": [{"operation": operation, "arguments": dict(arguments or {})}], "memory_delta": memory_from_learned(learned)}


def build(operation, arguments=None, learned=None):
    return {"type": "construir", "operation": operation, "arguments": dict(arguments or {}), "memory_delta": memory_from_learned(learned)}


def conclude(response, learned=None):
    return {"type": "concluir", "response": response, "memory_delta": memory_from_learned(learned)}


def provider_context(workspace: Path | None, eyle_root: Path | None = None):
    return {
        "standard": {
            "caminho_origem": str(workspace) if workspace is not None else None,
            "eyle_root": str(eyle_root or workspace) if (eyle_root or workspace) is not None else None,
        },
        "core_memory": {
            "storage_dir": str((workspace or eyle_root).parent / ((workspace or eyle_root).name + "_memory")) if (workspace or eyle_root) else None,
            "world_scope_id": f"workspace:{(workspace or eyle_root).resolve()}" if (workspace or eyle_root) else None,
        },
    }


def test_ecc_source_is_fail_closed_when_model_omits_it(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(str(prompt))
        prompts.append(payload)
        if len(prompts) == 1:
            return explore("read_file", {"path": "app.py", "line_start": 1, "line_end": 1})
        error = payload["latest_observations"][0]
        assert error["error_code"] == "ECC_SOURCE_REQUIRED"
        return conclude("corrigível")

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, _, details = run_agent(
        agent, "leia app.py", base_config(),
        provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "corrigível")
    assert details["physical_capability_calls"] == 0


def test_eyle_source_remains_available_without_user_workspace(monkeypatch, tmp_path):
    eyle_root = tmp_path / "self"
    (eyle_root / "eyle" / "core").mkdir(parents=True)
    (eyle_root / "eyle" / "core" / "session.py").write_text(
        "class AgentSession:\n    pass\n", encoding="utf-8",
    )
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(str(prompt))
        prompts.append(payload)
        if len(prompts) == 1:
            return explore("find_symbol", {"source": "eyle", "symbol": "AgentSession"})
        observed = payload["latest_observations"][0]
        assert observed["ok"] is True
        assert observed["detail"]["source"] == "eyle"
        return conclude("achou")

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, _, details = run_agent(
        agent, "onde AgentSession está na Eyle?", base_config(),
        provider_context=provider_context(None, eyle_root), retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "achou")
    assert details["physical_capability_calls"] == 1


def test_cache_signature_preserves_case_for_queries_symbols_and_paths():
    registry = standard_registry()
    assert registry.observation_signature(
        "standard.search_code", {"source": "workspace", "query": "AgentSession"}
    ) != registry.observation_signature(
        "standard.search_code", {"source": "workspace", "query": "agentsession"}
    )
    assert registry.observation_signature(
        "standard.find_symbol", {"source": "workspace", "symbol": "AgentSession", "path": "Foo.py"}
    ) != registry.observation_signature(
        "standard.find_symbol", {"source": "workspace", "symbol": "agentsession", "path": "foo.py"}
    )
    assert registry.observation_signature(
        "standard.read_file", {"source": "workspace", "path": "Foo.py", "line_start": 1, "line_end": 2}
    ) != registry.observation_signature(
        "standard.read_file", {"source": "workspace", "path": "foo.py", "line_start": 1, "line_end": 2}
    )


def test_negative_search_cache_invalidates_when_external_file_appears(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(str(prompt))
        prompts.append(payload)
        if len(prompts) == 1:
            return explore("search", {"source": "workspace", "query": "AgentSession"})
        if len(prompts) == 2:
            assert payload["latest_observations"][0]["detail"]["files_with_matches"] == 0
            (tmp_path / "session.py").write_text("class AgentSession:\n    pass\n", encoding="utf-8")
            return explore("search", {"source": "workspace", "query": "AgentSession"})
        observed = payload["latest_observations"][0]
        assert observed["status"] != "already_observed"
        assert observed["detail"]["files_with_matches"] >= 1
        return conclude("atualizou")

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, _, details = run_agent(
        agent, "procure AgentSession", base_config(),
        provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "atualizou")
    assert details["physical_capability_calls"] == 2
    assert details["operation_replays"] == 0


def test_positive_read_cache_invalidates_on_external_content_change(monkeypatch, tmp_path):
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(str(prompt))
        prompts.append(payload)
        if len(prompts) == 1:
            return explore("read_file", {"source": "workspace", "path": "a.py", "line_start": 1, "line_end": 1})
        if len(prompts) == 2:
            target.write_text("x = 222\n", encoding="utf-8")
            return explore("read_file", {"source": "workspace", "path": "a.py", "line_start": 1, "line_end": 1})
        observed = payload["latest_observations"][0]
        assert observed["status"] != "already_observed"
        assert "x = 222" in json.dumps(observed, ensure_ascii=False)
        return conclude("novo")

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, _, details = run_agent(
        agent, "leia duas vezes", base_config(),
        provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "novo")
    assert details["physical_capability_calls"] == 2
    assert details["operation_replays"] == 0







def test_alias_collision_is_stable_even_if_only_one_provider_is_available():
    empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

    def fn(arguments, ctx):
        return result("success", True, True, detail={"ok": 1})

    p1 = Provider("p1", {"sense": {"description": "sense", "input_schema": empty, "returns": "x", "effect": "observe", "fn": fn, "ecc_name": "look"}})
    p2 = Provider("p2", {"sense": {"description": "sense", "input_schema": empty, "returns": "x", "effect": "observe", "fn": fn, "ecc_name": "look"}})
    registry = build_registry([p1, p2])
    mapping = operation_map(registry, {"p1.sense"}, "explorar")
    assert mapping == {"p1.sense": "p1.sense"}


def test_repeated_identical_internal_failure_counts_as_no_progress_not_no_execution(monkeypatch, tmp_path):
    empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

    def explode(arguments, ctx):
        raise RuntimeError("same boom")

    provider = Provider("boom", {"fail": {
        "description": "always fails", "input_schema": empty, "returns": "failure",
        "effect": "observe", "fn": explode, "ecc_name": "fail",
        "signature": lambda arguments: "same",
    }})
    registry = build_registry([provider])
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(str(prompt))
        prompts.append(payload)
        if len(prompts) == 3:
            feedback = next(item for item in payload["runtime_feedback"] if item.get("code") == "NO_PROGRESS")
            assert feedback["physical_execution"] is True
            assert feedback["new_physical_observation"] is False
            assert feedback["repeat_count"] == 1
        return explore("fail", {})

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, _, details = agent.executar_agente(
        "falhe", base_config(), provider_context={"boom": {}}, registry=registry, retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] == "ECC_NO_PROGRESS_UNRECOVERABLE"
    assert details["physical_capability_calls"] == 3
    assert len(prompts) == 3


def test_run_tests_is_not_replay_cached_and_logs_source(monkeypatch, tmp_path):
    from eyle.providers.standard import editing

    workspace = tmp_path / "workspace"
    eyle_root = tmp_path / "eyle-src"
    workspace.mkdir(); eyle_root.mkdir()
    seen = []

    def fake_runner(root, cfg, scope=None):
        seen.append((Path(root), scope))
        return {
            "ok": True, "executado": True, "comando": "pytest", "codigo": 0,
            "scope": scope or ".", "backend": "local", "runner": "pytest",
            "tests_detected": True, "saida_resumida": "1 passed",
        }

    monkeypatch.setattr(editing, "rodar_testes_projeto", fake_runner)
    registry = standard_registry()
    cfg = base_config(tests_enabled=True)
    ctx = {"config": cfg, "provider_context": provider_context(workspace, eyle_root)}
    args = {"source": "eyle", "scope": "tests"}
    first = registry.execute("standard.run_tests", args, ctx)
    second = registry.execute("standard.run_tests", args, ctx)
    assert first["ok"] is True and second["ok"] is True
    assert seen == [(eyle_root, "tests"), (eyle_root, "tests")]
    assert registry.observation_signature("standard.run_tests", args) is None
    assert registry.public_arguments("standard.run_tests", args)["source"] == "eyle"


def test_pending_mutation_requires_explicit_confirmation_at_agent_boundary(monkeypatch, tmp_path):
    target = tmp_path / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")
    outputs = iter([
        explore("read_file", {"source": "workspace", "path": "app.py", "line_start": 1, "line_end": 1}),
        build("transaction", {"patches": [{"operation": "update", "path": "app.py", "line_start": 1, "line_end": 1, "new_code": "x = 2\n"}]}),
    ])
    monkeypatch.setattr(agent, "executar_ecc_llm", lambda prompt, cfg: next(outputs))
    cfg = base_config()
    status, _, pending, _ = run_agent(
        agent, "mude x", cfg, provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert status == "confirmation_required"

    status2, _, pending2, details2 = run_agent(
        agent, "mude x", cfg, provider_context=provider_context(tmp_path),
        retomar=pending, resposta_usuario="qualquer coisa", retornar_detalhes=True,
    )
    assert status2 == "confirmation_required"
    assert pending2 is not None
    assert details2["failure_code"] == "EXPLICIT_CONFIRMATION_REQUIRED"
    assert target.read_text(encoding="utf-8") == "x = 1\n"

    status3, _, pending3, _ = run_agent(
        agent, "mude x", cfg, provider_context=provider_context(tmp_path),
        retomar=pending, resposta_usuario="não", retornar_detalhes=True,
    )
    assert status3 == "cancelled"
    assert pending3 is None
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_runtime_environment_exposes_physical_source_availability_without_routing(monkeypatch, tmp_path):
    eyle_root = tmp_path / "self"
    eyle_root.mkdir()
    prompts = []
    monkeypatch.setattr(agent, "executar_ecc_llm", lambda prompt, cfg: prompts.append(json.loads(str(prompt))) or conclude("ok"))
    status, _, _, _ = run_agent(
        agent, "estado", base_config(), provider_context=provider_context(None, eyle_root), retornar_detalhes=True,
    )
    assert status == "completed"
    resources = prompts[0]["runtime_environment"]["providers"]["standard"]["resources"]
    assert resources["workspace"]["available"] is False
    assert resources["eyle_source"]["available"] is True




def test_memory_is_internal_not_registered_provider(tmp_path):
    registry = standard_registry()
    assert not any(name.startswith("memory.") for name in registry.names())
    ctx = provider_context(tmp_path)
    assert ctx["core_memory"]["storage_dir"]

def test_missing_eyle_source_never_falls_back_to_user_workspace_on_rehydrate(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "same.py").write_text("WRONG = 1\n", encoding="utf-8")
    materials = {
        "mat-0001": {
            "id": "mat-0001",
            "source_type": "read_file",
            "source_capability": "standard.read_file",
            "source_provider": "standard",
            "locator": {"kind": "file", "source": "eyle", "path": "same.py", "line_start": 1, "line_end": 1},
            "source_version": "old-eyle-hash",
            "content_hash": "old-content-hash",
            "reality_epoch": 0,
        }
    }
    registry = standard_registry()
    registry.rehydrate_materials(
        materials,
        {"config": base_config(), "provider_context": {
            "standard": {"caminho_origem": str(workspace), "eyle_root": None},
            "core_memory": {"storage_dir": str(workspace / ".memory"), "world_scope_id": f"workspace:{workspace.resolve()}"},
        }},
    )
    assert "content" not in materials["mat-0001"]
    assert materials["mat-0001"]["rehydration_error"] == "OBSERVATION_SOURCE_UNAVAILABLE"


def test_global_calculation_material_is_not_mislabeled_as_workspace(tmp_path):
    registry = standard_registry()
    result_value = registry.execute(
        "standard.calculate", {"expression": "2+2"},
        {"config": base_config(), "provider_context": provider_context(tmp_path)},
    )
    assert result_value["ok"] is True
    assert result_value["observations"][0]["locator"]["source"] == "runtime"


def test_unavailable_selected_source_reports_source_not_workspace(tmp_path):
    registry = standard_registry()
    ctx = {"config": base_config(), "provider_context": {
        "standard": {"caminho_origem": str(tmp_path), "eyle_root": None},
        "core_memory": {"storage_dir": str(tmp_path / ".memory"), "world_scope_id": f"workspace:{tmp_path.resolve()}"},
    }}
    normalized, error = registry.validate("standard.read_file", {"source": "eyle", "path": "x.py"})
    assert error is None
    result_value = registry.execute("standard.read_file", normalized, ctx)
    assert result_value["error_code"] == "SOURCE_NOT_AVAILABLE"
    assert result_value["detail"]["source"] == "eyle"


def test_source_change_during_operation_is_not_cached_as_current(monkeypatch):
    empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    state = {"version": 0}

    def token(arguments, ctx):
        return f"v{state['version']}"

    def sense(arguments, ctx):
        state["version"] += 1
        return result("success", True, True, detail={"observed_version": state["version"]})

    provider = Provider("race", {"sense": {
        "description": "race probe", "input_schema": empty, "returns": "probe",
        "effect": "observe", "fn": sense, "ecc_name": "sense",
        "signature": lambda arguments: "race:same", "freshness_token": token,
    }})
    registry = build_registry([provider])
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(str(prompt))
        prompts.append(payload)
        if len(prompts) <= 2:
            if len(prompts) == 2:
                observed = payload["latest_observations"][0]
                assert observed["current_world_valid"] is False
                assert observed["freshness_reason"] == "source_changed_during_operation"
            return explore("sense", {})
        assert payload["latest_observations"][0]["freshness_reason"] == "source_changed_during_operation"
        return conclude("race detectada")

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, _, details = agent.executar_agente(
        "observe", base_config(), provider_context={"race": {}}, registry=registry, retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "race detectada")
    assert details["physical_capability_calls"] == 2
    assert details["operation_replays"] == 0


def test_eyle_observation_cannot_authorize_workspace_write_with_same_path(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    eyle_root = tmp_path / "eyle-src"
    workspace.mkdir(); eyle_root.mkdir()
    (workspace / "same.py").write_text("X = 1\n", encoding="utf-8")
    (eyle_root / "same.py").write_text("X = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(str(prompt))
        prompts.append(payload)
        if len(prompts) == 1:
            return explore("read_file", {"source": "eyle", "path": "same.py", "line_start": 1, "line_end": 1})
        if len(prompts) == 2:
            return build("transaction", {
                "patches": [{"operation": "update", "path": "same.py", "line_start": 1, "line_end": 1, "new_code": "X = 2\n"}]
            })
        failed = payload["latest_observations"][0]
        assert failed["ok"] is False
        assert failed["error_code"] == "WORKSPACE_TRANSACTION_INVALID"
        assert "observe the existing file" in str(failed.get("detail"))
        return conclude("bloqueado")

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, pending, _ = run_agent(
        agent, "altere same.py", base_config(),
        provider_context=provider_context(workspace, eyle_root), retornar_detalhes=True,
    )
    assert (status, text, pending) == ("completed", "bloqueado", None)
    assert (workspace / "same.py").read_text(encoding="utf-8") == "X = 1\n"


def test_eyle_find_symbol_excludes_live_runtime_top_level_directories(tmp_path):
    eyle_root = tmp_path / "eyle-src"
    (eyle_root / "workspace").mkdir(parents=True)
    (eyle_root / "workspace" / "secret.py").write_text("class RuntimeOnlySecret:\n    pass\n", encoding="utf-8")
    (eyle_root / "eyle").mkdir()
    (eyle_root / "eyle" / "real.py").write_text("class RealSourceSymbol:\n    pass\n", encoding="utf-8")
    registry = standard_registry()
    ctx = {"config": base_config(), "provider_context": provider_context(None, eyle_root)}

    hidden = registry.execute("standard.find_symbol", {"source": "eyle", "symbol": "RuntimeOnlySecret"}, ctx)
    visible = registry.execute("standard.find_symbol", {"source": "eyle", "symbol": "RealSourceSymbol"}, ctx)
    assert hidden["ok"] is False and hidden["error_code"] == "SYMBOL_NOT_FOUND"
    assert visible["ok"] is True
    assert visible["detail"]["file"] == "eyle/real.py"


def test_workspace_transaction_uses_canonical_registry_run_tests(monkeypatch, tmp_path):
    from eyle.providers.standard import editing
    from eyle.providers.standard import workspace_transaction as workspace_tx

    seen = []
    monkeypatch.setattr(editing, "rodar_testes_projeto", lambda root, cfg, scope=None: (
        seen.append((Path(root), scope)) or {
            "ok": True, "executado": True, "comando": "pytest", "codigo": 0,
            "scope": scope or ".", "backend": "fake", "runner": "pytest",
            "tests_detected": True, "saida_resumida": "1 passed",
        }
    ))
    registry = standard_registry()
    ctx = {
        "config": base_config(tests_enabled=True),
        "provider_context": provider_context(tmp_path),
        "registry": registry,
    }
    outcome = workspace_tx._run_tests(ctx)
    assert outcome["ok"] is True
    assert outcome["executed"] is True
    assert seen == [(tmp_path, None)]


def test_rev375_type_is_single_family_authority_and_operation_names_are_current_only():
    from llm.structured import parse_profile_response, StructuredResponseError

    direct = parse_profile_response({"type":"explorar","operations":[{"operation":"search","arguments":{}}],"memory_delta":[]}, "ecc")
    assert direct["operations"][0]["operation"] == "search"
    for retired in ("explorar.search", "construir.transaction"):
        with pytest.raises(StructuredResponseError):
            parse_profile_response({"type":"explorar","operations":[{"operation":retired,"arguments":{}}],"memory_delta":[]}, "ecc")


def test_rev22_structured_retry_resets_after_a_valid_decision(monkeypatch, tmp_path):
    from llm.executar import ErroLLM

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    sequence = iter([
        ErroLLM("bad1", transient=False, error_code="STRUCTURED_RESPONSE_INVALID:ecc:ECC_OPERATION_INVALID"),
        explore("read_file", {"source": "workspace", "path": "a.py", "line_start": 1, "line_end": 1}),
        ErroLLM("bad2", transient=False, error_code="STRUCTURED_RESPONSE_INVALID:ecc:ECC_OPERATION_INVALID"),
        conclude("ok"),
    ])

    def fake(prompt, cfg):
        item = next(sequence)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, pending, details = run_agent(
        agent, "observe", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text, pending) == ("completed", "ok", None)
    assert details["physical_capability_calls"] == 1


def test_rev22_search_material_supports_line_selected_learning(monkeypatch, tmp_path):
    (tmp_path / "session.py").write_text(
        "header = 1\nclass AgentSession:\n    pass\nfooter = 2\n", encoding="utf-8",
    )
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(str(prompt))
        prompts.append(payload)
        if len(prompts) == 1:
            return explore("search", {"source": "workspace", "query": "AgentSession"})
        observed = payload["latest_observations"][0]
        row = observed["detail"]["results"][0]
        mat = row["grounding_id"]
        return conclude("found", learned=[{
            "statement": "AgentSession is declared in session.py.",
            "support": {"material_id": mat, "line_start": 2, "line_end": 3},
        }])

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, _, details = run_agent(
        agent, "find AgentSession", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "found")
    assert details["memory_nodes"] == 1
    assert details["evidence_items"] == 1


def test_rev22_context_window_is_backend_configuration_not_llama_38k_cap():
    from eyle.runtime.token_budget import available_user_prompt_tokens
    cfg = base_config()
    cfg["llm"]["context_window_tokens"] = 100_000
    budget = available_user_prompt_tokens(cfg, "system", output_tokens=2_000)
    assert budget > 90_000


def test_rev22_read_timeout_marks_provider_usage_unknown_and_cost_risk():
    import llm.executar as executar
    attempt = {"request_status": "started"}
    executar._registrar_falha_tentativa_runtime(attempt, "READ_TIMEOUT", "timed out", elapsed_ms=1234)
    assert attempt["provider_usage_unknown"] is True
    assert attempt["billing_may_have_occurred"] is True
    assert attempt["retry_cost_risk"] is True
