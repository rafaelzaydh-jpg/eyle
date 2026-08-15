#!/usr/bin/env python3
"""Atomic JSON persistence tests."""
import json
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eyle.runtime import storage as persistence  # noqa: E402


def test_salvar_json_atomico_substitui_destino_e_preserva_permissao(tmp_path):
    caminho = tmp_path / "memoria.json"
    caminho.write_text('{"antes": true}', encoding="utf-8")
    caminho.chmod(0o640)

    persistence.salvar_json_atomico(caminho, {"depois": True})

    assert json.loads(caminho.read_text(encoding="utf-8")) == {"depois": True}
    assert stat.S_IMODE(caminho.stat().st_mode) == 0o640
    assert not list(tmp_path.glob(".memoria.json.*.tmp"))


def test_falha_antes_do_replace_mantem_json_anterior(monkeypatch, tmp_path):
    caminho = tmp_path / "memoria.json"
    original = '{"inteiro": true}\n'
    caminho.write_text(original, encoding="utf-8")

    def falhar_dump(*args, **kwargs):
        args[1].write('{"truncated":')
        raise OSError("interrupcao simulada")

    monkeypatch.setattr(persistence.json, "dump", falhar_dump)
    try:
        persistence.salvar_json_atomico(caminho, {"novo": True})
    except OSError:
        pass
    else:
        raise AssertionError("a falha simulada deveria ser propagada")

    assert caminho.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".memoria.json.*.tmp"))
