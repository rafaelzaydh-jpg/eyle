#!/usr/bin/env python3
"""Atualizacao 24 -- indice Python por AST e preambulo preservado."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.codar import localizar_simbolo  # noqa: E402
from ingest import (  # noqa: E402
    dividir_em_chunks, extrair_definicoes_python, extrair_simbolos,
    indice_esta_atual, ingerir,
)


CODIGO_CLASSES = """import os

CONSTANTE = 7

class ClasseA:
    def run(self):
        return "a"

class ClasseB:
    async def run(self):
        return "b"
"""


def test_ast_qualifica_metodos_homonimos_e_detecta_async():
    linhas = CODIGO_CLASSES.split("\n")

    nomes = [nome for nome, _ in extrair_simbolos(linhas, ".py")]

    assert nomes == ["ClasseA", "ClasseA.run", "ClasseB", "ClasseB.run"]
    assert "run" not in nomes
    definicoes = extrair_definicoes_python(linhas)
    assert next(d for d in definicoes if d["nome"] == "ClasseB.run")["tipo"] == "funcao_assincrona"


def test_chunk_de_preambulo_preserva_imports_e_constantes():
    chunks = dividir_em_chunks(
        "classes.py", CODIGO_CLASSES.split("\n"), ".py",
        chunk_max_tokens=400, chars_per_token=4,
    )

    preambulo = chunks[0]
    assert preambulo["tipo_chunk"] == "preambulo"
    assert preambulo["simbolo"] is None
    assert preambulo["linha_inicio"] == 1
    assert "import os" in preambulo["texto"]
    assert "CONSTANTE = 7" in preambulo["texto"]
    assert [c["simbolo"] for c in chunks[1:]] == [
        "ClasseA", "ClasseA.run", "ClasseB", "ClasseB.run",
    ]


def test_decorator_fica_anexado_ao_chunk_do_simbolo():
    codigo = """import functools

@functools.lru_cache
def calcular():
    return 1
"""
    chunks = dividir_em_chunks(
        "decorado.py", codigo.split("\n"), ".py",
        chunk_max_tokens=400, chars_per_token=4,
    )

    assert chunks[0]["tipo_chunk"] == "preambulo"
    simbolo = next(c for c in chunks if c["simbolo"] == "calcular")
    assert simbolo["linha_inicio"] == 3
    assert simbolo["texto"].startswith("@functools.lru_cache")


def test_localizar_simbolo_nao_confunde_metodos_de_classes_diferentes(tmp_path):
    caminho = tmp_path / "classes.py"
    caminho.write_text(CODIGO_CLASSES, encoding="utf-8")

    classe_a = localizar_simbolo(str(tmp_path), "classes.py", "ClasseA.run")
    classe_b = localizar_simbolo(str(tmp_path), "classes.py", "ClasseB.run")

    assert classe_a is not None and 'return "a"' in classe_a["codigo_original"]
    assert 'return "b"' not in classe_a["codigo_original"]
    assert classe_b is not None and 'return "b"' in classe_b["codigo_original"]
    assert 'return "a"' not in classe_b["codigo_original"]
    assert localizar_simbolo(str(tmp_path), "classes.py", "run") is None


def test_ingest_respeita_gitignore_bloqueia_segredos_e_symlink_externo(tmp_path):
    projeto = tmp_path / "projeto"
    saida = tmp_path / "memoria"
    projeto.mkdir()
    (projeto / ".gitignore").write_text(
        "*.py\n!permitido.py\npasta_ignorada/\n",
        encoding="utf-8",
    )
    (projeto / "permitido.py").write_text("def ok():\n    return True\n", encoding="utf-8")
    (projeto / "ignorado.py").write_text("SEGREDO = 'nao indexar'\n", encoding="utf-8")
    (projeto / "dados.json").write_text('{"publico": true}', encoding="utf-8")
    subpasta = projeto / "sub"
    subpasta.mkdir()
    (subpasta / ".gitignore").write_text("local.json\n", encoding="utf-8")
    (subpasta / "local.json").write_text('{"ignorado_localmente": true}', encoding="utf-8")
    (subpasta / "publico.json").write_text('{"sub": true}', encoding="utf-8")
    (projeto / "credentials.json").write_text('{"password": "real"}', encoding="utf-8")
    (projeto / "chave.txt").write_text(
        "-----BEGIN PRIVATE KEY-----\nNAO_INDEXAR\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    pasta_ignorada = projeto / "pasta_ignorada"
    pasta_ignorada.mkdir()
    (pasta_ignorada / "dado.json").write_text('{"ignorado": true}', encoding="utf-8")

    fora = tmp_path / "fora.py"
    fora.write_text("DADO_EXTERNO = True\n", encoding="utf-8")
    atalho = projeto / "atalho.py"
    try:
        atalho.symlink_to(fora)
    except (OSError, NotImplementedError):
        atalho = None

    ingerir(
        str(projeto), "Seguro", str(saida),
        config={"entendimento": {"gerar_via_llm": False}},
    )

    import json
    estrutura = json.loads((saida / "estrutura.json").read_text(encoding="utf-8"))["arquivos"]
    projeto_json = json.loads((saida / "projeto.json").read_text(encoding="utf-8"))
    chunks = (saida / "chunks.jsonl").read_text(encoding="utf-8")

    assert set(estrutura) == {"permitido.py", "dados.json", os.path.join("sub", "publico.json")}
    assert "nao indexar" not in chunks
    assert "NAO_INDEXAR" not in chunks
    assert "DADO_EXTERNO" not in chunks
    assert projeto_json["arquivos_ignorados"]["gitignore"] >= 3
    assert projeto_json["arquivos_ignorados"]["segredo"] >= 2
    if atalho is not None:
        assert projeto_json["arquivos_ignorados"]["symlink_externo"] >= 1


def test_fingerprint_muda_com_conteudo_e_source_hash_tem_nome_honesto(tmp_path):
    projeto = tmp_path / "projeto"
    memoria = tmp_path / "memory"
    projeto.mkdir()
    arquivo = projeto / "app.py"
    arquivo.write_text("valor = 1\n", encoding="utf-8")
    config = {"entendimento": {"gerar_via_llm": False}}

    ingerir(str(projeto), "Teste", str(memoria), config=config)
    primeiro = __import__("json").loads(
        (memoria / "projeto.json").read_text(encoding="utf-8")
    )
    assert "source_hash" not in primeiro
    assert primeiro["source_path_hash"]
    assert len(primeiro["index_fingerprint"]) == 64
    assert indice_esta_atual(primeiro, config) is True

    arquivo.write_text("valor = 2\n", encoding="utf-8")
    assert indice_esta_atual(primeiro, config) is False

    ingerir(str(projeto), "Teste", str(memoria), config=config)
    segundo = __import__("json").loads(
        (memoria / "projeto.json").read_text(encoding="utf-8")
    )
    assert segundo["index_fingerprint"] != primeiro["index_fingerprint"]
    assert segundo["source_path_hash"] == primeiro["source_path_hash"]
