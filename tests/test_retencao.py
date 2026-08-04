#!/usr/bin/env python3
"""Atualizacao 36: historico, cache, trace e backups tem teto."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import retencao  # noqa: E402
from verify.validar import registrar_historico  # noqa: E402


def test_historico_conserva_so_as_entradas_mais_recentes(tmp_path):
    memoria = tmp_path / "memory"
    memoria.mkdir()
    resultado = {"citation_validity": None, "coverage": None, "grounding": None, "avisos": []}
    for indice in range(5):
        registrar_historico(
            str(memoria), f"pergunta {indice}", [], resultado, max_entradas=3,
        )
    decisoes = json.loads((memoria / "historico.json").read_text(encoding="utf-8"))["decisoes"]
    assert [item["pergunta"] for item in decisoes] == ["pergunta 2", "pergunta 3", "pergunta 4"]


def test_cache_remove_expiradas_e_aplica_lru():
    entradas = {
        "velha": {"ultimo_uso": "2020-01-01T00:00:00"},
        "a": {"ultimo_uso": "2026-01-01T00:00:00"},
        "b": {"ultimo_uso": "2026-01-02T00:00:00"},
    }
    agora = time.mktime(time.strptime("2026-01-03", "%Y-%m-%d"))
    retencao.podar_cache(entradas, max_entradas=1, max_age_days=30, agora=agora)
    assert set(entradas) == {"b"}


def test_trace_rotaciona_e_backups_respeitam_quantidade(tmp_path):
    trace = tmp_path / "agent_trace.jsonl"
    trace.write_text("primeiro\n", encoding="utf-8")
    retencao.rotacionar_arquivo(trace, max_files=2)
    trace.write_text("segundo\n", encoding="utf-8")
    retencao.rotacionar_arquivo(trace, max_files=2)
    assert (tmp_path / "agent_trace.jsonl.1").read_text(encoding="utf-8") == "segundo\n"
    assert (tmp_path / "agent_trace.jsonl.2").read_text(encoding="utf-8") == "primeiro\n"

    backups = tmp_path / "backups"
    backups.mkdir()
    for indice in range(4):
        caminho = backups / f"{indice}.bak"
        caminho.write_text(str(indice), encoding="utf-8")
        os.utime(caminho, (100 + indice, 100 + indice))
    mantidos = retencao.limpar_backups(
        backups, max_files=2, max_age_days=0, max_total_mb=1, agora=1000,
    )
    assert {os.path.basename(item) for item in mantidos} == {"2.bak", "3.bak"}
    assert {item.name for item in backups.iterdir()} == {"2.bak", "3.bak"}
