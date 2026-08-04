#!/usr/bin/env python3
"""Telemetria operacional persistente da Eyle.

Registra duracao e resultado de chamadas LLM, ferramentas e jobs sem misturar
esses dados com a memoria conversacional. Falha de telemetria nunca derruba a
tarefa principal.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "context", "telemetry.sqlite3")
_SCHEMA_LOCK = threading.Lock()
_READY = set()


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or _utc_now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _connect():
    path = os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=3.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 3000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    with _SCHEMA_LOCK:
        if path not in _READY:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    task_id TEXT,
                    job_id INTEGER,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runtime_metrics_created "
                "ON runtime_metrics(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runtime_metrics_kind_name "
                "ON runtime_metrics(kind, name, created_at)"
            )
            _READY.add(path)
    return conn


@contextmanager
def _connection():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def record(kind, name, status, duration_ms=0.0, *, task_id=None, job_id=None,
           metadata=None, max_entries=10000):
    """Persiste uma metrica. Retorna False se a telemetria falhar."""
    try:
        payload = metadata if isinstance(metadata, dict) else {}
        with _connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime_metrics
                    (kind, name, status, duration_ms, task_id, job_id, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(kind)[:80], str(name)[:160], str(status)[:80],
                    max(0.0, float(duration_ms or 0.0)),
                    None if task_id is None else str(task_id)[:160],
                    None if job_id is None else int(job_id),
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
                    _iso(),
                ),
            )
            limite = max(100, int(max_entries or 10000))
            conn.execute(
                """
                DELETE FROM runtime_metrics
                WHERE id IN (
                    SELECT id FROM runtime_metrics
                    ORDER BY id DESC LIMIT -1 OFFSET ?
                )
                """,
                (limite,),
            )
        return True
    except Exception:
        return False


def _percentile(sorted_values, q):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return round(float(sorted_values[0]), 3)
    pos = (len(sorted_values) - 1) * float(q)
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return round(sorted_values[low] * (1 - frac) + sorted_values[high] * frac, 3)


def summary(window_seconds=3600, limit=5000):
    """Resume P50/P95/P99 e erros por kind/name na janela solicitada."""
    try:
        since = _iso(_utc_now() - timedelta(seconds=max(1, int(window_seconds))))
        with _connection() as conn:
            rows = conn.execute(
                """
                SELECT kind, name, status, duration_ms, created_at
                FROM runtime_metrics
                WHERE created_at >= ?
                ORDER BY id DESC LIMIT ?
                """,
                (since, max(1, min(int(limit), 50000))),
            ).fetchall()
    except Exception:
        return {"window_seconds": int(window_seconds), "total": 0, "groups": {}, "error": "telemetry_unavailable"}

    groups = {}
    for row in rows:
        key = f"{row['kind']}:{row['name']}"
        group = groups.setdefault(key, {"count": 0, "errors": 0, "durations": [], "last_status": None})
        group["count"] += 1
        group["errors"] += 0 if row["status"] in ("ok", "success", "cache_hit") else 1
        group["durations"].append(float(row["duration_ms"] or 0.0))
        if group["last_status"] is None:
            group["last_status"] = row["status"]

    public = {}
    for key, group in groups.items():
        values = sorted(group.pop("durations"))
        public[key] = {
            **group,
            "p50_ms": _percentile(values, 0.50),
            "p95_ms": _percentile(values, 0.95),
            "p99_ms": _percentile(values, 0.99),
            "max_ms": round(max(values), 3) if values else None,
        }
    return {"window_seconds": int(window_seconds), "total": len(rows), "groups": public}


def clear():
    try:
        with _connection() as conn:
            conn.execute("DELETE FROM runtime_metrics")
        return True
    except Exception:
        return False
