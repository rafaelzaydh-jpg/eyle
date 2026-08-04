#!/usr/bin/env python3
"""Testes do Verify honesto (Atualizacoes 20 e 30)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verify.validar import validar_resposta  # noqa: E402


def _gravar_estrutura(memory_dir, arquivos=None):
    memory_dir.mkdir()
    (memory_dir / "estrutura.json").write_text(
        json.dumps({"arquivos": arquivos or {"src/app.py": {"linhas": 20}}}),
        encoding="utf-8",
    )


def test_resposta_sem_citacao_tem_confianca_indeterminada(tmp_path):
    memory_dir = tmp_path / "memory"
    _gravar_estrutura(memory_dir)

    resultado = validar_resposta("Resposta sem referencia verificavel.", str(memory_dir))

    assert resultado["total_mencoes_verificadas"] == 0
    assert resultado["confianca"] is None
    assert resultado["citation_validity"] is None
    assert resultado["coverage"] is None
    assert resultado["grounding"] is None
    assert resultado["verificacao_aprovada"] is None


def test_citacao_existente_continua_sendo_verificada(tmp_path):
    memory_dir = tmp_path / "memory"
    _gravar_estrutura(memory_dir)

    resultado = validar_resposta("Veja src/app.py:2-5.", str(memory_dir), ["src/app.py"])

    assert resultado["total_mencoes_verificadas"] == 1
    assert resultado["confirmadas"] == 1
    assert resultado["confianca"] == 1.0
    assert resultado["citation_validity"] == 1.0
    assert resultado["coverage"] == 1.0
    assert resultado["grounding"] == 1.0
    assert resultado["verificacao_aprovada"] is True


def test_linha_final_fora_do_arquivo_reprova_a_citacao(tmp_path):
    memory_dir = tmp_path / "memory"
    _gravar_estrutura(memory_dir)

    resultado = validar_resposta("Veja src/app.py:1-999.", str(memory_dir), ["src/app.py"])

    assert resultado["confirmadas"] == 0
    assert resultado["citation_validity"] == 0.0
    assert resultado["grounding"] == 0.0
    assert resultado["coverage"] == 0.0
    assert resultado["verificacao_aprovada"] is False
    assert any("Linha final 999" in aviso for aviso in resultado["avisos"])


def test_metricas_separam_validade_cobertura_e_grounding(tmp_path):
    memory_dir = tmp_path / "memory"
    _gravar_estrutura(memory_dir, {
        "src/app.py": {"linhas": 20},
        "src/helper.py": {"linhas": 10},
        "docs/guia.md": {"linhas": 30},
    })

    resultado = validar_resposta(
        "A base esta em src/app.py:2-5; veja tambem docs/guia.md:1-2.",
        str(memory_dir),
        ["src/app.py", "src/helper.py"],
    )

    # As duas referencias existem e tem faixas validas...
    assert resultado["citation_validity"] == 1.0
    # ...mas so uma das duas veio do contexto, e so metade do contexto foi citada.
    assert resultado["grounding"] == 0.5
    assert resultado["coverage"] == 0.5
    assert resultado["confianca"] == 0.5
    assert resultado["verificacao_aprovada"] is False


def test_nome_base_ambiguo_nao_e_confirmado_por_acaso(tmp_path):
    memory_dir = tmp_path / "memory"
    _gravar_estrutura(memory_dir, {
        "src/app.py": {"linhas": 20},
        "tests/app.py": {"linhas": 12},
    })

    resultado = validar_resposta("Veja app.py:2-5.", str(memory_dir), ["src/app.py"])

    assert resultado["citation_validity"] == 0.0
    assert resultado["confirmadas"] == 0
    assert any("ambiguo" in aviso for aviso in resultado["avisos"])
