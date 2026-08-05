#!/usr/bin/env python3
"""Criterios executaveis das Atualizacoes 46 e 47."""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod  # noqa: E402
import engine.agent_tools as tools_mod  # noqa: E402
from engine.benchmark import CASOS, calcular_metricas  # noqa: E402
from engine.codar import aplicar_patch  # noqa: E402
from engine.project_reader import ler_faixa_projeto  # noqa: E402


def _config():
    return {
        "llm": {"context_window_tokens": 4080, "max_tokens": 700},
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "max_recent_observations": 4,
        },
        "codar": {"fazer_backup": False, "testes": {"ativado": False}},
        "agent": {
            "enabled": True,
            "enabled_modes": ["analyze", "suggest", "edit"],
            "max_steps": 8,
            "max_no_progress_decisions": 3,
            "max_tentativas_parse": 1,
            "max_erros_consecutivos": 3,
            "max_chars_por_observacao": 500,
            "max_fatos_importantes": 10,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
            "exigir_run_tests_apos_escrita": True,
        },
    }


def _hashes(conteudo):
    valor = hashlib.sha256(conteudo.encode("utf-8")).hexdigest()
    return valor, valor


def _sequencia_edicao(file_hash, range_hash, codigo_novo="valor = 2"):
    base = {
        "caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 1,
        "codigo_novo": codigo_novo,
        "file_hash_esperado": file_hash,
        "range_hash_esperado": range_hash,
    }
    return [
        json.dumps({
            "tool": "read_range",
            "arguments": {"caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 1},
        }),
        json.dumps({"tool": "test_patch_dry_run", "arguments": base}),
        json.dumps({
            "tool": "apply_patch",
            "arguments": {**base, "codigo_original_esperado": "valor = 1"},
        }),
    ]


def test_patch_stale_e_rejeitado_por_hash_do_arquivo_antes_da_escrita(tmp_path):
    arquivo = tmp_path / "a.py"
    arquivo.write_text("valor = 1\n", encoding="utf-8")
    file_hash, range_hash = _hashes("valor = 1\n")
    arquivo.write_text("# mudou fora da faixa proposta\nvalor = 1\n", encoding="utf-8")

    resultado = aplicar_patch(
        str(tmp_path), "a.py", 1, 1, "valor = 1", "valor = 2",
        file_hash_esperado=file_hash, range_hash_esperado=range_hash,
    )

    assert resultado["ok"] is False
    assert resultado["changed"] is False
    assert resultado["error_code"] == "STALE_PATCH"
    assert arquivo.read_text(encoding="utf-8").startswith("# mudou")


def test_ciclo_edit_confirmado_testa_rele_e_conclui(tmp_path, monkeypatch):
    arquivo = tmp_path / "a.py"
    arquivo.write_text("valor = 1\n", encoding="utf-8")
    leitura = ler_faixa_projeto(tmp_path, "a.py", 1, 1)
    respostas = iter(_sequencia_edicao(leitura["file_hash"], leitura["content_hash"]))
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))

    status, _, pendente, _ = agent_mod.executar_agente(
        "altere a.py para valor 2", _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="edit",
    )
    assert status == "needs_user"
    assert pendente["tool_pendente"]["tool"] == "apply_patch"
    assert "hash arquivo=" in pendente["pergunta_ao_usuario"]
    assert arquivo.read_text(encoding="utf-8") == "valor = 1\n"

    hash_final = hashlib.sha256(b"valor = 2\n").hexdigest()
    respostas_retomada = iter([
        json.dumps({"tool": "run_tests", "arguments": {}}),
        json.dumps({
            "tool": "read_range",
            "arguments": {"caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 1},
        }),
        json.dumps({
            "final": {
                "resposta": "Em a.py:1, a alteração define `valor` como `2`; a releitura confirmou o novo conteúdo.",
                "evidence_ids": ["ev-0002"],
                "verificacao": "suite executada e faixa relida",
                "limitacoes": [],
            },
        }),
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas_retomada))
    monkeypatch.setattr(tools_mod, "rodar_testes_projeto", lambda *a, **k: {
        "executado": True, "ok": True, "detalhe": "python -m unittest passou",
    })

    status, texto, _, detalhes = agent_mod.executar_agente(
        "altere a.py para valor 2", _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retomar=json.loads(json.dumps(pendente)),
        retornar_detalhes=True, modo="edit",
    )

    assert status == "success"
    assert "`valor` como `2`" in texto
    assert arquivo.read_text(encoding="utf-8") == "valor = 2\n"
    assert detalhes["edit_state"]["status"] == "tests_passed"
    assert detalhes["edit_state"]["post_write_evidence_id"] == "ev-0002"
    assert detalhes["evidencias_usadas"][0]["file_hash"] == hash_final


def test_falha_de_teste_reverte_conteudo_original(tmp_path, monkeypatch):
    arquivo = tmp_path / "a.py"
    arquivo.write_text("valor = 1\n", encoding="utf-8")
    leitura = ler_faixa_projeto(tmp_path, "a.py", 1, 1)
    respostas = iter(_sequencia_edicao(leitura["file_hash"], leitura["content_hash"]))
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))
    _, _, pendente, _ = agent_mod.executar_agente(
        "altere a.py", _config(), projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="edit",
    )
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: '{"tool":"run_tests","arguments":{}}')
    monkeypatch.setattr(tools_mod, "rodar_testes_projeto", lambda *a, **k: {
        "executado": True, "ok": False, "detalhe": "1 teste falhou",
    })

    status, texto, _, detalhes = agent_mod.executar_agente(
        "altere a.py", _config(), projeto={"caminho_origem": str(tmp_path)},
        retomar=json.loads(json.dumps(pendente)), retornar_detalhes=True, modo="edit",
    )

    assert status == "needs_user"
    assert "revertida" in texto
    assert arquivo.read_text(encoding="utf-8") == "valor = 1\n"
    assert detalhes["edit_state"]["status"] == "reverted"


