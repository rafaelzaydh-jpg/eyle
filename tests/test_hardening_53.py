#!/usr/bin/env python3
"""Revisao 53: ambiguidades, cache, ciclos e backoff de alto nivel."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import agent as agent_mod
from engine import engine as engine_mod
from engine import queue as queue_mod
from engine.agent_state import AgentState
from engine.config_schema import ConfigError, validar_config
from llm import cache as cache_mod
from llm import executar as llm_mod


class _Response:
    def __init__(self, content="ok"):
        self.content = content

    def read(self):
        return json.dumps({"message": {"content": self.content}}).encode("utf-8")

    def close(self):
        return None


def _llm_config(**updates):
    llm = {
        "provider": "ollama",
        "base_url": "http://localhost:8080",
        "model": "test",
        "openai_compatible": False,
        "temperature": 0.1,
        "timeout_seconds": 2,
        "connect_timeout_seconds": 1,
        "read_timeout_seconds": 2,
        "retry_max_attempts": 1,
        "retry_base_delay_seconds": 0,
        "retry_max_delay_seconds": 0,
        "retry_jitter_seconds": 0,
        "max_concurrent_requests": 1,
        "cache": {"ativado": True},
    }
    llm.update(updates)
    return {"llm": llm}


def test_parser_usa_ultima_de_duas_decisoes_validas():
    bruto = (
        '{"tool":"list_tree","arguments":{}} '
        '{"final":"nao execute a primeira"}'
    )
    assert agent_mod._parse_decisao_agente(bruto) == {"final": "nao execute a primeira"}


def test_cache_rejeita_envelopes_de_erro_estruturado(tmp_path):
    respostas = (
        '{"ok":false,"error_code":"TIMEOUT","detail":"falhou"}',
        '{"status":"failed","error":"backend indisponivel"}',
    )
    for indice, resposta in enumerate(respostas):
        assert cache_mod.resposta_cacheavel(resposta) is False
        assert cache_mod.definir(tmp_path, "backend", "s", str(indice), resposta) is False
        assert cache_mod.obter(tmp_path, "backend", "s", str(indice)) is None


def test_cache_estruturado_envenenado_e_removido_antes_do_backend(monkeypatch):
    invalidacoes = []
    chamadas = []
    cfg = _llm_config()

    monkeypatch.setattr(
        llm_mod._cache, "obter",
        lambda *args, **kwargs: '{"ok":false,"error_code":"TIMEOUT","detail":"velho"}',
    )
    monkeypatch.setattr(
        llm_mod._cache, "invalidar",
        lambda *args, **kwargs: invalidacoes.append(args) or True,
    )
    monkeypatch.setattr(llm_mod._cache, "definir", lambda *args, **kwargs: True)

    def responder(req, timeout=None):
        chamadas.append(req.full_url)
        return _Response("resposta nova")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", responder)
    assert llm_mod._chamar_llm("s", "u", cfg) == "resposta nova"
    assert invalidacoes
    assert len(chamadas) == 1


def test_resposta_acima_do_orcamento_nao_e_publicada_no_cache(monkeypatch):
    definicoes = []
    cfg = _llm_config()
    cfg["_runtime_agent_budget"] = {
        "max_llm_calls": 2,
        "llm_calls": 0,
        "max_generated_tokens": 1,
        "generated_tokens": 0,
    }
    monkeypatch.setattr(llm_mod._cache, "obter", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        llm_mod._cache, "definir",
        lambda *args, **kwargs: definicoes.append(args) or True,
    )
    monkeypatch.setattr(
        llm_mod.urllib.request, "urlopen",
        lambda req, timeout=None: _Response("resposta grande demais para o limite"),
    )
    with pytest.raises(llm_mod.ErroLLM, match="tokens gerados"):
        llm_mod._chamar_llm("s", "u", cfg)
    assert definicoes == []


def test_fingerprint_exige_tres_repeticoes_do_ciclo_ab():
    estado = AgentState(config={"agent": {}})
    sucesso_a = {
        "status": "success", "ok": True, "executed": True,
        "changed": False, "error_code": None, "detail": {"valor": "A"},
    }
    sucesso_b = dict(sucesso_a, detail={"valor": "B"})
    for tool, resultado in (
        ("tool_a", sucesso_a), ("tool_b", sucesso_b),
        ("tool_a", sucesso_a), ("tool_b", sucesso_b),
        ("tool_a", sucesso_a),
    ):
        assert estado.registrar_fingerprint_ciclo(tool, resultado)["detectado"] is False
    ciclo = estado.registrar_fingerprint_ciclo("tool_b", sucesso_b)
    assert ciclo["detectado"] is True
    assert ciclo["periodo"] == 2
    assert ciclo["repeticoes"] == 3


class _Cursor:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount


class _ConflictConnection:
    def __init__(self):
        self.rollbacks = 0
        self.closed = False
        self.row = {
            "id": 1,
            "payload": json.dumps({"tipo": "pergunta", "texto": "x"}),
            "tentativas": 0,
        }

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("SELECT * FROM JOBS"):
            return self
        if normalized.startswith("UPDATE JOBS"):
            return _Cursor(rowcount=0)
        return _Cursor(rowcount=0)

    def fetchone(self):
        return self.row

    def commit(self):
        return None

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_reserva_da_fila_tem_teto_mesmo_com_conflito_permanente(monkeypatch):
    conexao = _ConflictConnection()
    monkeypatch.setattr(queue_mod, "_conectar", lambda: conexao)
    assert queue_mod._reservar_proximo(max_invalid_jobs=1) is None
    assert conexao.rollbacks == 4
    assert conexao.closed is True


def test_analista_para_quando_repete_as_mesmas_lacunas(monkeypatch, tmp_path):
    chamadas_busca = []

    def buscar(pergunta, **kwargs):
        chamadas_busca.append(pergunta)
        return {
            "trechos": [], "arquivos_relevantes": [],
            "historico_relacionado": [],
        }

    monkeypatch.setattr(engine_mod, "buscar", buscar)
    monkeypatch.setattr(
        engine_mod, "executar_analista",
        lambda prompt, config: json.dumps({
            "ler": [], "ignorar": [], "faltando": ["mesma coisa"],
            "riscos": [], "motivo": "ainda falta",
        }),
    )
    monkeypatch.setattr(engine_mod, "CONTEXT_DIR", str(tmp_path))

    _, decisoes = engine_mod.ciclo_analista(
        "pergunta", {"engine": {"max_iteracoes_analista": 3}}, {}, [], {},
    )
    assert len(chamadas_busca) == 2
    assert decisoes[-1]["_early_exit"] == "repeated_missing"


def test_backoff_executor_e_exponencial_e_limitado(monkeypatch):
    esperas = []
    monkeypatch.setattr(engine_mod.time, "sleep", esperas.append)
    cfg = {"engine": {
        "executor_retry_base_delay_seconds": 0.5,
        "executor_retry_max_delay_seconds": 1.0,
        "executor_retry_jitter_seconds": 0.0,
    }}
    assert engine_mod._esperar_retry_executor(cfg, 1) == 0.5
    assert engine_mod._esperar_retry_executor(cfg, 2) == 1.0
    assert engine_mod._esperar_retry_executor(cfg, 3) == 1.0
    assert esperas == [0.5, 1.0, 1.0]


def test_config_rejeita_backoff_executor_invertido():
    with pytest.raises(ConfigError, match="executor_retry_max_delay_seconds"):
        validar_config({"engine": {
            "executor_retry_base_delay_seconds": 2,
            "executor_retry_max_delay_seconds": 1,
        }})
