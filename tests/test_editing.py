#!/usr/bin/env python3
"""Testes da Atualizacao 19: rollback sem backup e escrita atomica."""
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eyle.core.editing as codar_mod  # noqa: E402


def test_rollback_restaura_bytes_originais_sem_backup(monkeypatch, tmp_path):
    arquivo = tmp_path / "modulo.py"
    original = b"def valor():\n    return 1\n"
    arquivo.write_bytes(original)

    def falhar_parse(*args, **kwargs):
        raise SyntaxError("falha simulada", ("modulo.py", 1, 1, "def valor():"))

    monkeypatch.setattr(codar_mod.ast, "parse", falhar_parse)

    resultado = codar_mod.aplicar_patch(
        str(tmp_path), "modulo.py", 1, 2,
        "def valor():\n    return 1", "def valor():\n    return 2",
        backups_dir=None, cfg_testes={"ativado": False},
    )

    assert resultado["ok"] is False
    assert resultado["changed"] is False
    assert resultado["backup_path"] is None
    assert "Revertido automaticamente" in resultado["detalhe"]
    assert arquivo.read_bytes() == original


def test_escrita_atomica_usa_replace_e_preserva_permissoes(monkeypatch, tmp_path):
    arquivo = tmp_path / "dados.txt"
    arquivo.write_text("antes", encoding="utf-8")
    arquivo.chmod(0o640)
    replace_real = codar_mod.os.replace
    chamadas = []

    def replace_observado(origem, destino):
        chamadas.append((origem, destino))
        return replace_real(origem, destino)

    monkeypatch.setattr(codar_mod.os, "replace", replace_observado)
    codar_mod._escrever_arquivo_atomico(str(arquivo), "depois")

    assert arquivo.read_text(encoding="utf-8") == "depois"
    assert stat.S_IMODE(arquivo.stat().st_mode) == 0o640
    assert len(chamadas) == 1
    assert os.path.dirname(chamadas[0][0]) == str(tmp_path)
    assert chamadas[0][1] == str(arquivo)
    assert not list(tmp_path.glob(".dados.txt.eyle-*"))


def test_falha_antes_do_replace_nao_trunca_destino(monkeypatch, tmp_path):
    arquivo = tmp_path / "dados.txt"
    arquivo.write_bytes(b"conteudo original")

    def replace_falho(*args, **kwargs):
        raise OSError("falha simulada no replace")

    monkeypatch.setattr(codar_mod.os, "replace", replace_falho)

    try:
        codar_mod._escrever_arquivo_atomico(str(arquivo), "conteudo novo")
    except OSError as erro:
        assert "falha simulada" in str(erro)
    else:
        raise AssertionError("a falha simulada deveria ter sido propagada")

    assert arquivo.read_bytes() == b"conteudo original"
    assert not list(tmp_path.glob(".dados.txt.eyle-*"))


def test_escrita_atomica_nao_depende_de_fchmod(monkeypatch, tmp_path):
    arquivo = tmp_path / "windows.txt"
    arquivo.write_text("antes", encoding="utf-8")

    monkeypatch.setattr(
        codar_mod.shutil, "copymode",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("modo indisponivel")),
    )
    codar_mod._escrever_arquivo_atomico(str(arquivo), "depois")

    assert arquivo.read_text(encoding="utf-8") == "depois"
    assert not list(tmp_path.glob(".windows.txt.eyle-*"))
