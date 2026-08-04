#!/usr/bin/env python3
"""Regressoes da revisao 55.5: ordem, schema e ciclo."""
from engine import worker
from engine.agent_state import AgentState
from llm import executar as llm_mod


def test_worker_limita_consumidores_pela_capacidade_llm():
    configurado, efetivo = worker._resolver_parallelismo({
        "worker": {"max_parallel_jobs": 4},
        "llm": {"max_concurrent_requests": 1},
    })
    assert configurado == 4
    assert efetivo == 1


def test_worker_preserva_paralelismo_quando_backend_suporta():
    assert worker._resolver_parallelismo({
        "worker": {"max_parallel_jobs": 2},
        "llm": {"max_concurrent_requests": 3},
    }) == (2, 2)


def test_schema_exige_um_unico_ramo_e_argumentos_da_tool():
    schema = llm_mod._SCHEMA_DECISAO_AGENTE
    assert "oneOf" in schema
    assert "anyOf" not in schema
    assert {"required": ["tool", "arguments"]} in schema["oneOf"]


def test_duas_repeticoes_iguais_nao_sao_ciclo():
    estado = AgentState(config={"agent": {}})
    resultado = {"status": "success", "ok": True, "detail": "igual"}
    assert estado.registrar_fingerprint_ciclo("read_file", resultado)["detectado"] is False
    assert estado.registrar_fingerprint_ciclo("read_file", resultado)["detectado"] is False
    assert estado.registrar_fingerprint_ciclo("read_file", resultado)["detectado"] is True


def test_config_rejeita_ciclo_com_uma_unica_repeticao():
    import pytest
    from engine.config_schema import ConfigError, validar_config

    with pytest.raises(ConfigError, match="cycle_min_repetitions"):
        validar_config({"agent": {"cycle_min_repetitions": 1}})


def test_navegador_associa_falha_a_mensagem_de_origem():
    from pathlib import Path

    fonte = (Path(__file__).parents[1] / "web" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "mensagem_id: data.mensagem_id" in fonte
    assert 'origemEl.insertAdjacentElement("afterend", wrap)' in fonte
    assert "Falha ao processar" in fonte


def test_navegador_descarta_job_terminal_ao_recarregar():
    from pathlib import Path

    fonte = (Path(__file__).parents[1] / "web" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    assert '["pending", "processing"].includes(job.status)' in fonte
    assert 'sessionStorage.setItem(JOBS_STORAGE_KEY, JSON.stringify(ativos))' in fonte
    assert 'sessionStorage.removeItem(JOBS_STORAGE_KEY)' in fonte
