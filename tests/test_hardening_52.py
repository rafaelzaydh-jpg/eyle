#!/usr/bin/env python3
"""Revisao 52: watchdog, grounding, telemetria e cache SQLite."""
from contextlib import closing
import json
import os
import sqlite3
import sys
import time
import urllib.error
import socket

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import process_limiter, queue, telemetry, worker
from engine.config_schema import avisos_config, validar_config
from engine.grounding import verify_conclusion
import engine.engine as engine_mod
import llm.cache as cache_mod
import llm.executar as llm_mod


class _Response:
    def read(self):
        return json.dumps({"message": {"content": "ok"}}).encode("utf-8")

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
        "retry_max_attempts": 3,
        "retry_base_delay_seconds": 0,
        "retry_max_delay_seconds": 0,
        "retry_jitter_seconds": 0,
        "max_concurrent_requests": 1,
        "cache": {"ativado": False},
    }
    llm.update(updates)
    return {"llm": llm}


def _child_sleep(event, connection):
    del event, connection
    time.sleep(5)


def _child_ok(event, connection):
    connection.send(("ok", {"echo": event["value"]}))
    connection.close()


def test_grounding_bloqueia_identificador_objetivo_inventado():
    evidence = [{
        "id": "ev-1", "arquivo": "audio.py", "linha_inicio": 1, "linha_fim": 8,
        "conteudo": "def limitar_volume(valor):\n    return max(0, min(100, valor))\n",
    }]
    result = verify_conclusion(
        "A funcao `limitar_volume` chama `os.remove` (audio.py:1-8).",
        evidence,
        {"enabled": True, "block_unsupported_anchors": True},
    )
    assert result["ok"] is False
    anchors = result["errors"][0]["unsupported_anchors"]
    assert any(item["value"] == "os.remove" for item in anchors)


def test_grounding_aceita_ancoras_presentes_e_parafrase():
    evidence = [{
        "id": "ev-1", "arquivo": "audio.py", "linha_inicio": 1, "linha_fim": 8,
        "conteudo": "def limitar_volume(valor):\n    return max(0, min(100, valor))\n",
    }]
    result = verify_conclusion(
        "`limitar_volume` restringe o valor entre `0` e `100` (audio.py:1-8).",
        evidence,
        {"enabled": True, "block_unsupported_anchors": True},
    )
    assert result["ok"] is True


def test_worker_watchdog_encerra_processo_travado():
    start = time.monotonic()
    with pytest.raises(worker.JobDeadlineExceeded):
        worker.executar_evento_isolado(
            {"value": 1}, 0.5, target=_child_sleep,
            heartbeat_interval=0.05, mp_context="spawn",
        )
    assert time.monotonic() - start < 3


def test_worker_isolado_devolve_resultado():
    result = worker.executar_evento_isolado(
        {"value": 7}, 2, target=_child_ok, mp_context="spawn",
    )
    assert result == {"echo": 7}


