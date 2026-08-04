#!/usr/bin/env python3
"""Testes da Atualizacao 18 -- caminho seguro compartilhado."""
import os

from engine.agent_tools import executar_tool
from engine.dicas import ler_codigo_real
from engine.seguranca import _resolver_caminho_seguro


def _ctx(raiz):
    return {
        "projeto": {"caminho_origem": str(raiz)},
        "config": {"dicas": {"max_chars_por_arquivo": 20000}},
    }


def test_caminho_normal_dentro_do_projeto_continua_funcionando(tmp_path):
    raiz = tmp_path / "projeto"
    arquivo = raiz / "src" / "normal.py"
    arquivo.parent.mkdir(parents=True)
    arquivo.write_text("valor = 42\n", encoding="utf-8")

    resolvido = _resolver_caminho_seguro(raiz, "src/normal.py")
    assert resolvido == os.path.realpath(arquivo)

    resultado = executar_tool(
        "read_file", {"caminho_relativo": "src/normal.py"}, _ctx(raiz),
    )
    assert resultado["status"] == "success"
    assert resultado["detail"]["conteudo"] == "valor = 42\n"
    assert resultado["detail"]["truncado"] is False


def test_read_file_rejeita_travessia_sem_vazar_conteudo(tmp_path):
    raiz = tmp_path / "projeto"
    raiz.mkdir()
    segredo = tmp_path / "fora.txt"
    segredo.write_text("SEGREDO_QUE_NAO_PODE_VAZAR", encoding="utf-8")

    resultado = executar_tool(
        "read_file", {"caminho_relativo": "../fora.txt"}, _ctx(raiz),
    )

    assert resultado["status"] == "failed"
    assert resultado["error_code"] == "FILE_READ_REJECTED"
    assert "caminho inseguro rejeitado" in resultado["detail"]
    assert "SEGREDO_QUE_NAO_PODE_VAZAR" not in str(resultado)


def test_caminho_absoluto_e_rejeitado_mesmo_quando_aponta_para_dentro(tmp_path):
    raiz = tmp_path / "projeto"
    raiz.mkdir()
    arquivo = raiz / "interno.txt"
    arquivo.write_text("interno", encoding="utf-8")

    assert _resolver_caminho_seguro(raiz, str(arquivo)) is None
    resultado = ler_codigo_real([str(arquivo)], str(raiz))
    assert "erro" in resultado[str(arquivo)]
    assert "conteudo" not in resultado[str(arquivo)]


def test_symlink_para_fora_do_projeto_e_rejeitado(tmp_path):
    raiz = tmp_path / "projeto"
    raiz.mkdir()
    segredo = tmp_path / "segredo.txt"
    segredo.write_text("NAO_LER", encoding="utf-8")
    atalho = raiz / "atalho.txt"
    atalho.symlink_to(segredo)

    assert _resolver_caminho_seguro(raiz, "atalho.txt") is None
    resultado = executar_tool(
        "read_file", {"caminho_relativo": "atalho.txt"}, _ctx(raiz),
    )
    assert resultado["status"] == "failed"
    assert "caminho inseguro rejeitado" in resultado["detail"]
    assert "NAO_LER" not in str(resultado)
