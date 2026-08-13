#!/usr/bin/env python3
"""Sandbox fail-closed, allowlist and limit tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eyle.providers.standard_impl.editing as codar_mod  # noqa: E402
import eyle.providers.standard_impl.sandbox as sandbox_mod  # noqa: E402


def _cfg(**extras):
    cfg = {
        "backend": "process",
        "bloquear_rede": False,
        "comandos_permitidos": [["python", "-m", "pytest"]],
        "timeout_segundos": 5,
        "cpu_segundos": 4,
        "memoria_mb": 128,
        "max_processos": 16,
        "max_arquivos_abertos": 32,
        "max_saida_kb": 32,
    }
    cfg.update(extras)
    return cfg


def test_allowlist_recusa_antes_de_criar_processo(monkeypatch, tmp_path):
    def nao_pode_criar(*args, **kwargs):
        raise AssertionError("Popen nao deveria ser chamado")

    monkeypatch.setattr(sandbox_mod.subprocess, "Popen", nao_pode_criar)
    resultado = sandbox_mod.executar_no_sandbox(
        str(tmp_path), ["python", "script_perigoso.py"], _cfg(),
    )

    assert resultado["executado"] is False
    assert resultado["ok"] is False
    assert "allowlist" in resultado["erro"]


def test_process_backend_does_not_pretend_to_block_network(tmp_path):
    resultado = sandbox_mod.executar_no_sandbox(
        str(tmp_path), ["python", "-m", "pytest"],
        _cfg(bloquear_rede=True),
    )

    assert resultado["executado"] is False
    assert "does not block network" in resultado["erro"]


def test_bwrap_monta_isolamento_rede_workspace_e_limites(monkeypatch, tmp_path):
    caminhos = {"bwrap": "/usr/bin/bwrap", "prlimit": "/usr/bin/prlimit"}
    monkeypatch.setattr(sandbox_mod.shutil, "which", lambda nome: caminhos.get(nome))
    limites = sandbox_mod._limites(_cfg())

    comando, limpeza = sandbox_mod._comando_bwrap(
        str(tmp_path), ["python", "-m", "pytest"],
        _cfg(bloquear_rede=True), limites,
    )

    assert limpeza is None
    assert "--unshare-all" in comando
    assert "--share-net" not in comando
    assert ["--bind", os.path.realpath(tmp_path), "/workspace"] == comando[
        comando.index("--bind"):comando.index("--bind") + 3
    ]
    assert any(item.startswith("--as=") for item in comando)
    assert any(item.startswith("--nproc=") for item in comando)
    assert comando[-3:] == ["python", "-m", "pytest"]


def test_override_por_projeto_nao_vem_do_repositorio(tmp_path):
    cfg = _cfg(
        comandos_permitidos=[["pytest"]],
        projetos={os.path.realpath(tmp_path): {"comandos_permitidos": [["npm", "test"]]}},
    )
    efetiva = sandbox_mod._config_para_projeto(str(tmp_path), cfg)

    assert efetiva["comandos_permitidos"] == [["npm", "test"]]
    assert "projetos" not in efetiva


def test_recusa_do_sandbox_nao_vira_teste_pulado(monkeypatch, tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    monkeypatch.setattr(codar_mod, "executar_no_sandbox", lambda *args, **kwargs: {
        "executado": False, "ok": False, "codigo": None,
        "saida": "", "erro": "backend indisponivel",
    })

    resultado = codar_mod.rodar_testes_projeto(
        str(tmp_path),
        {"ativado": True, "comando_python": "pytest -q", "sandbox": _cfg()},
    )

    assert resultado["executado"] is False
    assert resultado["ok"] is False
    assert resultado["recusado"] is True
    assert "backend indisponivel" in resultado["detalhe"]


def test_execucao_padrao_usa_copia_e_nao_altera_projeto_real(tmp_path):
    arquivo = tmp_path / "estado.txt"
    arquivo.write_text("original", encoding="utf-8")
    # Este teste mede isolamento por copia, nao pressao de memoria. Alguns
    # runtimes Python carregam extensoes no startup e entram em thrashing com
    # o teto artificial de 128 MiB da fixture, antes de executar o comando.
    cfg = _cfg(
        comandos_permitidos=[[sys.executable]],
        memoria_mb=512,
    )

    resultado = sandbox_mod.executar_no_sandbox(
        str(tmp_path),
        [sys.executable, "-c", "from pathlib import Path; Path('estado.txt').write_text('alterado')"],
        cfg,
    )

    assert resultado["ok"] is True
    assert arquivo.read_text(encoding="utf-8") == "original"


def test_metacaractere_de_shell_vira_argumento_literal(monkeypatch, tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    recebido = {}

    def fake_sandbox(caminho, argv, config):
        recebido["argv"] = argv
        return {"executado": True, "ok": True, "codigo": 0, "saida": "ok", "erro": None}

    monkeypatch.setattr(codar_mod, "executar_no_sandbox", fake_sandbox)
    resultado = codar_mod.rodar_testes_projeto(str(tmp_path), {
        "ativado": True,
        "comando_python": "pytest -q ; touch NAO_EXECUTAR",
        "sandbox": _cfg(),
    })

    assert resultado["ok"] is True
    assert recebido["argv"] == ["pytest", "-q", ";", "touch", "NAO_EXECUTAR"]


def test_snapshot_omits_internal_absolute_symlink(tmp_path):
    target = tmp_path / "inside.txt"
    target.write_text("real", encoding="utf-8")
    link = tmp_path / "inside-link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks unavailable")

    workspace, tempdir = sandbox_mod._copiar_projeto(str(tmp_path), sandbox_mod._limites(_cfg(memoria_mb=512)))
    try:
        assert os.path.isfile(os.path.join(workspace, "inside.txt"))
        assert not os.path.lexists(os.path.join(workspace, "inside-link.txt"))
    finally:
        tempdir.cleanup()


def test_safe_sandbox_cwd_rejects_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    import pytest
    with pytest.raises(sandbox_mod.ErroSandbox, match="escapar"):
        sandbox_mod._safe_sandbox_cwd(str(workspace), "../")


def test_process_backend_timeout_kills_execution(tmp_path):
    cfg = _cfg(
        comandos_permitidos=[[sys.executable]],
        timeout_segundos=1,
        cpu_segundos=3,
        memoria_mb=512,
    )
    result = sandbox_mod.executar_no_sandbox(
        str(tmp_path),
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cfg,
    )
    assert result["executado"] is True
    assert result["ok"] is False
    assert "timeout de 1s" in result["erro"]


def test_process_backend_returns_only_bounded_output_tail(tmp_path):
    cfg = _cfg(
        comandos_permitidos=[[sys.executable]],
        max_saida_kb=16,
        memoria_mb=512,
    )
    result = sandbox_mod.executar_no_sandbox(
        str(tmp_path),
        [sys.executable, "-c", "print('A'*20000); print('TAIL-MARKER')"],
        cfg,
    )
    assert result["ok"] is True
    assert result["saida"].endswith("TAIL-MARKER\n")
    assert len(result["saida"].encode("utf-8")) <= 16 * 1024


def test_timeout_kills_spawned_child_process_tree(tmp_path):
    import shutil
    import time
    if os.name != "posix" or not shutil.which("prlimit"):
        import pytest
        pytest.skip("POSIX prlimit required")
    marker = tmp_path.parent / (tmp_path.name + "-child-marker")
    if marker.exists():
        marker.unlink()
    child = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',\"import time; time.sleep(2); open({str(marker)!r},'w').write('alive')\"]); "
        "time.sleep(10)"
    )
    cfg = _cfg(comandos_permitidos=[[sys.executable]], timeout_segundos=1, cpu_segundos=4, memoria_mb=512)
    result = sandbox_mod.executar_no_sandbox(str(tmp_path), [sys.executable, "-c", child], cfg)
    assert result["executado"] is True and result["ok"] is False
    time.sleep(2.2)
    assert not marker.exists(), "child survived timeout process-group cleanup"


def test_supervised_backend_auto_fallback_is_explicit(monkeypatch):
    monkeypatch.setattr(sandbox_mod, "_microsandbox_available", lambda: False)
    real_which = sandbox_mod.shutil.which
    monkeypatch.setattr(sandbox_mod.shutil, "which", lambda name: None if name in {"bwrap", "docker"} else real_which(name))
    assert sandbox_mod._supervised_backend({"backend": "auto", "bloquear_rede": False}) == "process"
    import pytest
    with pytest.raises(sandbox_mod.ErroSandbox, match="nenhum backend supervisionado"):
        sandbox_mod._supervised_backend({"backend": "auto", "bloquear_rede": True})


def test_docker_container_init_failure_is_fail_closed(monkeypatch, tmp_path):
    class Completed:
        returncode = 125
        stdout = ""
        stderr = "daemon unavailable"

    monkeypatch.setattr(sandbox_mod.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(sandbox_mod.subprocess, "run", lambda *args, **kwargs: Completed())
    import pytest
    with pytest.raises(sandbox_mod.ErroSandbox, match="Docker sandbox indisponivel"):
        sandbox_mod._ensure_docker_container(str(tmp_path), {"imagem_oci": "python:3.12-slim"}, sandbox_mod._limites(_cfg(memoria_mb=512)))


def test_execution_context_cleans_persistent_docker_and_snapshot(monkeypatch):
    from eyle.runtime.execution_context import ExecutionContext

    calls = []
    class Temp:
        cleaned = False
        def cleanup(self):
            self.cleaned = True

    temp = Temp()
    monkeypatch.setattr(sandbox_mod.subprocess, "run", lambda argv, **kwargs: calls.append(list(argv)))
    ctx = ExecutionContext(
        started_monotonic=0.0, deadline_monotonic=10.0, execution_id="t", source_job_id=1,
    )
    state = sandbox_mod._sandbox_state(ctx)
    state["tempdir"] = temp
    state["workspace_path"] = "/tmp/fake-workspace"
    state["container_name"] = "eyle-sandbox-test"
    state["docker_binary"] = "/usr/bin/docker"
    state["backend"] = "docker"
    ctx.cleanup()
    assert calls == [["/usr/bin/docker", "rm", "-f", "eyle-sandbox-test"]]
    assert temp.cleaned is True
    assert state == {}
