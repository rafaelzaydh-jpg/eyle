"""Shared pytest configuration for isolated Eyle runtime state."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_runtime_state(monkeypatch, tmp_path):
    """Never let tests write persistent Runtime state into the repository tree."""
    from eyle.core import tools
    from eyle.runtime import limiter, queue, service, telemetry

    state_root = tmp_path / "eyle-runtime-state"
    context_dir = state_root / "context"
    memory_dir = state_root / "memory"
    context_dir.mkdir(parents=True, exist_ok=True)
    memory_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(service, "MEMORY_DIR", str(memory_dir))
    monkeypatch.setattr(service, "CONTEXT_DIR", str(context_dir))
    monkeypatch.setattr(service, "AGENT_PENDENTE_PATH", str(context_dir / "agent_pendente.json"))
    monkeypatch.setattr(queue, "DB_PATH", str(context_dir / "fila.sqlite3"))
    monkeypatch.setattr(limiter, "DB_PATH", str(context_dir / "llm_limiter.sqlite3"))
    monkeypatch.setattr(telemetry, "DB_PATH", str(context_dir / "telemetry.sqlite3"))
    monkeypatch.setattr(tools, "MEMORY_DIR", str(memory_dir / "project"))
    limiter._READY.clear()
    telemetry._READY.clear()
