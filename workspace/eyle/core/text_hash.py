#!/usr/bin/env python3
"""Hashes canonicos de texto usados por leitura, dry-run e escrita.

Os hashes representam o conteudo logico do arquivo: CRLF e CR sao
normalizados para LF antes do calculo. Assim uma leitura feita no Windows e
uma verificacao feita no Linux/WSL nao produzem um falso ``STALE_PATCH`` so
por causa da traducao automatica de quebras de linha do Python.
"""
import hashlib


def normalizar_quebras(conteudo):
    return str(conteudo or "").replace("\r\n", "\n").replace("\r", "\n")


def hash_texto(conteudo):
    canonico = normalizar_quebras(conteudo)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def extrair_faixa(conteudo, linha_inicio, linha_fim):
    """Devolve a faixa 1-based inclusiva preservando LF canonico."""
    canonico = normalizar_quebras(conteudo)
    linhas = canonico.splitlines(keepends=True)
    if linha_inicio < 1 or linha_fim < linha_inicio or linha_fim > len(linhas):
        return None
    return "".join(linhas[linha_inicio - 1:linha_fim])


def hash_faixa(conteudo, linha_inicio, linha_fim):
    faixa = extrair_faixa(conteudo, linha_inicio, linha_fim)
    return hash_texto(faixa) if faixa is not None else None
