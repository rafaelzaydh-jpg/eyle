#!/usr/bin/env python3
"""Shared safe-path boundary tests."""
import os
import pytest

from tests.canonical import standard_registry
from eyle.providers.standard_impl.security import _resolver_caminho_seguro


def _ctx(raiz):
    return {
        "provider_context": {"standard": {"caminho_origem": str(raiz)}},
        "config": {"agent": {"max_file_read_lines": 400}},
    }


def test_caminho_normal_dentro_do_projeto_continua_funcionando(tmp_path):
    raiz = tmp_path / "projeto"
    arquivo = raiz / "src" / "normal.py"
    arquivo.parent.mkdir(parents=True)
    arquivo.write_text("valor = 42\n", encoding="utf-8")

    resolvido = _resolver_caminho_seguro(raiz, "src/normal.py")
    assert resolvido == os.path.realpath(arquivo)

    resultado = standard_registry().execute(
        "standard.read_file", {"path": "src/normal.py"}, _ctx(raiz),
    )
    assert resultado["status"] == "success"
    assert resultado["detail"]["content"] == "valor = 42\n"
    assert resultado["detail"]["truncated"] is False


def test_read_file_rejeita_travessia_sem_vazar_conteudo(tmp_path):
    raiz = tmp_path / "projeto"
    raiz.mkdir()
    segredo = tmp_path / "fora.txt"
    segredo.write_text("SEGREDO_QUE_NAO_PODE_VAZAR", encoding="utf-8")

    resultado = standard_registry().execute(
        "standard.read_file", {"path": "../fora.txt"}, _ctx(raiz),
    )

    assert resultado["status"] == "failed"
    assert resultado["error_code"] == "UNSAFE_PATH"
    assert "caminho inseguro rejeitado" in resultado["detail"]
    assert "SEGREDO_QUE_NAO_PODE_VAZAR" not in str(resultado)


def test_caminho_absoluto_e_rejeitado_mesmo_quando_aponta_para_dentro(tmp_path):
    raiz = tmp_path / "projeto"
    raiz.mkdir()
    arquivo = raiz / "interno.txt"
    arquivo.write_text("interno", encoding="utf-8")

    assert _resolver_caminho_seguro(raiz, str(arquivo)) is None
    resultado = standard_registry().execute(
        "standard.read_file", {"path": str(arquivo)}, _ctx(raiz),
    )
    assert resultado["status"] == "failed"
    assert resultado["error_code"] in {"UNSAFE_PATH", "FILE_READ_REJECTED"}


def test_symlink_para_fora_do_projeto_e_rejeitado(tmp_path):
    raiz = tmp_path / "projeto"
    raiz.mkdir()
    segredo = tmp_path / "segredo.txt"
    segredo.write_text("NAO_LER", encoding="utf-8")
    atalho = raiz / "atalho.txt"
    try:
        atalho.symlink_to(segredo)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable on this host")

    assert _resolver_caminho_seguro(raiz, "atalho.txt") is None
    resultado = standard_registry().execute(
        "standard.read_file", {"path": "atalho.txt"}, _ctx(raiz),
    )
    assert resultado["status"] == "failed"
    assert "caminho inseguro rejeitado" in resultado["detail"]
    assert "NAO_LER" not in str(resultado)
