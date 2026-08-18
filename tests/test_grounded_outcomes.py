from __future__ import annotations
from tests.canonical import run_agent
from tests.canonical import standard_registry

import json
from pathlib import Path

import eyle.core.agent as core_agent
import eyle.providers.standard.sandbox as sandbox_mod
from eyle.providers.standard.code_relations import analyze_symbol_relations
from eyle.runtime.execution_context import ExecutionContext, bind_execution, reset_execution
from tests.canonical import base_config








def test_symbol_relations_reports_registry_binding_and_root_reachability(tmp_path):
    (tmp_path / "tools.py").write_text(
        "def target():\n    return 1\n\nTOOLS = {'x': {'fn': target}}\n",
        encoding="utf-8",
    )
    result = standard_registry().execute(
        "standard.symbol_relations",
        {"symbol": "target", "roots": ["tools.py"], "direction": "incoming", "include_text_references": False},
        {"provider_context": {"standard": {"caminho_origem": str(tmp_path)}}, "config": base_config()},
    )
    assert result["ok"] is True
    detail = result["detail"]
    assert any(edge["kind"] == "registry_binding" for edge in detail["incoming"])
    assert detail["outgoing"] == []
    assert detail["text_references"] == []
    assert detail["root_reachability"][0]["reachable"] is True


def test_docker_backend_reuses_one_container_per_job(monkeypatch, tmp_path):
    cfg = {
        "backend": "docker", "imagem_oci": "python:3.12-slim",
        "timeout_segundos": 30, "cpu_segundos": 30, "memoria_mb": 256,
        "max_processos": 32, "max_arquivos_abertos": 64, "max_saida_kb": 64,
        "max_arquivo_mb": 64, "max_arquivos_projeto": 1000, "max_tamanho_projeto_mb": 64,
        "cpus": 1.0,
    }
    monkeypatch.setattr(sandbox_mod.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    calls = []

    class Completed:
        returncode = 0
        stdout = "container-id\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return Completed()

    monkeypatch.setattr(sandbox_mod.subprocess, "run", fake_run)
    execution = ExecutionContext.from_config(base_config())
    token = bind_execution(execution)
    try:
        limits = sandbox_mod._limites(cfg)
        first, cleanup1 = sandbox_mod._comando_docker(str(tmp_path), "apt-get update", ".", cfg, limits)
        second, cleanup2 = sandbox_mod._comando_docker(str(tmp_path), "python -V", ".", cfg, limits)
        assert cleanup1 is None and cleanup2 is None
        assert first[0:2] == ["/usr/bin/docker", "exec"]
        assert second[0:2] == ["/usr/bin/docker", "exec"]
        state = execution.provider_state_for("standard.sandbox")
        assert first[4] == second[4] == state["container_name"]
        docker_runs = [call for call in calls if call[:2] == ["/usr/bin/docker", "run"]]
        assert len(docker_runs) == 1
        assert "--pull" in docker_runs[0] and "missing" in docker_runs[0]
        assert "--read-only" not in docker_runs[0]
        assert "--network" in docker_runs[0] and "bridge" in docker_runs[0]
    finally:
        execution.cleanup()
        reset_execution(token)


def test_symbol_relations_reports_python_main_guard_even_with_ambiguous_main_names(tmp_path):
    (tmp_path / "a.py").write_text(
        "def main():\n    return 1\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    detail = analyze_symbol_relations(
        str(tmp_path), "main", path="a.py", direction="incoming",
        include_text_references=False, max_depth=4, max_edges=20,
    )
    assert len(detail["definitions"]) == 1
    assert any(
        edge["kind"] == "python_main_guard"
        and edge["from"] == "a.py::<module>"
        and edge["to"] == "a.py::main"
        for edge in detail["incoming"]
    )
    assert "python_main_guard" in detail["reachability_edge_kinds"]
