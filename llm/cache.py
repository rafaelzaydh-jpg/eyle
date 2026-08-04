#!/usr/bin/env python3
"""Cache LLM em duas camadas por hash exato do prompt/backend.

Um LRU thread-safe atende repeticoes na execucao atual sem I/O. O SQLite
persiste respostas entre sessoes, suporta concorrencia Flask/Worker e migra
automaticamente o JSON legado uma unica vez.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

NOME_ARQUIVO = "cache_llm.sqlite3"
NOME_LEGADO = "cache_llm.json"
_SCHEMA_LOCK = threading.Lock()
_READY = set()
_MIGRATED = set()
_MEMORY_LOCK = threading.RLock()
_MEMORY_CACHE = OrderedDict()


def _ttl_seconds(max_age_hours=None, max_age_days=30):
    """Resolve o TTL preservando compatibilidade com max_age_days legado."""
    if max_age_hours is not None:
        try:
            horas = max(0.0, float(max_age_hours))
        except (TypeError, ValueError):
            horas = 24.0
        return horas * 3600.0
    try:
        dias = max(0.0, float(max_age_days or 0))
    except (TypeError, ValueError):
        dias = 30.0
    return dias * 86400.0


def _chave_memoria(base_dir, key):
    return f"{os.path.abspath(_caminho(base_dir))}\x1f{key}"


def _limite_memoria(memoria_max_entradas, max_entradas):
    memoria = max(0, int(memoria_max_entradas or 0))
    disco = max(0, int(max_entradas or 0))
    if disco == 0:
        return 0
    return min(memoria, disco)


def _memoria_obter(memory_key, max_entradas, ttl_seconds):
    max_entradas = max(0, int(max_entradas or 0))
    if max_entradas == 0:
        return None
    agora = time.time()
    with _MEMORY_LOCK:
        entrada = _MEMORY_CACHE.get(memory_key)
        if entrada is None:
            return None
        resposta, criado_em, hits = entrada
        if ttl_seconds and agora - criado_em >= ttl_seconds:
            _MEMORY_CACHE.pop(memory_key, None)
            return None
        if not _resposta_cacheavel(resposta):
            _MEMORY_CACHE.pop(memory_key, None)
            return None
        _MEMORY_CACHE[memory_key] = (resposta, criado_em, hits + 1)
        _MEMORY_CACHE.move_to_end(memory_key)
        return resposta


def _memoria_definir(memory_key, resposta, criado_em=None, max_entradas=2048, hits=0):
    max_entradas = max(0, int(max_entradas or 0))
    if max_entradas == 0 or not _resposta_cacheavel(resposta):
        return False
    with _MEMORY_LOCK:
        _MEMORY_CACHE[memory_key] = (
            resposta, float(criado_em or time.time()), max(0, int(hits or 0)),
        )
        _MEMORY_CACHE.move_to_end(memory_key)
        while len(_MEMORY_CACHE) > max_entradas:
            _MEMORY_CACHE.popitem(last=False)
    return True


def limpar_memoria():
    """Limpa apenas o LRU do processo atual; util em testes e manutencao."""
    with _MEMORY_LOCK:
        _MEMORY_CACHE.clear()



def _caminho(base_dir):
    return os.path.join(os.fspath(base_dir), "context", NOME_ARQUIVO)


def _caminho_legado(base_dir):
    return os.path.join(os.fspath(base_dir), "context", NOME_LEGADO)


def _chave(backend_fingerprint, prompt_sistema, prompt_usuario):
    bruto = "\x1f".join([
        backend_fingerprint, prompt_sistema, prompt_usuario,
    ]).encode("utf-8")
    return hashlib.sha256(bruto).hexdigest()


def _epoch_from_text(value, default=None):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
        try:
            return time.mktime(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, OverflowError):
            pass
    return time.time() if default is None else float(default)


def _resposta_cacheavel(resposta):
    if not isinstance(resposta, str) or not resposta.strip():
        return False
    texto = resposta.lstrip()
    if texto.lower().startswith("[erro]"):
        return False

    # Erros operacionais antigos tambem apareceram como envelopes JSON e
    # acabavam sobrevivendo entre sessoes. A deteccao e conservadora: so
    # rejeita formatos inequivocos de falha do runtime, nao qualquer resposta
    # do modelo que por acaso mencione a palavra "error".
    if texto.startswith("{"):
        try:
            dados = json.loads(texto)
        except (TypeError, ValueError, json.JSONDecodeError):
            dados = None
        if isinstance(dados, dict):
            status = str(dados.get("status") or "").strip().lower()
            erro_explicito = any(
                chave in dados for chave in ("error_code", "exception", "traceback")
            )
            if dados.get("ok") is False:
                return False
            if status in {"failed", "failure", "error", "timeout"}:
                return False
            if erro_explicito and any(chave in dados for chave in ("error", "erro", "detail")):
                return False
    return True


def resposta_cacheavel(resposta):
    """API publica pequena para validar caches externos/mocks tambem."""
    return _resposta_cacheavel(resposta)


def _connect(base_dir):
    path = os.path.abspath(_caminho(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    with _SCHEMA_LOCK:
        if path not in _READY:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    response TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_used REAL NOT NULL,
                    hits INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_last_used "
                "ON cache_entries(last_used)"
            )
            _READY.add(path)
    _migrate_legacy(base_dir, conn, path)
    return conn


def _migrate_legacy(base_dir, conn, db_path):
    with _SCHEMA_LOCK:
        if db_path in _MIGRATED:
            return
        legacy = _caminho_legado(base_dir)
        if os.path.exists(legacy):
            try:
                with open(legacy, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                entries = data.get("entradas", {}) if isinstance(data, dict) else {}
                if isinstance(entries, dict):
                    for key, entry in entries.items():
                        if not isinstance(entry, dict):
                            continue
                        response = entry.get("resposta")
                        if not _resposta_cacheavel(response):
                            continue
                        created = _epoch_from_text(entry.get("criado_em"))
                        last_used = _epoch_from_text(entry.get("ultimo_uso"), created)
                        hits = max(0, int(entry.get("hits", 0) or 0))
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO cache_entries
                                (cache_key, response, created_at, last_used, hits)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (str(key), response, created, last_used, hits),
                        )
                migrated = legacy + ".migrated"
                try:
                    os.replace(legacy, migrated)
                except OSError:
                    pass
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        _MIGRATED.add(db_path)


def _prune(conn, max_entries, max_age_days=30, max_age_hours=None):
    now = time.time()
    max_entries = max(0, int(max_entries or 0))
    ttl = _ttl_seconds(max_age_hours=max_age_hours, max_age_days=max_age_days)
    if ttl:
        # TTL absoluto: um hit nao torna uma resposta antiga eterna.
        conn.execute(
            "DELETE FROM cache_entries WHERE created_at < ?",
            (now - ttl,),
        )
    if max_entries == 0:
        conn.execute("DELETE FROM cache_entries")
    else:
        conn.execute(
            """
            DELETE FROM cache_entries WHERE cache_key IN (
                SELECT cache_key FROM cache_entries
                ORDER BY last_used DESC LIMIT -1 OFFSET ?
            )
            """,
            (max_entries,),
        )


def obter(
    base_dir, backend_fingerprint, prompt_sistema, prompt_usuario,
    max_entradas=500, max_age_days=30, hit_flush_interval=20,
    memoria_max_entradas=2048, max_age_hours=None,
):
    """Consulta primeiro o LRU em memoria e depois o SQLite persistente."""
    del hit_flush_interval  # mantido por compatibilidade de configuracao.
    key = _chave(backend_fingerprint, prompt_sistema, prompt_usuario)
    memory_key = _chave_memoria(base_dir, key)
    limite_memoria = _limite_memoria(memoria_max_entradas, max_entradas)
    ttl = _ttl_seconds(max_age_hours=max_age_hours, max_age_days=max_age_days)
    resposta_memoria = _memoria_obter(memory_key, limite_memoria, ttl)
    if resposta_memoria is not None:
        return resposta_memoria

    conn = _connect(base_dir)
    try:
        _prune(conn, max_entradas, max_age_days, max_age_hours)
        row = conn.execute(
            "SELECT response, created_at FROM cache_entries WHERE cache_key = ?", (key,),
        ).fetchone()
        if row is None:
            return None
        response = row["response"]
        if not _resposta_cacheavel(response):
            conn.execute("DELETE FROM cache_entries WHERE cache_key = ?", (key,))
            return None
        conn.execute(
            "UPDATE cache_entries SET hits = hits + 1, last_used = ? WHERE cache_key = ?",
            (time.time(), key),
        )
        _memoria_definir(
            memory_key, response, criado_em=row["created_at"],
            max_entradas=limite_memoria,
        )
        return response
    finally:
        conn.close()


def definir(
    base_dir, backend_fingerprint, prompt_sistema, prompt_usuario, resposta,
    max_entradas=500, max_age_days=30, memoria_max_entradas=2048,
    max_age_hours=None,
):
    if not _resposta_cacheavel(resposta):
        invalidar(base_dir, backend_fingerprint, prompt_sistema, prompt_usuario)
        return False
    key = _chave(backend_fingerprint, prompt_sistema, prompt_usuario)
    memory_key = _chave_memoria(base_dir, key)
    limite_memoria = _limite_memoria(memoria_max_entradas, max_entradas)
    now = time.time()
    conn = _connect(base_dir)
    try:
        conn.execute(
            """
            INSERT INTO cache_entries (cache_key, response, created_at, last_used, hits)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(cache_key) DO UPDATE SET
                response = excluded.response,
                created_at = excluded.created_at,
                last_used = excluded.last_used,
                hits = 0
            """,
            (key, resposta, now, now),
        )
        _prune(conn, max_entradas, max_age_days, max_age_hours)
        _memoria_definir(
            memory_key, resposta, criado_em=now, max_entradas=limite_memoria,
        )
        return True
    finally:
        conn.close()


def invalidar(base_dir, backend_fingerprint, prompt_sistema, prompt_usuario):
    key = _chave(backend_fingerprint, prompt_sistema, prompt_usuario)
    memory_key = _chave_memoria(base_dir, key)
    with _MEMORY_LOCK:
        _MEMORY_CACHE.pop(memory_key, None)
    conn = _connect(base_dir)
    try:
        cursor = conn.execute("DELETE FROM cache_entries WHERE cache_key = ?", (key,))
        return cursor.rowcount > 0
    finally:
        conn.close()


def estatisticas(base_dir):
    conn = _connect(base_dir)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(hits), 0) AS hits FROM cache_entries"
        ).fetchone()
        prefixo = os.path.abspath(_caminho(base_dir)) + "\x1f"
        with _MEMORY_LOCK:
            entradas_memoria = [
                entrada for chave, entrada in _MEMORY_CACHE.items()
                if chave.startswith(prefixo)
            ]
        memoria = len(entradas_memoria)
        hits_memoria = sum(entrada[2] for entrada in entradas_memoria)
        return {
            "entries": int(row["total"]),
            "hits": int(row["hits"]) + hits_memoria,
            "memory_entries": memoria, "memory_hits": hits_memoria,
            "backend": "sqlite", "layers": ["memory_lru", "sqlite"],
        }
    finally:
        conn.close()
