#!/usr/bin/env python3
"""Revision 55 -- inverted BM25, query LRU and deterministic parallel ingest."""
import json
import math
import os
import threading
import time
from collections import Counter

import pytest

import ingest as ingest_mod
from engine.config_schema import ConfigError, validar_config
from retrieval import buscar as busca_mod


def _chunk(arquivo, texto, tokens=10, simbolo=None):
    return {
        "arquivo": arquivo,
        "simbolo": simbolo,
        "texto": texto,
        "linha_inicio": 1,
        "linha_fim": 1,
        "tokens": tokens,
    }


def _pontuar_denso(chunks, query_tokens, k1=1.5, b=0.75):
    tokenizados = [
        busca_mod.tokenizar(c["texto"] + " " + str(c.get("simbolo") or "") + " " + c["arquivo"])
        for c in chunks
    ]
    doc_len = [len(tokens) for tokens in tokenizados]
    avgdl = sum(doc_len) / len(doc_len)
    contagens = [Counter(tokens) for tokens in tokenizados]
    df = {}
    for tokens in tokenizados:
        for termo in set(tokens):
            df[termo] = df.get(termo, 0) + 1

    pontos = [0.0] * len(chunks)
    for termo in query_tokens:
        if termo not in df:
            continue
        idf = math.log(1 + (len(chunks) - df[termo] + 0.5) / (df[termo] + 0.5))
        for indice, contagem in enumerate(contagens):
            frequencia = contagem.get(termo, 0)
            if not frequencia:
                continue
            denom = frequencia + k1 * (1 - b + b * doc_len[indice] / avgdl)
            pontos[indice] += idf * (frequencia * (k1 + 1)) / denom
    return pontos


def _gravar_chunks(memory, chunks):
    memory.mkdir(exist_ok=True)
    (memory / "chunks.jsonl").write_text(
        "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks),
        encoding="utf-8",
    )


def test_bm25_invertido_equivale_ao_calculo_denso():
    chunks = [
        _chunk("a.py", "timeout timeout servidor", simbolo="conectar"),
        _chunk("b.py", "servidor local rapido", simbolo="iniciar"),
        _chunk("c.py", "cache persistente sqlite", simbolo="salvar"),
    ]
    consulta = busca_mod.tokenizar("timeout servidor timeout")
    bm25 = busca_mod.BM25(chunks)

    esparso = bm25.pontuar(consulta)
    denso = _pontuar_denso(chunks, consulta)

    assert set(bm25.postings["timeout"]) == {(0, 2)}
    assert 2 not in esparso
    for indice, esperado in enumerate(denso):
        assert esparso.get(indice, 0.0) == pytest.approx(esperado)


def test_query_cache_normaliza_ordem_e_recarrega_historico(tmp_path, monkeypatch):
    memory = tmp_path / "memory"
    _gravar_chunks(memory, [_chunk("a.py", "timeout servidor")])
    historico = {"decisoes": [{"id": 1, "arquivos_relevantes": ["a.py"]}]}
    (memory / "historico.json").write_text(json.dumps(historico), encoding="utf-8")
    busca_mod.invalidar_cache_bm25(memory)

    original = busca_mod.BM25.pontuar
    chamadas = []

    def contar(self, tokens):
        chamadas.append(tuple(tokens))
        return original(self, tokens)

    monkeypatch.setattr(busca_mod.BM25, "pontuar", contar)
    config = {"retrieval": {"query_cache_ativado": True, "query_cache_max_entradas": 8}}
    primeira = busca_mod.buscar("Timeout servidor!", memory_dir=memory, config=config)

    historico["decisoes"].append({"id": 2, "arquivos_relevantes": ["a.py"]})
    (memory / "historico.json").write_text(json.dumps(historico), encoding="utf-8")
    segunda = busca_mod.buscar("servidor timeout", memory_dir=memory, config=config)

    assert len(chamadas) == 1
    assert primeira["trechos"] == segunda["trechos"]
    assert segunda["historico_relacionado"][0]["id"] == 2


