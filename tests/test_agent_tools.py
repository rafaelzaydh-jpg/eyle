#!/usr/bin/env python3
"""Testes do contrato de tools (Atualizacoes 21, 40 e 41)."""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent_tools as tools_mod  # noqa: E402


CAMPOS = {"status", "ok", "executed", "changed", "error_code", "detail"}
HASH = "a" * 64


def _ctx(caminho_projeto="/projeto"):
    return {
        "projeto": {"caminho_origem": caminho_projeto},
        "entendimento": {"arquivos": {"a.py": {"tipo": "modulo"}}},
        "config": {"codar": {"testes": {"ativado": False}}},
    }


def test_tools_de_leitura_usam_o_mesmo_envelope(monkeypatch):
    monkeypatch.setattr(tools_mod, "buscar", lambda *a, **k: {
        "trechos": [{"arquivo": "a.py", "linhas": "1-2", "score": 3.0}],
        "arquivos_relevantes": ["a.py"],
    })
    monkeypatch.setattr(tools_mod, "localizar_simbolo", lambda *a, **k: {
        "linha_inicio": 1, "linha_fim": 2, "codigo_original": "x = 1",
    })
    monkeypatch.setattr(tools_mod, "ler_codigo_real", lambda *a, **k: {
        "a.py": {"conteudo": "x = 1\n", "truncado": False},
    })

    chamadas = [
        ("read_metadata", {"caminho_relativo": "a.py"}),
        ("search_code", {"pergunta": "x"}),
        ("find_symbol", {"caminho_relativo": "a.py", "simbolo": "x"}),
        ("read_file", {"caminho_relativo": "a.py"}),
    ]
    for nome, argumentos in chamadas:
        resultado = tools_mod.executar_tool(nome, argumentos, _ctx())
        assert set(resultado) == CAMPOS
        assert resultado["status"] == "success"
        assert resultado["ok"] is True
        assert resultado["executed"] is True
        assert resultado["changed"] is False


def test_run_tests_pulado_mapeia_executed_false(monkeypatch):
    monkeypatch.setattr(tools_mod, "rodar_testes_projeto", lambda *a, **k: {
        "executado": False, "ok": True, "detalhe": "sem suite configurada",
    })

    resultado = tools_mod.executar_tool("run_tests", {}, _ctx())

    assert set(resultado) == CAMPOS
    assert resultado == {
        "status": "skipped", "ok": True, "executed": False,
        "changed": False, "error_code": None,
        "detail": "sem suite configurada",
    }


def test_apply_patch_falho_com_rollback_informa_changed_false(monkeypatch):
    monkeypatch.setattr(tools_mod, "aplicar_patch", lambda *a, **k: {
        "ok": False, "detalhe": "falhou; revertido automaticamente", "backup_path": None,
    })
    argumentos = {
        "caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 1,
        "codigo_original_esperado": "x = 1", "codigo_novo": "x = 2",
        "file_hash_esperado": HASH, "range_hash_esperado": HASH,
    }

    resultado = tools_mod.executar_tool("apply_patch", argumentos, _ctx())

    assert set(resultado) == CAMPOS
    assert resultado["status"] == "failed"
    assert resultado["ok"] is False
    assert resultado["executed"] is True
    assert resultado["changed"] is False
    assert resultado["error_code"] == "PATCH_FAILED"


def test_apply_patch_bem_sucedido_informa_changed_true(monkeypatch):
    monkeypatch.setattr(tools_mod, "aplicar_patch", lambda *a, **k: {
        "ok": True, "detalhe": "aplicado", "backup_path": "/tmp/a.bak",
    })
    argumentos = {
        "caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 1,
        "codigo_original_esperado": "x = 1", "codigo_novo": "x = 2",
        "file_hash_esperado": HASH, "range_hash_esperado": HASH,
    }

    resultado = tools_mod.executar_tool("apply_patch", argumentos, _ctx())

    assert resultado["status"] == "success"
    assert resultado["changed"] is True
    assert resultado["error_code"] is None


def test_tool_desconhecida_tambem_respeita_o_contrato():
    resultado = tools_mod.executar_tool("nao_existe", {}, {})

    assert set(resultado) == CAMPOS
    assert resultado["status"] == "failed"
    assert resultado["executed"] is False
    assert resultado["error_code"] == "TOOL_NOT_FOUND"


def test_run_tests_tem_permissao_exec_separada():
    assert tools_mod.TOOLS["run_tests"]["permission"] == "EXEC"
    assert tools_mod.TOOLS["read_file"]["permission"] == "READ"
    assert tools_mod.TOOLS["apply_patch"]["permission"] == "WRITE"


