from __future__ import annotations

import os
import shlex
import subprocess
import sys
from tests.canonical import run_agent
from tests.canonical import standard_registry

import json
from pathlib import Path

import eyle.core.agent as core_agent
from eyle.runtime.ecc_runtime import project_result
import eyle.providers.standard.sandbox as sandbox_mod
from eyle.runtime.execution_context import ExecutionContext, bind_execution, reset_execution
from eyle.core.session import AgentSession
from tests.canonical import base_config


def observation_signature(name, arguments):
    return standard_registry().observation_signature(f"standard.{name}" if "." not in name else name, arguments)


def _ctx(root, config=None):
    return {"provider_context": {"standard": {"caminho_origem": str(root)}}, "config": config or base_config()}


def test_symbol_relations_distinguishes_references_from_root_reachability(tmp_path):
    (tmp_path / "main.py").write_text(
        "def live():\n    return 1\n\nif __name__ == '__main__':\n    live()\n",
        encoding="utf-8",
    )
    (tmp_path / "dead.py").write_text(
        "def target():\n    return 7\n\ndef dead_a():\n    return target()\n\ndef dead_b():\n    return target()\n",
        encoding="utf-8",
    )
    result = standard_registry().execute(
        "standard.symbol_relations",
        {"symbol": "target", "roots": ["main.py", "dead_a"], "max_depth": 6},
        _ctx(tmp_path),
    )
    assert result["ok"] is True
    detail = result["detail"]
    assert len(detail["definitions"]) == 1
    assert len(detail["incoming"]) == 2
    roots = {item["root"]: item for item in detail["root_reachability"]}
    assert roots["main.py"]["reachable"] is False
    assert roots["dead_a"]["reachable"] is True
    assert detail["semantics"] == "structural_facts_only"
    assert "live" not in detail and "dead" not in detail and "legacy" not in detail


def test_run_command_snapshot_persists_for_job_without_touching_real_workspace(monkeypatch, tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("real", encoding="utf-8")
    cfg = {
        "backend": "auto", "timeout_segundos": 10, "cpu_segundos": 10,
        "memoria_mb": 512, "max_processos": 32, "max_arquivos_abertos": 64,
        "max_saida_kb": 64, "max_arquivo_mb": 64, "max_arquivos_projeto": 1000,
        "max_tamanho_projeto_mb": 64,
    }
    # Exercise persistence/isolation without requiring bwrap in the test host.
    monkeypatch.setattr(sandbox_mod, "_strong_backend", lambda config: "bwrap")
    monkeypatch.setattr(sandbox_mod, "_comando_bwrap", lambda workspace, argv, config, limits: (list(argv), None))
    execution = ExecutionContext.from_config(base_config())
    token = bind_execution(execution)
    try:
        write_argv = [sys.executable, "-c", "from pathlib import Path; Path('generated.txt').write_text('sandbox', encoding='utf-8')"]
        read_argv = [sys.executable, "-c", "from pathlib import Path; print(Path('generated.txt').read_text(encoding='utf-8'))"]
        render = subprocess.list2cmdline if os.name == "nt" else shlex.join
        first = sandbox_mod.executar_comando_livre_no_sandbox(
            str(tmp_path), render(write_argv), cfg,
        )
        second = sandbox_mod.executar_comando_livre_no_sandbox(
            str(tmp_path), render(read_argv), cfg,
        )
        assert first["ok"] is True and second["ok"] is True
        assert "sandbox" in second["saida"]
        assert not (tmp_path / "generated.txt").exists()
        assert real.read_text(encoding="utf-8") == "real"
        assert first["workspace_isolated"] is True
        assert first["snapshot_persists_for_job"] is True
        assert first["network_enabled"] is True
    finally:
        execution.cleanup()
        reset_execution(token)


def test_unrestricted_run_command_refuses_weak_backend(monkeypatch):
    monkeypatch.setattr(sandbox_mod.shutil, "which", lambda name: None)
    try:
        sandbox_mod._strong_backend({"backend": "process"})
    except sandbox_mod.ErroSandbox as exc:
        assert "strong backends" in str(exc)
    else:
        raise AssertionError("weak backend should be rejected")


def test_run_command_is_not_replayable_because_sandbox_state_can_change():
    assert observation_signature("run_command", {"command": "echo x"}) is None


def test_find_symbol_model_view_is_location_only(tmp_path):
    (tmp_path / "a.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    raw = standard_registry().execute("standard.find_symbol", {"symbol": "hello"}, _ctx(tmp_path))
    assert raw["ok"] is True
    # Raw Evidence may retain source bytes; model-facing locator must not.
    session = AgentSession("locate")
    model = project_result(session, "standard.find_symbol", raw, standard_registry(), base_config())
    detail = model["detail"]
    assert detail["file"] == "a.py"
    assert "numbered_content" not in detail and "content" not in detail and "codigo_original" not in detail


def test_agent_sandbox_snapshot_omits_only_path_identified_protected_resources(tmp_path):
    (tmp_path / ".env").write_text("TOKEN=supersecretvalue\n", encoding="utf-8")
    (tmp_path / "safe.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "looks_safe.py").write_text("api_key='abcdefghijklmnop'\n", encoding="utf-8")
    limits = sandbox_mod._limites({
        "timeout_segundos": 5, "cpu_segundos": 5, "memoria_mb": 256,
        "max_processos": 16, "max_arquivos_abertos": 32, "max_saida_kb": 32,
        "max_arquivo_mb": 64, "max_arquivos_projeto": 1000, "max_tamanho_projeto_mb": 64,
    })
    snapshot, tempdir = sandbox_mod._copiar_projeto(str(tmp_path), limits)
    try:
        assert Path(snapshot, "safe.py").exists()
        assert not Path(snapshot, ".env").exists()
        assert Path(snapshot, "looks_safe.py").exists()
        assert getattr(tempdir, "protected_resources_omitted", []) == [".env"]
    finally:
        tempdir.cleanup()
