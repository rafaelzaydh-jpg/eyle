#!/usr/bin/env python3
"""Regressoes da correcao de PID seguro no Windows."""
from contextlib import closing
import os

from eyle.runtime import limiter, process, queue


def test_pid_atual_nao_chama_os_kill(monkeypatch):
    def proibido(*args, **kwargs):
        raise AssertionError("os.kill nao pode ser chamado para o proprio PID")

    monkeypatch.setattr(process.os, "kill", proibido)
    assert process.pid_ativo(os.getpid()) is True


def test_branch_windows_nunca_usa_os_kill(monkeypatch):
    chamadas = []

    monkeypatch.setattr(process, "_rodando_no_windows", lambda: True)
    monkeypatch.setattr(process, "_pid_ativo_windows", lambda pid: chamadas.append(pid) or True)
    monkeypatch.setattr(
        process.os, "kill",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("os.kill usado no Windows")),
    )

    pid_externo = os.getpid() + 100000
    assert process.pid_ativo(pid_externo) is True
    assert chamadas == [pid_externo]


def test_status_da_fila_com_pid_proprio_nao_sinaliza_processo(monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "fila.sqlite3"))
    queue._schemas_prontos.clear()
    queue.registrar_heartbeat("worker-local", "idle", pid=os.getpid())
    monkeypatch.setattr(
        process.os, "kill",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("status sinalizou o processo")),
    )

    stats = queue.estatisticas(stale_after_seconds=30)
    assert stats["live_workers"] == 1
    assert stats["workers"][0]["pid_alive"] is True


def test_limiter_com_owner_proprio_nao_sinaliza_processo(monkeypatch, tmp_path):
    monkeypatch.setattr(limiter, "DB_PATH", str(tmp_path / "limiter.sqlite3"))
    limiter._READY.clear()
    token = limiter.acquire("backend", limit=1, timeout=0.1, lease_seconds=10)
    assert token is not None
    monkeypatch.setattr(
        process.os, "kill",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("limiter sinalizou o processo")),
    )

    assert limiter.active("backend") == 1
    assert limiter.release(token) is True


def test_pid_fora_da_faixa_nao_derruba_probe_posix(monkeypatch):
    monkeypatch.setattr(process, "_rodando_no_windows", lambda: False)
    monkeypatch.setattr(
        process.os, "kill",
        lambda *args, **kwargs: (_ for _ in ()).throw(OverflowError("pid fora da faixa")),
    )
    assert process.pid_ativo(10**30) is False


def test_timestamp_sem_timezone_e_rejeitado_pelo_contrato_atual():
    assert queue._idade_segundos("2020-01-01T00:00:00") is None


def test_recovery_trata_heartbeat_invalido_como_interrompido(monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "fila.sqlite3"))
    queue._schemas_prontos.clear()
    job_id = queue.adicionar({"type": "pergunta", "texto": "travado"})
    assert queue.proximo(timeout=0, worker_id="worker-corrompido")["_job_id"] == job_id
    queue.registrar_heartbeat("worker-corrompido", "processing", job_id=job_id, pid=os.getpid())

    import sqlite3
    with closing(sqlite3.connect(queue.DB_PATH)) as conn:
        conn.execute(
            "UPDATE worker_heartbeat SET atualizado_em='nao-e-data' WHERE worker_id=?",
            ("worker-corrompido",),
        )
        conn.commit()

    assert queue.recuperar_interrompidos(stale_after_seconds=30) == 1
    assert queue.obter(job_id)["status"] == "pending"
