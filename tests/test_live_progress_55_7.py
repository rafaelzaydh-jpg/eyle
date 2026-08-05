#!/usr/bin/env python3
"""Regressoes da revisao 55.7: progresso, streaming e estado web."""
import contextlib
import json
from pathlib import Path

from engine import queue
from llm import executar as llm_mod


def _usar_fila_temporaria(monkeypatch, tmp_path):
    banco = tmp_path / "fila.sqlite3"
    monkeypatch.setattr(queue, "DB_PATH", str(banco))
    queue._evento_disponivel.clear()
    queue._schemas_prontos.discard(str(banco.resolve()))
    return banco


def test_fila_persiste_progresso_incremental(monkeypatch, tmp_path):
    _usar_fila_temporaria(monkeypatch, tmp_path)
    job_id = queue.adicionar({"tipo": "pergunta", "texto": "analise"})
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


def test_chamada_estruturada_desativa_streaming_mesmo_com_job_ativo(monkeypatch):
    def fake_openai(*args, **kwargs):
        assert kwargs["on_chunk"] is None
        return '{"tool":"list_tree","arguments":{}}'

    publicados = []
    monkeypatch.setattr(llm_mod, "_chamar_openai_com_fallback", fake_openai)
    monkeypatch.setattr(llm_mod, "_resolver_modelo_openai", lambda *a, **k: "modelo")
    monkeypatch.setattr(llm_mod.process_limiter, "acquire", lambda *a, **k: "slot")
    monkeypatch.setattr(llm_mod.process_limiter, "release", lambda *a, **k: True)
    monkeypatch.setattr(llm_mod, "_semaforo_backend", lambda *a, **k: type("S", (), {
        "acquire": lambda self, timeout=None: True,
        "release": lambda self: None,
    })())
    monkeypatch.setattr(
        llm_mod.job_progress,
        "publicar",
        lambda config, phase, message, **campos: publicados.append(
            {"phase": phase, "message": message, **campos}
        ) or True,
    )
    monkeypatch.setattr(llm_mod.job_progress, "job_id_de", lambda config: 4)

    config = {
        "llm": {
            "base_url": "http://127.0.0.1:8080",
            "model": "modelo",
            "openai_compatible": True,
            "temperature": 0.2,
            "retry_max_attempts": 1,
            "agent_retry_max_attempts": 1,
            "stream_responses": True,
            "cache": {"ativado": False},
        },
        "context_engine": {"chars_per_token_fallback": 3},
        "_runtime_agent_budget": {"source_job_id": 4},
    }

    resposta = llm_mod._chamar_llm_impl(
        "s", "u", config, forcar_json=True, perfil="agent", stream_visible=False,
    )

    assert json.loads(resposta)["tool"] == "list_tree"
    assert any(item["phase"] == "validating" for item in publicados)
    assert all(item.get("partial_text") in (None, "") for item in publicados)


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
    from engine.config_schema import ConfigError, validar_config

    assert validar_config({"llm": {"stream_responses": True}})["llm"]["stream_responses"] is True
    with pytest.raises(ConfigError, match="llm.stream_responses"):
        validar_config({"llm": {"stream_responses": "sim"}})
