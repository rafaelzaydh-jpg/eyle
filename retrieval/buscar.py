#!/usr/bin/env python3
"""
buscar.py
---------
Este e o componente mais importante do sistema.

Ele transforma:
    "Tenho 30 mil / 100 mil tokens guardados"
em:
    "Estes 1200 tokens sao importantes agora"

Usa BM25 (algoritmo classico de busca textual, o mesmo tipo usado por
motores de busca) implementado em Python puro -- sem precisar instalar
nada. Roda 100% offline.

Uso como script:
    python retrieval/buscar.py "aumentar limite de upload"

Uso como modulo (chamado pelo main.py):
    from retrieval.buscar import buscar
    ctx = buscar("aumentar limite de upload", memory_dir="memory", config=cfg)
"""
import argparse
import copy
import heapq
import json
import math
import os
import re
import sys
import threading
import time
from collections import Counter, OrderedDict, defaultdict

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from engine.persistencia import salvar_json_atomico  # noqa: E402
from engine.config_schema import carregar_config_validada  # noqa: E402

TOKEN_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_INDICES_BM25 = {}
_INDICES_LOCK = threading.Lock()
_MAX_INDICES_EM_MEMORIA = 4
_CACHE_BUSCAS = OrderedDict()
_CACHE_BUSCAS_LOCK = threading.Lock()
_DEFAULT_CACHE_BUSCAS_MAX = 256


def tokenizar(texto):
    return [t.lower() for t in TOKEN_RE.findall(texto)]


def carregar_chunks(memory_dir):
    caminho = os.path.join(memory_dir, "chunks.jsonl")
    chunks = []
    if not os.path.exists(caminho):
        return chunks
    with open(caminho, "r", encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if linha:
                chunks.append(json.loads(linha))
    return chunks


class BM25:
    """BM25 com indice invertido: pontua somente docs que contem cada termo."""

    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.n_docs = len(chunks)
        self.doc_len = []
        self.postings = defaultdict(list)

        for indice, chunk in enumerate(chunks):
            tokens = tokenizar(
                chunk["texto"] + " " + str(chunk.get("simbolo") or "") + " " + chunk["arquivo"]
            )
            self.doc_len.append(len(tokens))
            for termo, frequencia in Counter(tokens).items():
                self.postings[termo].append((indice, frequencia))

        self.avgdl = sum(self.doc_len) / self.n_docs if self.n_docs else 0
        self.df = {termo: len(lista) for termo, lista in self.postings.items()}

    def _idf(self, termo):
        df = self.df.get(termo, 0)
        return math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))

    def pontuar(self, query_tokens):
        """Retorna apenas scores positivos/candidatos em um mapa doc_id -> score."""
        pontos = defaultdict(float)
        for termo in query_tokens:
            postings = self.postings.get(termo)
            if not postings:
                continue
            idf = self._idf(termo)
            for indice, frequencia in postings:
                dl = self.doc_len[indice]
                denom = frequencia + self.k1 * (
                    1 - self.b + self.b * dl / (self.avgdl or 1)
                )
                pontos[indice] += idf * (frequencia * (self.k1 + 1)) / (denom or 1)
        return dict(pontos)


def _invalidar_cache_buscas_caminho(caminho=None):
    with _CACHE_BUSCAS_LOCK:
        if caminho is None:
            _CACHE_BUSCAS.clear()
            return
        remover = [chave for chave in _CACHE_BUSCAS if chave[0] == caminho]
        for chave in remover:
            _CACHE_BUSCAS.pop(chave, None)


def _cache_busca_obter(chave):
    with _CACHE_BUSCAS_LOCK:
        valor = _CACHE_BUSCAS.get(chave)
        if valor is None:
            return None
        _CACHE_BUSCAS.move_to_end(chave)
        return copy.deepcopy(valor)


