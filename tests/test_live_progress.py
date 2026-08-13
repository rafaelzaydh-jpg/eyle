#!/usr/bin/env python3
"""Live progress regression coverage: progresso, streaming e estado web."""
import contextlib
import json
from pathlib import Path

from eyle.runtime import queue
from llm import executar as llm_mod


def _usar_fila_temporaria(monkeypatch, tmp_path):
    banco = tmp_path / "fila.sqlite3"
    monkeypatch.setattr(queue, "DB_PATH", str(banco))
    queue._evento_disponivel.clear()
    queue._schemas_prontos.discard(str(banco.resolve()))
    return banco


def test_fila_persiste_progresso_incremental(monkeypatch, tmp_path):
    _usar_fila_temporaria(monkeypatch, tmp_path)
    job_id = queue.adicionar({"type": "pergunta", "texto": "analise"})
    queue.proximo(timeout=0, worker_id="worker-test")

    assert queue.atualizar_progresso(
        job_id,
        phase="generating",
        message="LLM gerando tokens",
        estimated_tokens=12,
        tokens_per_second=8.4,
    ) is True

    salvo = queue.obter(job_id)
    assert salvo["progresso"]["phase"] == "generating"
    assert salvo["progresso"]["estimated_tokens"] == 12
    assert salvo["progresso"]["tokens_per_second"] == 8.4
    assert salvo["progresso_seq"] >= 2


def test_openai_stream_monta_texto_e_ignora_reasoning_publico(monkeypatch):
    linhas = [
        b'data: {"choices":[{"delta":{"reasoning_content":"segredo interno"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"Oi"}}]}\n',
        b'data: {"choices":[{"delta":{"content":" mundo"}}],"usage":{"completion_tokens":2}}\n',
        b'data: [DONE]\n',
    ]
    recebidos = []

    class Resposta:
        def __iter__(self):
            return iter(linhas)

    @contextlib.contextmanager
    def fake_abrir(req, connect_timeout, read_timeout=None):
        yield Resposta()

    monkeypatch.setattr(llm_mod, "_abrir_url", fake_abrir)
    resposta = llm_mod._chamar_openai_compatible(
        "http://127.0.0.1:8080", "modelo", "s", "u", 0.2,
        timeout=1, read_timeout=1,
        on_chunk=lambda delta, metadata, done: recebidos.append((delta, metadata, done)),
    )

    assert resposta == "Oi mundo"
    assert "segredo interno" not in resposta
    assert any(delta == "Oi" for delta, _, _ in recebidos)
    assert recebidos[-1][2] is True


def test_navegador_nao_reabre_job_terminal_cacheado():
    fonte = (Path(__file__).parents[1] / "web" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    assert '["pending", "processing"].includes(job.status)' in fonte
    assert 'sessionStorage.setItem(JOBS_STORAGE_KEY, JSON.stringify(ativos))' in fonte
    assert 'sessionStorage.removeItem(JOBS_STORAGE_KEY)' in fonte
    assert 'job._descartar = true' in fonte
    assert 'trackedJobs = trackedJobs.filter((job) => !job._descartar)' in fonte


def test_config_valida_stream_responses():
    import pytest
    from eyle.runtime.config import ConfigError, validar_config
    from tests.canonical import base_config
    from tests.canonical import standard_registry

    cfg = base_config()
    cfg["llm"]["stream_responses"] = True
    assert validar_config(cfg, standard_registry())["llm"]["stream_responses"] is True

    cfg = base_config()
    cfg["llm"]["stream_responses"] = "sim"
    with pytest.raises(ConfigError, match="llm.stream_responses"):
        validar_config(cfg, standard_registry())
