#!/usr/bin/env python3
"""Atualizacao 34: config tipada e falha cedo."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config_schema import ConfigError, carregar_config_validada, validar_config  # noqa: E402


def test_config_valida_e_aceita_comentarios_desconhecidos():
    config = {
        "llm": {
            "provider": "ollama", "base_url": "http://localhost:11434",
            "model": "modelo", "temperature": 0.2,
        },
        "codar": {"testes": {"comando_python": ["python", "-m", "pytest"]}},
        "agent": {"enabled": False, "_comentario": "preservado"},
    }
    assert validar_config(config) is config


def test_config_invalida_lista_todos_os_erros(tmp_path):
    caminho = tmp_path / "config.json"
    caminho.write_text(json.dumps({
        "llm": {"base_url": "sem-protocolo", "temperature": 9},
        "servidor": {"port": 99999},
        "agent": {"enabled": "sim"},
    }), encoding="utf-8")

    try:
        carregar_config_validada(caminho)
    except ConfigError as erro:
        mensagem = str(erro)
    else:
        raise AssertionError("config invalida deveria falhar")

    assert "llm.base_url" in mensagem
    assert "llm.temperature" in mensagem
    assert "servidor.port" in mensagem
    assert "agent.enabled" in mensagem


def test_config_ausente_falha_em_vez_de_virar_dict_vazio(tmp_path):
    try:
        carregar_config_validada(tmp_path / "nao_existe.json")
    except ConfigError as erro:
        assert "nao encontrado" in str(erro)
    else:
        raise AssertionError("config ausente deveria falhar")


def test_limites_de_leitura_do_agente_precisam_ser_positivos():
    try:
        validar_config({
            "agent": {
                "max_tree_entries": 0,
                "max_tree_depth": 0,
                "max_read_range_lines": 0,
            }
        })
    except ConfigError as erro:
        mensagem = str(erro)
    else:
        raise AssertionError("limites zerados deveriam falhar")

    assert "agent.max_tree_entries" in mensagem
    assert "agent.max_tree_depth" in mensagem
    assert "agent.max_read_range_lines" in mensagem


def test_context_window_4080_e_orcamento_dinamico_sao_validos():
    config = {
        "llm": {"context_window_tokens": 4080, "max_tokens": 700},
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "max_recent_observations": 4,
        },
    }
    assert validar_config(config) is config


def test_resposta_e_margem_nao_podem_consumir_a_janela_inteira():
    config = {
        "llm": {"context_window_tokens": 1000, "max_tokens": 700},
        "context_engine": {
            "safety_margin_tokens": 300,
            "chars_per_token_fallback": 3,
            "max_recent_observations": 4,
        },
    }
    with pytest.raises(ConfigError) as erro:
        validar_config(config)
    assert "precisa ser menor que llm.context_window_tokens" in str(erro.value)