def test_suite_indisponivel_nao_vira_testes_passaram(tmp_path, monkeypatch):
    arquivo = tmp_path / "a.py"
    arquivo.write_text("valor = 1\n", encoding="utf-8")
    leitura = ler_faixa_projeto(tmp_path, "a.py", 1, 1)
    respostas = iter(_sequencia_edicao(leitura["file_hash"], leitura["content_hash"]))
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))
    _, _, pendente, _ = agent_mod.executar_agente(
        "altere a.py", _config(), projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="edit",
    )
    respostas_retomada = iter([
        '{"tool":"run_tests","arguments":{}}',
        '{"tool":"read_range","arguments":{"caminho_relativo":"a.py","linha_inicio":1,"linha_fim":1}}',
        '{"final":{"resposta":"feito","evidence_ids":["ev-0002"],"verificacao":"sem suite","limitacoes":["sem testes"]}}',
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas_retomada))
    monkeypatch.setattr(tools_mod, "rodar_testes_projeto", lambda *a, **k: {
        "executado": False, "ok": True, "detalhe": "sem suite configurada",
    })

    status, texto, _, detalhes = agent_mod.executar_agente(
        "altere a.py", _config(), projeto={"caminho_origem": str(tmp_path)},
        retomar=pendente, retornar_detalhes=True, modo="edit",
    )
    assert status == "failed"
    assert "executed=false" in texto
    assert "passaram" not in texto.lower()
    assert detalhes["edit_state"]["status"] == "applied_without_suite"


def test_benchmark_declara_10_casos_e_gate_5_checks_de_escrita():
    assert len(CASOS) == 10
    resultados = []
    for caso in CASOS:
        resultados.append({
            "leu": bool(caso["leitura"]), "factual_ok": True,
            "grounded_ok": True, "tools": [] if not caso["leitura"] else ["read_range"],
            "json_failures": 0, "latency_ms": 1, "inventadas": [],
            "false_success": False, "unauthorized_write": False,
            "write": {
                "confirmacao_barrou_escrita": True, "hashes_na_pendencia": True,
                "dry_run_antes_write": True, "rollback": True,
                "retomada_releitura": True,
            },
        })
    metricas = calcular_metricas(resultados)
    assert metricas["tarefas_com_uso_correto_de_leitura"] == 10
    assert metricas["checks_escrita_aprovados"] == 5
    assert metricas["gate_aprovado"] is True


def test_read_file_enriquecido_satisfaz_grounding(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("valor = 1\n", encoding="utf-8")
    respostas = iter([
        '{"tool":"read_file","arguments":{"caminho_relativo":"a.py"}}',
        '{"final":{"resposta":"Em a.py:1, o trecho define `valor` com o valor `1`.","evidence_ids":["ev-0001"],"verificacao":"lido","limitacoes":[]}}',
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))

    status, texto, _, detalhes = agent_mod.executar_agente(
        "analise a.py", _config(), projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="analyze",
    )

    assert status == "success"
    assert texto == "Em a.py:1, o trecho define `valor` com o valor `1`."
    assert detalhes["read_status"] == "read"
    assert detalhes["tools_called"] == ["read_file"]
    assert detalhes["evidencias_usadas"][0]["arquivo"] == "a.py"


def test_argumentos_obvios_de_find_symbol_sao_inferidos_do_objetivo(tmp_path, monkeypatch):
    (tmp_path / "symbols.py").write_text("def existente():\n    return 1\n", encoding="utf-8")
    respostas = iter([
        '{"tool":"find_symbol","arguments":{}}',
        '{"needs_user":"A funcao fantasma nao foi encontrada."}',
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))

    status, texto, _, detalhes = agent_mod.executar_agente(
        "Verifique se a funcao fantasma existe em symbols.py",
        _config(), projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="analyze",
    )

    assert status == "success"
    assert "fantasma" in texto
    assert "nao encontrado" in texto
    assert detalhes["tools_called"] == ["find_symbol"]


def test_snapshot_benchmark_ignora_trace_e_cache(tmp_path):
    from engine.benchmark import _snapshot

    (tmp_path / "calc.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "trace.jsonl").write_text("{}\n", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "calc.cpython-313.pyc").write_bytes(b"nao importa")

    assert _snapshot(tmp_path) == {"calc.py": "x = 1\n"}


def test_benchmark_nao_confirma_pergunta_livre_como_write(tmp_path, monkeypatch):
    from engine import benchmark as benchmark_mod

    chamadas = []

    def fake_agente(*args, **kwargs):
        chamadas.append(kwargs.get("retomar"))
        with open(agent_mod._TRACE_PATH, "a", encoding="utf-8") as trace:
            trace.write('{"tipo":"needs_user","step":1}\n')
        return (
            "needs_user", "qual mudanca?",
            {
                "tool_pendente": {
                    "tool": "__user_response__",
                    "arguments": {"resposta": ""},
                },
                "estado": {},
            },
            {"edit_state": {}},
        )

    monkeypatch.setattr(benchmark_mod, "ingerir", lambda *a, **k: None)
    monkeypatch.setattr(agent_mod, "executar_agente", fake_agente)

    resultado = benchmark_mod._rodar_caso(
        {"id": "06_edicao_confirmada", "modo": "edit", "leitura": True},
        _config(), str(tmp_path / "caso"),
    )

    assert chamadas == [None]
    assert resultado["unauthorized_write"] is False
    assert resultado["write"]["confirmacao_barrou_escrita"] is False