def test_connection_reset_recebe_retry(monkeypatch):
    calls = []

    def responder(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise ConnectionResetError("reset")
        return _Response()

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", responder)
    assert llm_mod._chamar_llm("s", "u", _llm_config()) == "ok"
    assert len(calls) == 2


def test_socket_timeout_recebe_retry(monkeypatch):
    calls = []

    def responder(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise socket.timeout("slow backend")
        return _Response()

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", responder)
    assert llm_mod._chamar_llm("s", "u", _llm_config()) == "ok"
    assert len(calls) == 2


def test_429_respeita_retry_after_sem_repetir_4xx_permanente(monkeypatch):
    calls = []

    def responder(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 429, "busy", {"Retry-After": "0"}, None,
            )
        return _Response()

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", responder)
    assert llm_mod._chamar_llm("s", "u", _llm_config()) == "ok"
    assert len(calls) == 2


def test_cache_sqlite_lookup_e_migracao(tmp_path):
    assert cache_mod.definir(tmp_path, "backend", "s", "u", "resposta") is True
    db = tmp_path / "context" / cache_mod.NOME_ARQUIVO
    assert db.read_bytes().startswith(b"SQLite format 3")
    assert cache_mod.obter(tmp_path, "backend", "s", "u") == "resposta"
    stats = cache_mod.estatisticas(tmp_path)
    assert stats["backend"] == "sqlite"
    assert stats["entries"] == 1
    assert stats["hits"] == 1


def test_queue_detecta_head_of_line_blocking(monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "fila.sqlite3"))
    queue._schemas_prontos.clear()
    first = queue.adicionar({"tipo": "pergunta", "texto": "travado"})
    queue.adicionar({"tipo": "pergunta", "texto": "esperando"})
    assert queue.proximo(timeout=0, worker_id="dead-worker")["_job_id"] == first
    queue.registrar_heartbeat("dead-worker", "processing", job_id=first, pid=99999999)
    with closing(sqlite3.connect(queue.DB_PATH)) as conn:
        conn.execute(
            "UPDATE jobs SET iniciado_em='2020-01-01T00:00:00Z' WHERE id=?", (first,)
        )
        conn.execute(
            "UPDATE worker_heartbeat SET atualizado_em='2020-01-01T00:00:00Z' "
            "WHERE worker_id='dead-worker'"
        )
        conn.commit()
    stats = queue.estatisticas(stale_after_seconds=1, blocked_after_seconds=1)
    assert stats["head_of_line_blocked"] is True
    assert stats["live_workers"] == 0


def test_telemetry_persiste_percentis(monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "DB_PATH", str(tmp_path / "telemetry.sqlite3"))
    telemetry._READY.clear()
    for duration in (10, 20, 30, 100):
        assert telemetry.record("llm", "read", "ok", duration)
    result = telemetry.summary(3600)
    group = result["groups"]["llm:read"]
    assert group["count"] == 4
    assert group["p50_ms"] == 25.0
    assert group["p95_ms"] > 80
    assert group["p99_ms"] >= group["p95_ms"]


def test_limiter_entre_processos_recusa_segundo_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(process_limiter, "DB_PATH", str(tmp_path / "limiter.sqlite3"))
    process_limiter._READY.clear()
    first = process_limiter.acquire("backend", limit=1, timeout=0.1, lease_seconds=10)
    assert first is not None
    assert process_limiter.acquire("backend", limit=1, timeout=0.05, lease_seconds=10) is None
    assert process_limiter.release(first) is True
    second = process_limiter.acquire("backend", limit=1, timeout=0.1, lease_seconds=10)
    assert second is not None
    process_limiter.release(second)



def test_limiter_remove_owner_morto(monkeypatch, tmp_path):
    monkeypatch.setattr(process_limiter, "DB_PATH", str(tmp_path / "limiter.sqlite3"))
    process_limiter._READY.clear()
    conn = process_limiter._connect()
    try:
        key = process_limiter._key("backend")
        conn.execute(
            "INSERT INTO limiter_slots "
            "(limiter_key, slot, owner, acquired_at, expires_at) VALUES (?, 0, ?, ?, ?)",
            (key, "99999999-dead-owner", time.time(), time.time() + 300),
        )
    finally:
        conn.close()
    token = process_limiter.acquire("backend", limit=1, timeout=0.2, lease_seconds=10)
    assert token is not None
    assert process_limiter.release(token) is True

def test_config_separa_avisos_de_erros():
    config = {
        "llm": {"read_timeout_seconds": 400, "max_concurrent_requests": 1},
        "worker": {"max_parallel_jobs": 2},
        "codar": {"testes": {"ativado": False}},
        "web": {"api_token": None},
    }
    validated = validar_config(config)
    codes = {item["code"] for item in validated["_config_warnings"]}
    assert "LLM_READ_TIMEOUT_HIGH" in codes
    assert "WORKER_PARALLELISM_CAPPED" in codes
    assert avisos_config(config)