def _cache_busca_salvar(chave, valor, max_entradas):
    limite = max(1, int(max_entradas or _DEFAULT_CACHE_BUSCAS_MAX))
    with _CACHE_BUSCAS_LOCK:
        _CACHE_BUSCAS[chave] = copy.deepcopy(valor)
        _CACHE_BUSCAS.move_to_end(chave)
        while len(_CACHE_BUSCAS) > limite:
            _CACHE_BUSCAS.popitem(last=False)


def _indice_bm25_cached(memory_dir, k1, b):
    """Reutiliza chunks/tokenizacao enquanto o arquivo de indice nao muda."""
    caminho = os.path.abspath(os.path.join(memory_dir, "chunks.jsonl"))
    try:
        stat = os.stat(caminho)
    except OSError:
        return [], None, None
    fingerprint = (
        stat.st_mtime_ns,
        getattr(stat, "st_ctime_ns", 0),
        stat.st_size,
        getattr(stat, "st_ino", 0),
        float(k1),
        float(b),
    )
    with _INDICES_LOCK:
        entrada = _INDICES_BM25.get(caminho)
        if entrada and entrada["fingerprint"] == fingerprint:
            entrada["ultimo_uso"] = time.monotonic()
            return entrada["chunks"], entrada["bm25"], fingerprint

    chunks = carregar_chunks(memory_dir)
    bm25 = BM25(chunks, k1=k1, b=b) if chunks else None
    with _INDICES_LOCK:
        _INDICES_BM25[caminho] = {
            "fingerprint": fingerprint,
            "chunks": chunks,
            "bm25": bm25,
            "ultimo_uso": time.monotonic(),
        }
        if len(_INDICES_BM25) > _MAX_INDICES_EM_MEMORIA:
            antigo = min(
                _INDICES_BM25,
                key=lambda item: _INDICES_BM25[item]["ultimo_uso"],
            )
            if antigo != caminho:
                _INDICES_BM25.pop(antigo, None)
                _invalidar_cache_buscas_caminho(antigo)

    # A nova versao do arquivo nunca deve reutilizar resultado da versao anterior.
    _invalidar_cache_buscas_caminho(caminho)
    return chunks, bm25, fingerprint


def invalidar_cache_bm25(memory_dir=None):
    """Utilitario de testes/ingest; o fingerprint ja invalida automaticamente."""
    if memory_dir is None:
        with _INDICES_LOCK:
            _INDICES_BM25.clear()
        _invalidar_cache_buscas_caminho()
        return

    caminho = os.path.abspath(os.path.join(memory_dir, "chunks.jsonl"))
    with _INDICES_LOCK:
        _INDICES_BM25.pop(caminho, None)
    _invalidar_cache_buscas_caminho(caminho)


def carregar_historico_relacionado(memory_dir, arquivos_relevantes, limite=3):
    caminho = os.path.join(memory_dir, "historico.json")
    if not os.path.exists(caminho):
        return []
    with open(caminho, "r", encoding="utf-8") as f:
        hist = json.load(f)
    relacionados = []
    arquivos_alvo = set(arquivos_relevantes)
    for decisao in reversed(hist.get("decisoes", [])):
        arqs = set(decisao.get("arquivos_relevantes", []))
        if arqs & arquivos_alvo:
            relacionados.append(decisao)
        if len(relacionados) >= limite:
            break
    return relacionados


