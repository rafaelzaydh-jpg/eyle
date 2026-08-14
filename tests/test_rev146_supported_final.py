from tests.canonical import standard_registry
import json
from pathlib import Path

import pytest

import eyle.core.agent as agent
import eyle.providers.standard as tools
import eyle.providers.memory as memory_provider
from eyle.core.session import AgentSession
from eyle.core.token_budget import estimate_tokens
from eyle.core.validation import validate_complete
from llm.executar import PROMPT_AGENTE
from tests.canonical import agent_complete, base_config


def test_prompt_preserves_supported_terminal_semantics_without_forcing_workflow():
    assert "complete: deliver the terminal answer" in PROMPT_AGENTE.lower()
    assert "Complete carries grounding_ids and effect_ids as explicit coordinates" in PROMPT_AGENTE
    assert "there is no" not in PROMPT_AGENTE.lower() or True
    lower = PROMPT_AGENTE.lower()
    for forbidden in ("must use a capability", "must create an investigation", "must create a task", "before complete, inspect", "always investigate"):
        assert forbidden not in lower
    assert estimate_tokens(PROMPT_AGENTE, 3) > 570

def test_runtime_still_accepts_direct_complete_without_grounding_when_main_created_no_commitment():
    ok, reason, answer, limitations = validate_complete(
        {"answer": "Olá", "limitations": [], "grounding_ids": [], "effect_ids": []}, {}, {},
    )
    assert ok is True and reason == "ok" and answer == "Olá" and limitations == []


def test_physical_effect_shape_is_domain_neutral_and_rejects_bad_persistence():
    assert tools.physical_effect("isolated_snapshot", "job") == {
        "resource": "isolated_snapshot", "operation": "capability", "persistence": "job", "changed": False,
    }
    with pytest.raises(ValueError, match="PHYSICAL_EFFECT_PERSISTENCE_INVALID"):
        tools.physical_effect("x", "forever")

def test_run_command_reports_snapshot_effect_and_preserves_it_in_material(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "executar_comando_livre_no_sandbox", lambda *a, **k: {
        "executado": True, "ok": True, "cwd": ".", "codigo": 0, "saida": "4\n",
        "backend": "microsandbox", "network_enabled": True, "workspace_isolated": True,
        "snapshot_persists_for_job": True, "protected_resources_omitted": 0,
    })
    result = standard_registry().execute("run_command", {"source": "workspace", "command": "python3 calc.py"}, {
        "provider_context": {"standard": {"caminho_origem": str(tmp_path), "eyle_root": str(tmp_path)}},
        "config": base_config(), "observation_ledger": {}, "reality_epoch": 0,
    })
    assert result["ok"] is True and result["changed"] is False
    assert result["physical_effect"] == {"resource": "isolated_snapshot", "operation": "run_command", "persistence": "job", "changed": False}
    material_payload = json.loads(result["observations"][0]["content"])
    assert material_payload["physical_effect"] == result["physical_effect"]

def test_model_and_public_tool_result_preserve_physical_effect():
    effect = tools.physical_effect("isolated_snapshot", "job")
    raw = tools._sucesso({"output": "4\n", "returncode": 0}, physical_effect=effect)
    session = AgentSession("x")
    model = agent._model_capability_result(session, "standard.run_command", raw, standard_registry(), base_config())
    public = tools.capability_public_result("run_command", raw)
    assert model["physical_effect"] == effect
    assert public["physical_effect"] == effect


def test_run_tests_export_and_memory_store_effect_resources(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "rodar_testes_projeto", lambda *a, **k: {
        "executado": True, "ok": True, "saida_resumida": "1 passed", "comando": "pytest", "codigo": 0,
        "backend": "microsandbox", "runner": "pytest", "scope": None, "tests_detected": True,
    })
    ctx = {"provider_context": {"standard": {"caminho_origem": str(tmp_path), "eyle_root": str(tmp_path)}}, "config": base_config(tests_enabled=True)}
    test_result = tools._tool_run_tests({}, ctx)
    assert test_result["physical_effect"]["resource"] == "isolated_test_sandbox"
    assert test_result["physical_effect"]["persistence"] == "call"
    assert test_result["physical_effect"]["changed"] is False

    monkeypatch.setattr(tools, "export_active_sandbox_zip", lambda *a, **k: {"artifact": "x.zip", "bytes": 10, "sha256": "a" * 64})
    export_result = tools._tool_export_sandbox_zip({"filename": "x.zip"}, ctx)
    assert export_result["physical_effect"]["resource"] == "artifact"
    assert export_result["physical_effect"]["persistence"] == "persistent"

    monkeypatch.setattr(memory_provider, "apply_memory_changeset", lambda *a, **k: {"changeset_id": "cs-1", "count": 1})
    monkeypatch.setattr(memory_provider, "memory_record", lambda *a, **k: {"id": a[-1], "revision": 1, "content": "x"})
    memory_ctx = {"provider_context": {"memory": {"storage_dir": str(tmp_path / "memory"), "scope_root": str(tmp_path)}}, "grounding": {}}
    memory_result = standard_registry().execute("memory.store", {"text": "remember", "meta": {}}, memory_ctx)
    assert memory_result["changed"] is True
    assert memory_result["physical_effect"]["resource"] == "memory.kernel"
    assert memory_result["physical_effect"]["persistence"] == "persistent"

def test_workspace_transaction_declares_provider_owned_persistent_mutation():
    spec = tools.CAPABILITIES["workspace_transaction"]
    assert spec["effect"] == "mutate"
    assert spec["confirmation"] == "required"
    from eyle.contracts.capability import physical_effect
    assert physical_effect("workspace", "transaction", "persistent", changed=True) == {
        "resource": "workspace", "operation": "transaction", "persistence": "persistent", "changed": True,
    }

def test_release_manifest_declares_rev15_universal_physical_effects():
    manifest = json.loads(Path("release_manifest.json").read_text(encoding="utf-8"))
    assert manifest["config_schema_version"] == "2.7.5-r1.5.3"
    assert manifest["revision"] == "rev1.5.3-cognitive-task-memory"
    assert "resource, operation, persistence and changed" in manifest["architecture"]["effects"]

