#!/usr/bin/env python3
"""Atualizacao 28 -- sandbox fail-closed, allowlist e limites."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eyle.core.editing as codar_mod  # noqa: E402
import eyle.core.sandbox as sandbox_mod  # noqa: E402


def _cfg(**extras):
    cfg = {
        "backend": "processo",
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


def test_backend_processo_nao_finge_bloquear_rede(tmp_path):
    resultado = sandbox_mod.executar_no_sandbox(
        str(tmp_path), ["python", "-m", "pytest"],
        _cfg(bloquear_rede=True),
    )

    assert resultado["executado"] is False
    assert "nao bloqueia rede" in resultado["erro"]


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
