#!/usr/bin/env python3
"""Revisao 51: regressões dos limites operacionais e caches."""
from contextlib import closing
import io
import json
import os
import sqlite3
import sys
import time
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod  # noqa: E402
from engine.agent_state import AgentState  # noqa: E402
from engine.config_schema import ConfigError, validar_config  # noqa: E402
from engine import queue  # noqa: E402
import llm.cache as cache_mod  # noqa: E402
import llm.executar as llm_mod  # noqa: E402
import retrieval.buscar as busca_mod  # noqa: E402


class _Resposta:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def close(self):
        return None


def _cfg_llm(**updates):
    llm = {
        "provider": "ollama",
        "base_url": "http://localhost:8080",
        "model": "teste",
        "openai_compatible": False,
        "temperature": 0.2,
        "timeout_seconds": 2,
        "connect_timeout_seconds": 1,
        "read_timeout_seconds": 2,
        "retry_max_attempts": 3,
        "retry_base_delay_seconds": 0,
        "retry_max_delay_seconds": 0,
        "retry_jitter_seconds": 0,
        "max_concurrent_requests": 1,
        "cache": {"ativado": False},
    }
    llm.update(updates)
    return {"llm": llm}


def test_parser_ignora_objeto_tool_incompleto_e_usa_final_valido():
    bruto = 'rascunho {"tool":"exemplo"} depois {"final":"resposta real"}'
    assert agent_mod._parse_decisao_agente(bruto) == {"final": "resposta real"}


def test_parser_rejeita_envelope_com_dois_ramos():
    bruto = '{"tool":"list_tree","arguments":{},"final":"nao"}'
    assert agent_mod._parse_decisao_agente(bruto) is None


def test_orcamento_central_impede_nova_chamada_antes_do_backend(monkeypatch):
    chamadas = []

    def responder(req, timeout=None):
        chamadas.append(req.full_url)
        return _Resposta({"message": {"content": "ok"}})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", responder)
    config = _cfg_llm()
    config["_runtime_agent_budget"] = {
        "deadline_monotonic": time.monotonic() + 10,
        "max_llm_calls": 1,
        "max_generated_tokens": 1000,
        "llm_calls": 0,
        "generated_tokens": 0,
    }
    assert llm_mod._chamar_llm("s", "u1", config) == "ok"
    with pytest.raises(llm_mod.ErroLLM, match="limite global") as erro:
        llm_mod._chamar_llm("s", "u2", config)
    assert erro.value.error_code == "MAX_LLM_CALLS_EXCEEDED"
    assert len(chamadas) == 1


def test_config_rejeita_limites_operacionais_zerados():
    with pytest.raises(ConfigError) as erro:
        validar_config({
            "llm": {"timeout_seconds": 0, "retry_max_attempts": 0},
            "agent": {
                "max_steps": 0, "max_tentativas_parse": 0,
                "max_erros_consecutivos": 0,
            },
        })
    mensagem = str(erro.value)
    assert "llm.timeout_seconds" in mensagem
    assert "agent.max_steps" in mensagem
    assert "agent.max_tentativas_parse" in mensagem
    assert "agent.max_erros_consecutivos" in mensagem


def test_config_rejeita_schema_legado_divergente():
    with pytest.raises(ConfigError, match="config_schema_version"):
        validar_config({
            "app_version": "2.7.1",
            "config_schema_version": "2.6.1",
            "version": "2.7.1",
        })


def test_cache_nao_grava_resposta_vazia(tmp_path):
    assert cache_mod.definir(tmp_path, "backend", "s", "u", "") is False
    caminho = tmp_path / "context" / cache_mod.NOME_ARQUIVO
    with closing(sqlite3.connect(caminho)) as conexao:
        assert conexao.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0] == 0


def test_cache_remove_erro_legado_na_primeira_leitura(tmp_path):
    chave = cache_mod._chave("backend", "s", "u")
    caminho_legado = tmp_path / "context" / cache_mod.NOME_LEGADO
    caminho_legado.parent.mkdir(parents=True)
    caminho_legado.write_text(json.dumps({
        "version": "2.0",
        "entradas": {chave: {"resposta": "[erro] quebrado", "hits": 0}},
    }), encoding="utf-8")

    assert cache_mod.obter(tmp_path, "backend", "s", "u") is None
    with closing(sqlite3.connect(tmp_path / "context" / cache_mod.NOME_ARQUIVO)) as conexao:
        assert conexao.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0] == 0


