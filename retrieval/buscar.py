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
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from engine.persistencia import salvar_json_atomico  # noqa: E402
from engine.config_schema import carregar_config_validada  # noqa: E402

TOKEN_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


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
    """Implementacao simples e direta do BM25 (Okapi), sem dependencias externas."""

    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.tokenizados = [tokenizar(c["texto"] + " " + str(c.get("simbolo") or "") + " " + c["arquivo"])
                             for c in chunks]
        self.doc_len = [len(t) for t in self.tokenizados]
        self.avgdl = sum(self.doc_len) / len(self.doc_len) if self.doc_len else 0
        self.df = defaultdict(int)
        for tokens in self.tokenizados:
            for termo in set(tokens):
                self.df[termo] += 1
        self.n_docs = len(chunks)
        self.contagens = [Counter(t) for t in self.tokenizados]

    def _idf(self, termo):
        df = self.df.get(termo, 0)
        return math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))

    def pontuar(self, query_tokens):
        pontos = [0.0] * self.n_docs
        for termo in query_tokens:
            if termo not in self.df:
                continue
            idf = self._idf(termo)
            for i in range(self.n_docs):
                f = self.contagens[i].get(termo, 0)
                if f == 0:
                    continue
                dl = self.doc_len[i]
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                pontos[i] += idf * (f * (self.k1 + 1)) / (denom or 1)
        return pontos


def carregar_historico_relacionado(memory_dir, arquivos_relevantes, limite=3):
    caminho = os.path.join(memory_dir, "historico.json")
    if not os.path.exists(caminho):
        return []
    with open(caminho, "r", encoding="utf-8") as f:
        hist = json.load(f)
    relacionados = []
    for decisao in reversed(hist.get("decisoes", [])):
        arqs = set(decisao.get("arquivos_relevantes", []))
        if arqs & set(arquivos_relevantes):
            relacionados.append(decisao)
        if len(relacionados) >= limite:
            break
    return relacionados


def buscar(pergunta, memory_dir="memory", config=None, out_path=None):
    config = config or {}
    ctx_cfg = config.get("context", {})
    ret_cfg = config.get("retrieval", {})
    token_budget = ctx_cfg.get("token_budget", 1500)
    chars_per_token = ctx_cfg.get("chars_per_token", 4)
    max_chunks = ret_cfg.get("max_chunks_no_resultado", 8)
    k1 = ret_cfg.get("bm25_k1", 1.5)
    b = ret_cfg.get("bm25_b", 0.75)

    chunks = carregar_chunks(memory_dir)
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

    bm25 = BM25(chunks, k1=k1, b=b)
    query_tokens = tokenizar(pergunta)
    pontos = bm25.pontuar(query_tokens)

    ranking = sorted(range(len(chunks)), key=lambda i: pontos[i], reverse=True)

    selecionados = []
    tokens_usados = 0
    arquivos_relevantes = []
    for i in ranking:
        if pontos[i] <= 0:
            break
        c = chunks[i]
        custo = c.get("tokens") or (len(c["texto"]) // chars_per_token)
        if tokens_usados + custo > token_budget:
            continue  # tenta o proximo (pode ser menor e ainda caber)
        selecionados.append({**c, "score": round(pontos[i], 3)})
        tokens_usados += custo
        if c["arquivo"] not in arquivos_relevantes:
            arquivos_relevantes.append(c["arquivo"])
        if len(selecionados) >= max_chunks:
            break

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
