#!/usr/bin/env python3
"""Semaforo entre processos baseado em SQLite, com lease recuperavel."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid

from eyle.runtime.process import pid_ativo

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "context", "llm_limiter.sqlite3")
_SCHEMA_LOCK = threading.Lock()
_READY = set()


def _connect():
    path = os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=3.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    with _SCHEMA_LOCK:
        if path not in _READY:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS limiter_slots (
                    limiter_key TEXT NOT NULL,
                    slot INTEGER NOT NULL,
                    owner TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (limiter_key, slot)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_limiter_expiry ON limiter_slots(expires_at)"
            )
            _READY.add(path)
    return conn


def _key(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _owner_pid(owner):
    try:
        return int(str(owner).split("-", 1)[0])
    except (TypeError, ValueError):
        return None


def _cleanup_stale(conn, now):
    conn.execute("DELETE FROM limiter_slots WHERE expires_at <= ?", (now,))
    rows = conn.execute("SELECT limiter_key, slot, owner FROM limiter_slots").fetchall()
    estado_por_pid = {}
    for row in rows:
        pid = _owner_pid(row["owner"])
        if pid not in estado_por_pid:
            estado_por_pid[pid] = pid_ativo(pid)
        if not estado_por_pid[pid]:
            conn.execute(
                "DELETE FROM limiter_slots WHERE limiter_key=? AND slot=? AND owner=?",
                (row["limiter_key"], row["slot"], row["owner"]),
            )


def acquire(value, limit=1, timeout=60.0, lease_seconds=600.0, poll_seconds=0.05):
    """Adquire um slot ou retorna None ao exceder o timeout."""
    limit = max(1, int(limit or 1))
    timeout = max(0.0, float(timeout or 0.0))
    lease_seconds = max(1.0, float(lease_seconds or 1.0))
    limiter_key = _key(value)
    owner = f"{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}"
    deadline = time.monotonic() + timeout
    while True:
        now = time.time()
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _cleanup_stale(conn, now)
            occupied = {
                int(row["slot"]) for row in conn.execute(
                    "SELECT slot FROM limiter_slots WHERE limiter_key = ?",
                    (limiter_key,),
                ).fetchall()
            }
            slot = next((candidate for candidate in range(limit) if candidate not in occupied), None)
            if slot is not None:
                conn.execute(
                    """
                    INSERT INTO limiter_slots
                        (limiter_key, slot, owner, acquired_at, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (limiter_key, slot, owner, now, now + lease_seconds),
                )
                conn.commit()
                return {"key": limiter_key, "slot": slot, "owner": owner}
            conn.commit()
        except sqlite3.OperationalError:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
        finally:
            conn.close()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(max(0.01, float(poll_seconds)), remaining))


def release(token):
    if not isinstance(token, dict):
        return False
    try:
        conn = _connect()
        try:
            cursor = conn.execute(
                """
                DELETE FROM limiter_slots
                WHERE limiter_key = ? AND slot = ? AND owner = ?
                """,
                (token.get("key"), int(token.get("slot")), token.get("owner")),
            )
            return cursor.rowcount > 0
        finally:
            conn.close()
    except Exception:
        return False


def active(value=None):
    conn = _connect()
    try:
        _cleanup_stale(conn, time.time())
        if value is None:
            return int(conn.execute("SELECT COUNT(*) FROM limiter_slots").fetchone()[0])
        return int(conn.execute(
            "SELECT COUNT(*) FROM limiter_slots WHERE limiter_key = ?", (_key(value),)
        ).fetchone()[0])
    finally:
        conn.close()