def test_todas_as_tools_declaram_catalogo_e_schema_completo():
    obrigatorios = {
        "name", "description", "permission", "input_schema",
        "output_schema", "compat_aliases", "limits", "fn",
    }
    for nome, entrada in tools_mod.TOOLS.items():
        assert obrigatorios <= set(entrada), nome
        assert entrada["name"] == nome
        assert entrada["permission"] in {"READ", "EXEC", "WRITE"}
        assert entrada["description"]
        assert entrada["output_schema"]
        assert entrada["input_schema"]["type"] == "object"
        assert entrada["input_schema"]["additionalProperties"] is False


def test_catalogo_publico_nasce_do_registro_sem_fn_ou_aliases():
    catalogo = tools_mod.gerar_catalogo_tools()
    assert [item["name"] for item in catalogo] == list(tools_mod.TOOLS)
    assert all(set(item) == {
        "name", "description", "permission", "input_schema", "output_schema", "limits",
    } for item in catalogo)
    assert "fn" not in repr(catalogo)
    assert "compat_aliases" not in repr(catalogo)


def test_catalogo_resolve_limites_da_config():
    catalogo = tools_mod.gerar_catalogo_tools(config={
        "agent": {
            "max_tree_entries": 17,
            "max_tree_depth": 3,
            "max_read_range_lines": 25,
        },
        "dicas": {"max_chars_por_arquivo": 900},
    })
    por_nome = {item["name"]: item for item in catalogo}
    assert por_nome["list_tree"]["limits"] == {
        "max_entradas": 17, "max_profundidade": 3,
    }
    assert por_nome["read_range"]["limits"] == {"max_linhas": 25}
    assert por_nome["read_file"]["limits"] == {"max_caracteres": 900}


