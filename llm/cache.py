#!/usr/bin/env python3
"""Cache SQLite por hash do prompt completo.

A versao anterior reabria e regravava ``cache_llm.json`` inteiro. Esta versao
faz lookup/update O(log n) em SQLite, suporta concorrencia entre Flask/Worker e
migra automaticamente o JSON legado uma unica vez.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

NOME_ARQUIVO = "cache_llm.sqlite3"
NOME_LEGADO = "cache_llm.json"
_SCHEMA_LOCK = threading.Lock()
_READY = set()
_MIGRATED = set()


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


def _prune(conn, max_entries, max_age_days):
    now = time.time()
    max_entries = max(0, int(max_entries or 0))
    max_age_days = max(0, int(max_age_days or 0))
    if max_age_days:
        conn.execute(
            "DELETE FROM cache_entries WHERE last_used < ?",
            (now - max_age_days * 86400,),
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
):
    """Devolve uma resposta exata sem carregar o cache inteiro em memoria."""
    del hit_flush_interval  # SQLite torna o update pequeno; mantido por compatibilidade.
    key = _chave(backend_fingerprint, prompt_sistema, prompt_usuario)
    conn = _connect(base_dir)
    try:
        _prune(conn, max_entradas, max_age_days)
        row = conn.execute(
            "SELECT response FROM cache_entries WHERE cache_key = ?", (key,),
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
        return response
    finally:
        conn.close()


def definir(
    base_dir, backend_fingerprint, prompt_sistema, prompt_usuario, resposta,
    max_entradas=500, max_age_days=30,
):
    if not _resposta_cacheavel(resposta):
        invalidar(base_dir, backend_fingerprint, prompt_sistema, prompt_usuario)
        return False
    key = _chave(backend_fingerprint, prompt_sistema, prompt_usuario)
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
        _prune(conn, max_entradas, max_age_days)
        return True
    finally:
        conn.close()


def invalidar(base_dir, backend_fingerprint, prompt_sistema, prompt_usuario):
    key = _chave(backend_fingerprint, prompt_sistema, prompt_usuario)
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
        return {"entries": int(row["total"]), "hits": int(row["hits"]), "backend": "sqlite"}
    finally:
        conn.close()
