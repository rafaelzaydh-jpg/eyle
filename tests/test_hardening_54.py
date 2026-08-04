#!/usr/bin/env python3
"""Revisao 54: token orientado e cache LLM agressivo em duas camadas."""
from pathlib import Path
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config_schema import validar_config
from llm import cache as cache_mod
from llm import executar as llm_mod


@pytest.fixture(autouse=True)
def _limpar_lru():
    cache_mod.limpar_memoria()
    yield
    cache_mod.limpar_memoria()


def test_cache_em_memoria_evitar_segundo_acesso_ao_sqlite(monkeypatch, tmp_path):
    assert cache_mod.definir(
        tmp_path, "backend", "sistema", "usuario", "resposta",
        memoria_max_entradas=2048, max_age_hours=24,
    ) is True

    monkeypatch.setattr(
        cache_mod, "_connect",
        lambda *_: (_ for _ in ()).throw(AssertionError("SQLite nao deveria ser aberto")),
    )
    assert cache_mod.obter(
        tmp_path, "backend", "sistema", "usuario",
        memoria_max_entradas=2048, max_age_hours=24,
    ) == "resposta"


def test_cache_expira_por_ttl_absoluto_em_memoria_e_disco(monkeypatch, tmp_path):
    relogio = {"agora": 1_000.0}
    monkeypatch.setattr(cache_mod.time, "time", lambda: relogio["agora"])

    cache_mod.definir(
        tmp_path, "backend", "s", "u", "antiga",
        memoria_max_entradas=2048, max_age_hours=1,
    )
    relogio["agora"] += 3601

    assert cache_mod.obter(
        tmp_path, "backend", "s", "u",
        memoria_max_entradas=2048, max_age_hours=1,
    ) is None


def test_lru_em_memoria_respeita_limite(tmp_path):
    for indice in range(3):
        cache_mod.definir(
            tmp_path, "backend", "s", str(indice), f"r{indice}",
            memoria_max_entradas=2, max_age_hours=24,
        )
    assert len(cache_mod._MEMORY_CACHE) == 2
    primeira = cache_mod._chave_memoria(
        tmp_path, cache_mod._chave("backend", "s", "0"),
    )
    assert primeira not in cache_mod._MEMORY_CACHE




def test_max_entradas_zero_desativa_as_duas_camadas(tmp_path):
    cache_mod.definir(
        tmp_path, "backend", "s", "u", "nao deve permanecer",
        max_entradas=0, memoria_max_entradas=2048, max_age_hours=24,
    )
    assert cache_mod.obter(
        tmp_path, "backend", "s", "u",
        max_entradas=0, memoria_max_entradas=2048, max_age_hours=24,
    ) is None

def test_chamada_llm_identica_usa_backend_uma_vez(monkeypatch, tmp_path):
    chamadas = []
    monkeypatch.setattr(llm_mod, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(
        llm_mod, "_chamar_ollama",
        lambda *args, **kwargs: chamadas.append((args, kwargs)) or "resposta cacheavel",
    )
    monkeypatch.setattr(llm_mod.process_limiter, "acquire", lambda *a, **k: "slot")
    monkeypatch.setattr(llm_mod.process_limiter, "release", lambda *a, **k: True)

    config = {"llm": {
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "model": "teste",
        "temperature": 0.2,
        "openai_compatible": False,
        "retry_max_attempts": 1,
        "cache": {
            "ativado": True,
            "max_entradas": 4096,
            "memoria_max_entradas": 2048,
            "max_age_hours": 24,
        },
    }}

    assert llm_mod._chamar_llm("s", "u", config) == "resposta cacheavel"
    assert llm_mod._chamar_llm("s", "u", config) == "resposta cacheavel"
    assert len(chamadas) == 1


def test_config_aceita_novos_limites_de_cache():
    validada = validar_config({"llm": {"cache": {
        "ativado": True,
        "max_entradas": 4096,
        "memoria_max_entradas": 2048,
        "max_age_hours": 24,
    }}})
    assert validada["llm"]["cache"]["memoria_max_entradas"] == 2048


def test_navegador_explica_onde_encontrar_token_e_permite_tentar_de_novo():
    raiz = Path(__file__).resolve().parents[1]
    js = (raiz / "web" / "static" / "app.js").read_text(encoding="utf-8")
    html = (raiz / "web" / "templates" / "index.html").read_text(encoding="utf-8")

    assert "python main.py serve" in js
    assert "context/web_api_token.txt" in js
    assert "solicitarNovoToken" in js
    assert 'id="tokenBtn"' in html