def test_validacao_central_rejeita_ausente_tipo_errado_e_chave_desconhecida(monkeypatch):
    executou = []
    monkeypatch.setitem(
        tools_mod.TOOLS["read_range"], "fn",
        lambda *a, **k: executou.append(True) or tools_mod._sucesso({}),
    )
    casos = [
        {"caminho_relativo": "a.py", "linha_inicio": 1},
        {"caminho_relativo": "a.py", "linha_inicio": "1", "linha_fim": 2},
        {"caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 2, "surpresa": True},
    ]
    for argumentos in casos:
        resultado = tools_mod.executar_tool("read_range", argumentos, _ctx())
        assert resultado["error_code"] == "INVALID_ARGUMENT"
        assert resultado["executed"] is False
    assert executou == []


def test_alias_legado_e_normalizado_so_no_adaptador(monkeypatch):
    vistos = []

    def fake(arguments, ctx):
        vistos.append(arguments)
        return tools_mod._sucesso({"ok": True})

    monkeypatch.setitem(tools_mod.TOOLS["read_metadata"], "fn", fake)
    resultado = tools_mod.executar_tool("read_metadata", {"arquivo": "a.py"}, _ctx())
    assert resultado["ok"] is True
    assert vistos == [{"caminho_relativo": "a.py"}]

    conflito = tools_mod.executar_tool(
        "read_metadata",
        {"arquivo": "a.py", "caminho_relativo": "b.py"},
        _ctx(),
    )
    assert conflito["error_code"] == "INVALID_ARGUMENT"


def test_search_code_rele_trecho_fresco_numerado_e_com_hash(tmp_path, monkeypatch):
    arquivo = tmp_path / "audio.py"
    arquivo.write_text("volume = 1\nvolume += 1\n", encoding="utf-8")
    monkeypatch.setattr(tools_mod, "buscar", lambda *a, **k: {
        "trechos": [{
            "arquivo": "audio.py", "linhas": "1-2", "simbolo": None,
            "score": 4.2, "conteudo": "CONTEUDO VELHO DO INDICE",
        }],
        "arquivos_relevantes": ["audio.py"],
    })

    resultado = tools_mod.executar_tool(
        "search_code", {"pergunta": "volume"}, _ctx(str(tmp_path)),
    )

    item = resultado["detail"]["resultados"][0]
    assert "1 | volume = 1" in item["trecho_numerado"]
    assert "2 | volume += 1" in item["trecho_numerado"]
    assert "CONTEUDO VELHO" not in item["trecho_numerado"]
    assert item["content_hash"] == hashlib.sha256(
        b"volume = 1\nvolume += 1\n"
    ).hexdigest()
    assert item["file_hash"] == item["content_hash"]


def test_cada_schema_aceita_uma_chamada_canonica(monkeypatch):
    monkeypatch.setattr(tools_mod, "buscar", lambda *a, **k: {
        "trechos": [], "arquivos_relevantes": [],
    })
    monkeypatch.setattr(tools_mod, "localizar_simbolo", lambda *a, **k: {
        "linha_inicio": 1, "linha_fim": 1, "codigo_original": "x = 1",
    })
    monkeypatch.setattr(tools_mod, "ler_codigo_real", lambda *a, **k: {
        "a.py": {"conteudo": "x = 1", "truncado": False},
    })
    monkeypatch.setattr(tools_mod, "listar_arvore_projeto", lambda *a, **k: {
        "entradas": [], "ignorados_por_motivo": {}, "truncado": False,
    })
    monkeypatch.setattr(tools_mod, "ler_faixa_projeto", lambda *a, **k: {
        "arquivo": "a.py", "linha_inicio": 1, "linha_fim": 1,
        "trecho_numerado": "     1 | x = 1", "content_hash": "abc",
        "file_hash": HASH,
        "fim_ajustado_ao_arquivo": False,
    })
    monkeypatch.setattr(tools_mod, "testar_patch_em_copia", lambda *a, **k: {
        "ok": True, "detalhe": "ok", "conteudo_resultante": "x = 2",
    })
    monkeypatch.setattr(tools_mod, "rodar_testes_projeto", lambda *a, **k: {
        "executado": False, "ok": True, "detalhe": "sem suite",
    })
    monkeypatch.setattr(tools_mod, "aplicar_patch", lambda *a, **k: {
        "ok": True, "detalhe": "ok", "backup_path": None,
    })

    validos = {
        "read_metadata": {"caminho_relativo": "a.py"},
        "list_tree": {"limite": 10, "profundidade": 2, "filtro": "*.py"},
        "search_code": {"pergunta": "x"},
        "find_symbol": {"caminho_relativo": "a.py", "simbolo": "x"},
        "read_range": {"caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 1},
        "read_file": {"caminho_relativo": "a.py"},
        "test_patch_dry_run": {
            "caminho_relativo": "a.py", "linha_inicio": 1,
            "linha_fim": 1, "codigo_novo": "x = 2",
            "file_hash_esperado": HASH, "range_hash_esperado": HASH,
        },
        "run_tests": {},
        "apply_patch": {
            "caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 1,
            "codigo_original_esperado": "x = 1", "codigo_novo": "x = 2",
            "file_hash_esperado": HASH, "range_hash_esperado": HASH,
        },
    }
    assert set(validos) == set(tools_mod.TOOLS)
    for nome, argumentos in validos.items():
        resultado = tools_mod.executar_tool(nome, argumentos, _ctx())
        assert resultado["error_code"] != "INVALID_ARGUMENT", nome


def test_read_file_real_devolve_evidencia_verificavel(tmp_path):
    arquivo = tmp_path / "a.py"
    arquivo.write_text("x = 1\ny = 2\n", encoding="utf-8")
    ctx = {
        "projeto": {"caminho_origem": str(tmp_path)},
        "config": {
            "dicas": {"max_chars_por_arquivo": 20000},
            "agent": {"max_read_range_lines": 400},
        },
    }

    resultado = tools_mod.executar_tool(
        "read_file", {"caminho_relativo": "a.py"}, ctx,
    )

    assert resultado["ok"] is True
    detalhe = resultado["detail"]
    assert detalhe["conteudo"] == "x = 1\ny = 2\n"
    assert detalhe["arquivo"] == "a.py"
    assert detalhe["linha_inicio"] == 1
    assert detalhe["linha_fim"] == 2
    assert detalhe["content_hash"]
    assert detalhe["file_hash"]
    assert "1 | x = 1" in detalhe["trecho_numerado"]


def test_search_code_respeita_memory_dir_do_projeto(tmp_path, monkeypatch):
    vistos = []

    def fake_buscar(pergunta, memory_dir, config):
        vistos.append(memory_dir)
        return {"trechos": [], "arquivos_relevantes": []}

    monkeypatch.setattr(tools_mod, "buscar", fake_buscar)
    memoria = tmp_path / "memoria-isolada"
    resultado = tools_mod.executar_tool(
        "search_code", {"pergunta": "valor"},
        {
            "projeto": {
                "caminho_origem": str(tmp_path),
                "memory_dir": str(memoria),
            },
            "config": {},
        },
    )

    assert resultado["ok"] is True
    assert vistos == [str(memoria)]
