#!/usr/bin/env python3
"""Atualizacao 41: arvore atual e leitura fresca por faixa."""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod  # noqa: E402
from engine.project_reader import (  # noqa: E402
    ErroLeituraProjeto,
    ler_faixa_projeto,
    listar_arvore_projeto,
)


def _audio_14_linhas():
    return "".join(f"valor_{numero} = {numero}\n" for numero in range(1, 15))


def test_read_range_devolve_as_14_linhas_numeradas_e_hash_fresco(tmp_path):
    conteudo = _audio_14_linhas()
    (tmp_path / "audio.py").write_text(conteudo, encoding="utf-8")

    resultado = ler_faixa_projeto(tmp_path, "audio.py", 1, 14, max_linhas=14)

    assert resultado["linha_inicio"] == 1
    assert resultado["linha_fim"] == 14
    assert "     1 | valor_1 = 1" in resultado["trecho_numerado"]
    assert "    14 | valor_14 = 14" in resultado["trecho_numerado"]
    assert resultado["content_hash"] == hashlib.sha256(conteudo.encode("utf-8")).hexdigest()
    assert resultado["file_hash"] == hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def test_read_range_recusa_janela_acima_do_limite_e_travessia(tmp_path):
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

    caminhos = [item["caminho"] for item in resultado["entradas"]]
    assert caminhos == ["src/audio.py"]
    assert resultado["ignorados_por_motivo"]["gitignore"] >= 1
    assert resultado["ignorados_por_motivo"]["segredo"] >= 1
    assert resultado["ignorados_por_motivo"]["padrao_interno"] >= 1
    assert resultado["ignorados_por_motivo"]["extensao_nao_suportada"] >= 1
    assert resultado["ignorados_por_motivo"]["filtro"] >= 1

    limitado = listar_arvore_projeto(tmp_path, limite=1, profundidade=3)
    assert limitado["total_retornado"] == 1
    assert limitado["truncado"] is True
    assert limitado["varredura_completa"] is False


def test_agente_recebe_audio_inteiro_antes_de_finalizar(tmp_path, monkeypatch):
    (tmp_path / "audio.py").write_text(_audio_14_linhas(), encoding="utf-8")
    chamadas = []

    def fake_llm(prompt, config):
        chamadas.append(prompt)
        if len(chamadas) == 1:
            assert "TOOL CATALOG" in prompt
            assert '"name":"read_range"' in prompt
            return json.dumps({
                "tool": "read_range",
                "arguments": {
                    "caminho_relativo": "audio.py",
                    "linha_inicio": 1,
                    "linha_fim": 14,
                },
            })
        assert "1 | valor_1 = 1" in prompt
        assert "14 | valor_14 = 14" in prompt
        return '{"final":"audio.py analisado a partir das 14 linhas reais"}'

    monkeypatch.setattr(agent_mod, "executar_agente_llm", fake_llm)
    config = {
        "agent": {
            "max_steps": 2,
            "max_tentativas_parse": 1,
            "max_chars_por_observacao": 2000,
            "max_erros_consecutivos": 3,
            "max_fatos_importantes": 10,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
            "exigir_run_tests_apos_escrita": True,
        }
    }

    status, texto, pendente = agent_mod.executar_agente(
        "analise este arquivo audio.py",
        config,
        projeto={"caminho_origem": str(tmp_path)},
    )

    assert status == "success"
    assert "14 atribuições" in texto
    assert "`valor_1` a `valor_14`" in texto
    assert pendente is None
    assert len(chamadas) == 2