def test_model_discovery_falha_so_uma_vez_durante_ttl(monkeypatch):
    llm_mod._MODELOS_OPENAI.clear()
    chamadas = []

    def falhar(req, timeout=None):
        chamadas.append(req.full_url)
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", falhar)
    assert llm_mod._detectar_modelos_openai("http://x", 1, negative_ttl=60) == []
    assert llm_mod._detectar_modelos_openai("http://x", 1, negative_ttl=60) == []
    assert len(chamadas) == 1


def test_retry_transitorio_recupera_na_terceira_tentativa(monkeypatch):
    chamadas = []

    def responder(req, timeout=None):
        chamadas.append(req.full_url)
        if len(chamadas) < 3:
            raise urllib.error.HTTPError(
                req.full_url, 503, "ocupado", {}, io.BytesIO(b"temporario"),
            )
        return _Resposta({"message": {"content": "ok"}})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", responder)
    resposta = llm_mod._chamar_llm("s", "u", _cfg_llm())
    assert resposta == "ok"
    assert len(chamadas) == 3


def test_erro_http_permanente_nao_repete(monkeypatch):
    chamadas = []

    def responder(req, timeout=None):
        chamadas.append(req.full_url)
        raise urllib.error.HTTPError(
            req.full_url, 400, "ruim", {}, io.BytesIO(b"payload invalido"),
        )

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", responder)
    with pytest.raises(llm_mod.ErroLLM) as erro:
        llm_mod._chamar_llm("s", "u", _cfg_llm())
    assert erro.value.transient is False
    assert len(chamadas) == 1


def test_bm25_reutiliza_indice_sem_mudanca(tmp_path, monkeypatch):
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "chunks.jsonl").write_text(
        json.dumps({
            "arquivo": "a.py", "simbolo": "f", "texto": "timeout servidor",
            "linha_inicio": 1, "linha_fim": 1, "tokens": 2,
        }) + "\n",
        encoding="utf-8",
    )
    busca_mod.invalidar_cache_bm25(memory)
    original = busca_mod.carregar_chunks
    chamadas = []

    def contar(memory_dir):
        chamadas.append(1)
        return original(memory_dir)

    monkeypatch.setattr(busca_mod, "carregar_chunks", contar)
    busca_mod.buscar("timeout", memory_dir=memory)
    busca_mod.buscar("servidor", memory_dir=memory)
    assert len(chamadas) == 1


def test_heartbeat_persistente(monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "fila.sqlite3"))
    queue.registrar_heartbeat("worker-teste", "processing", job_id=7)
    salvo = queue.obter_heartbeat("worker-teste")
    assert salvo["status"] == "processing"
    assert salvo["job_id"] == 7


def test_busca_semanticamente_equivalente_e_redundante():
    estado = AgentState({"agent": {"semantic_repeat_overlap": 0.9}})
    resultado = {
        "status": "success", "ok": True, "executed": True,
        "changed": False, "error_code": None, "detail": {"resultados": []},
    }
    estado.registrar_chamada("search_code", {"pergunta": "problema de timeout"})
    estado.registrar_acao(
        "search_code", {"pergunta": "problema de timeout"},
        resultado, contar_execucao=True,
    )
    assert estado.chamada_repetida("search_code", {"pergunta": "timeout"}) is True


def test_identidade_release_detecta_manifesto_divergente(tmp_path):
    from engine.release_identity import ReleaseIdentityError, validar_identidade_release

    (tmp_path / "config.json").write_text(json.dumps({
        "app_version": "2.7.1",
        "config_schema_version": "2.7.1",
        "revision": "51.0-hardening",
    }), encoding="utf-8")
    (tmp_path / "release_manifest.json").write_text(json.dumps({
        "release": "2.7.0",
        "app_version": "2.7.0",
        "config_schema_version": "2.7.1",
        "revision": "51.0-hardening",
    }), encoding="utf-8")
    (tmp_path / "README.md").write_text("sem marcador", encoding="utf-8")

    with pytest.raises(ReleaseIdentityError, match="divergente"):
        validar_identidade_release(tmp_path)