def _selecionar_trechos(chunks, pontos, token_budget, chars_per_token, max_chunks):
    """Seleciona Top-K exato com heap, sem ordenar todos os candidatos."""
    heap = [(-score, indice) for indice, score in pontos.items() if score > 0]
    heapq.heapify(heap)

    selecionados = []
    tokens_usados = 0
    arquivos_relevantes = []
    while heap and len(selecionados) < max_chunks:
        score_negativo, indice = heapq.heappop(heap)
        score = -score_negativo
        chunk = chunks[indice]
        custo = chunk.get("tokens") or (len(chunk["texto"]) // chars_per_token)
        if tokens_usados + custo > token_budget:
            continue
        selecionados.append({**chunk, "score": round(score, 3)})
        tokens_usados += custo
        if chunk["arquivo"] not in arquivos_relevantes:
            arquivos_relevantes.append(chunk["arquivo"])

    return selecionados, tokens_usados, arquivos_relevantes


def buscar(pergunta, memory_dir="memory", config=None, out_path=None):
    config = config or {}
    ctx_cfg = config.get("context", {})
    ret_cfg = config.get("retrieval", {})
    token_budget = ctx_cfg.get("token_budget", 1500)
    chars_per_token = ctx_cfg.get("chars_per_token", 4)
    max_chunks = ret_cfg.get("max_chunks_no_resultado", 8)
    k1 = ret_cfg.get("bm25_k1", 1.5)
    b = ret_cfg.get("bm25_b", 0.75)
    cache_ativado = ret_cfg.get("query_cache_ativado", True)
    cache_max = ret_cfg.get("query_cache_max_entradas", _DEFAULT_CACHE_BUSCAS_MAX)

    chunks, bm25, fingerprint = _indice_bm25_cached(memory_dir, k1, b)
    if not chunks:
        print("[buscar] Nenhum chunk encontrado. Rode ingest.py primeiro.", file=sys.stderr)
        atual = {
            "version": "1.0",
            "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pergunta": pergunta,
            "tokens_usados": 0,
            "arquivos_relevantes": [],
            "trechos": [],
            "historico_relacionado": [],
        }
        return atual

    query_tokens = tokenizar(pergunta)
    caminho_indice = os.path.abspath(os.path.join(memory_dir, "chunks.jsonl"))
    chave_cache = (
        caminho_indice,
        fingerprint,
        tuple(sorted(query_tokens)),
        int(token_budget),
        int(chars_per_token),
        int(max_chunks),
    )
    dados_selecao = _cache_busca_obter(chave_cache) if cache_ativado else None

    if dados_selecao is None:
        pontos = bm25.pontuar(query_tokens)
        selecionados, tokens_usados, arquivos_relevantes = _selecionar_trechos(
            chunks, pontos, token_budget, chars_per_token, max_chunks,
        )
        dados_selecao = {
            "selecionados": selecionados,
            "tokens_usados": tokens_usados,
            "arquivos_relevantes": arquivos_relevantes,
        }
        if cache_ativado:
            _cache_busca_salvar(chave_cache, dados_selecao, cache_max)
    else:
        selecionados = dados_selecao["selecionados"]
        tokens_usados = dados_selecao["tokens_usados"]
        arquivos_relevantes = dados_selecao["arquivos_relevantes"]

    historico_relacionado = carregar_historico_relacionado(memory_dir, arquivos_relevantes)

    atual = {
        "version": "1.0",
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pergunta": pergunta,
        "token_budget": token_budget,
        "tokens_usados": tokens_usados,
        "arquivos_relevantes": arquivos_relevantes,
        "trechos": [
            {
                "arquivo": s["arquivo"],
                "simbolo": s.get("simbolo"),
                "linhas": f"{s['linha_inicio']}-{s['linha_fim']}",
                "score": s["score"],
                "conteudo": s["texto"],
            }
            for s in selecionados
        ],
        "historico_relacionado": historico_relacionado,
    }

    if out_path:
        salvar_json_atomico(out_path, atual)

    return atual


def main():
    parser = argparse.ArgumentParser(description="Busca os trechos mais relevantes da memoria para uma pergunta")
    parser.add_argument("pergunta", help="Pergunta ou tarefa")
    parser.add_argument("--memory-dir", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory"))
    parser.add_argument("--config", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"))
    parser.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "context", "atual.json"))
    args = parser.parse_args()

    config = carregar_config_validada(args.config)

    atual = buscar(args.pergunta, memory_dir=args.memory_dir, config=config, out_path=args.out)

    print(f"[buscar] Pergunta: {args.pergunta}")
    print(f"[buscar] Tokens usados: {atual['tokens_usados']} / orcamento {atual.get('token_budget')}")
    print(f"[buscar] Arquivos relevantes: {atual['arquivos_relevantes']}")
    print(f"[buscar] Contexto salvo em: {args.out}")


if __name__ == "__main__":
    main()
