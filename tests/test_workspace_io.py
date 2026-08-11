#!/usr/bin/env python3
"""Atualizacao 41: arvore atual e leitura fresca por faixa."""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eyle.core.workspace_io import (  # noqa: E402
    ErroLeituraProjeto,
    ler_faixa_projeto,
    listar_arvore_projeto,
)


def _audio_14_linhas():
    return "".join(f"valor_{numero} = {numero}\n" for numero in range(1, 15))


def test_file_read_range_devolve_as_14_linhas_numeradas_e_hash_fresco(tmp_path):
    conteudo = _audio_14_linhas()
    (tmp_path / "audio.py").write_text(conteudo, encoding="utf-8")

    resultado = ler_faixa_projeto(tmp_path, "audio.py", 1, 14, max_linhas=14)

    assert resultado["line_start"] == 1
    assert resultado["line_end"] == 14
    assert "     1 | valor_1 = 1" in resultado["numbered_content"]
    assert "    14 | valor_14 = 14" in resultado["numbered_content"]
    assert resultado["content_hash"] == hashlib.sha256(conteudo.encode("utf-8")).hexdigest()
    assert resultado["file_hash"] == hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def test_file_read_range_recusa_janela_acima_do_limite_e_travessia(tmp_path):
    (tmp_path / "a.py").write_text("1\n2\n3\n4\n", encoding="utf-8")

    try:
        ler_faixa_projeto(tmp_path, "a.py", 1, 4, max_linhas=3)
    except ErroLeituraProjeto as erro:
        assert erro.error_code == "RANGE_TOO_LARGE"
        assert "limite configurado" in erro.detail
    else:
        raise AssertionError("faixa acima do limite deveria falhar")

    try:
        ler_faixa_projeto(tmp_path, "../fora.py", 1, 1, max_linhas=3)
    except ErroLeituraProjeto as erro:
        assert erro.error_code == "UNSAFE_PATH"
    else:
        raise AssertionError("travessia deveria falhar")


def test_list_tree_respeita_filtro_limites_e_motivos_ignorados(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "audio.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("docs\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secreto\n", encoding="utf-8")
    (tmp_path / "data.bin").write_bytes(b"\x00\x01")
    (tmp_path / "ignorado.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignorado.py\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pacote.js").write_text("x\n", encoding="utf-8")

    resultado = listar_arvore_projeto(
        tmp_path, limite=20, profundidade=3, filtro="*.py",
    )

    caminhos = [item["path"] for item in resultado["entries"]]
    assert caminhos == ["src/audio.py"]
    assert resultado["ignorados_por_motivo"]["gitignore"] >= 1
    assert "segredo" not in resultado["ignorados_por_motivo"]

    completo = listar_arvore_projeto(tmp_path, limite=20, profundidade=3)
    env_entry = next(item for item in completo["entries"] if item["path"] == ".env")
    assert env_entry["content_access"] == "protected"
    assert completo["protected_resources"] == 1
    assert resultado["ignorados_por_motivo"]["padrao_interno"] >= 1
    assert resultado["ignorados_por_motivo"]["extensao_nao_suportada"] >= 1
    assert resultado["ignorados_por_motivo"]["filter"] >= 1

    limitado = listar_arvore_projeto(tmp_path, limite=1, profundidade=3)
    assert limitado["total_retornado"] == 1
    assert limitado["truncated"] is True
    assert limitado["varredura_completa"] is False