def test_query_cache_invalida_quando_indice_muda(tmp_path, monkeypatch):
    memory = tmp_path / "memory"
    _gravar_chunks(memory, [_chunk("antigo.py", "timeout servidor")])
    busca_mod.invalidar_cache_bm25(memory)

    original = busca_mod.BM25.pontuar
    chamadas = []

    def contar(self, tokens):
        chamadas.append(1)
        return original(self, tokens)

    monkeypatch.setattr(busca_mod.BM25, "pontuar", contar)
    config = {"retrieval": {"query_cache_ativado": True}}
    assert busca_mod.buscar("timeout", memory_dir=memory, config=config)["arquivos_relevantes"] == ["antigo.py"]

    time.sleep(0.002)
    _gravar_chunks(memory, [_chunk("novo.py", "timeout servidor com conteudo maior")])
    os.utime(memory / "chunks.jsonl", None)
    assert busca_mod.buscar("timeout", memory_dir=memory, config=config)["arquivos_relevantes"] == ["novo.py"]
    assert len(chamadas) == 2


def test_topk_heap_pula_chunk_grande_sem_perder_proximo():
    chunks = [
        _chunk("grande.py", "x", tokens=100),
        _chunk("primeiro.py", "x", tokens=4),
        _chunk("segundo.py", "x", tokens=4),
    ]
    selecionados, usados, arquivos = busca_mod._selecionar_trechos(
        chunks, {0: 10.0, 1: 5.0, 2: 5.0}, token_budget=8,
        chars_per_token=4, max_chunks=2,
    )

    assert [item["arquivo"] for item in selecionados] == ["primeiro.py", "segundo.py"]
    assert usados == 8
    assert arquivos == ["primeiro.py", "segundo.py"]


def _criar_projeto(projeto, quantidade=12):
    projeto.mkdir()
    for indice in range(quantidade):
        (projeto / f"mod_{indice:02d}.py").write_text(
            f"CONSTANTE = {indice}\n\ndef func_{indice}():\n    return CONSTANTE\n",
            encoding="utf-8",
        )


def test_ingest_paralelo_mantem_saida_deterministica(tmp_path):
    projeto = tmp_path / "projeto"
    _criar_projeto(projeto)
    serial = tmp_path / "serial"
    paralelo = tmp_path / "paralelo"
    base = {"entendimento": {"gerar_via_llm": False}}

    ingest_mod.ingerir(
        str(projeto), "Teste", str(serial),
        config={**base, "ingest": {"max_workers": 1, "parallel_threshold": 1}},
    )
    ingest_mod.ingerir(
        str(projeto), "Teste", str(paralelo),
        config={**base, "ingest": {"max_workers": 4, "parallel_threshold": 2}},
    )

    estrutura_serial = json.loads((serial / "estrutura.json").read_text(encoding="utf-8"))["arquivos"]
    estrutura_paralela = json.loads((paralelo / "estrutura.json").read_text(encoding="utf-8"))["arquivos"]
    projeto_serial = json.loads((serial / "projeto.json").read_text(encoding="utf-8"))
    projeto_paralelo = json.loads((paralelo / "projeto.json").read_text(encoding="utf-8"))

    assert estrutura_serial == estrutura_paralela
    assert (serial / "chunks.jsonl").read_text(encoding="utf-8") == (paralelo / "chunks.jsonl").read_text(encoding="utf-8")
    assert projeto_serial["index_fingerprint"] == projeto_paralelo["index_fingerprint"]


def test_ingest_processa_arquivos_em_multiplas_threads(tmp_path, monkeypatch):
    projeto = tmp_path / "projeto"
    _criar_projeto(projeto, quantidade=16)
    saida = tmp_path / "memory"
    original = ingest_mod._processar_arquivo_indexavel
    threads = set()
    lock = threading.Lock()

    def observar(*args, **kwargs):
        with lock:
            threads.add(threading.get_ident())
        time.sleep(0.01)
        return original(*args, **kwargs)

    monkeypatch.setattr(ingest_mod, "_processar_arquivo_indexavel", observar)
    ingest_mod.ingerir(
        str(projeto), "Teste", str(saida),
        config={
            "entendimento": {"gerar_via_llm": False},
            "ingest": {"max_workers": 4, "parallel_threshold": 2},
        },
    )

    assert len(threads) >= 2


def test_schema_valida_opcoes_da_revisao_55():
    validar_config({
        "retrieval": {"query_cache_ativado": True, "query_cache_max_entradas": 256},
        "ingest": {"max_workers": 4, "parallel_threshold": 8},
    })
    with pytest.raises(ConfigError):
        validar_config({"ingest": {"max_workers": 33}})
    with pytest.raises(ConfigError):
        validar_config({"retrieval": {"query_cache_ativado": "sim"}})
