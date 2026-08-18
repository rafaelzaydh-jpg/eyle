#!/usr/bin/env python3
"""Atomic-write primitives used by the canonical transaction engine."""
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eyle.providers.standard.editing as codar_mod  # noqa: E402


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
    if os.name == "posix":
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
