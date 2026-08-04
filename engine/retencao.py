#!/usr/bin/env python3
"""Politicas de retencao para artefatos que crescem ao longo do uso."""
import os
import time
from datetime import datetime


def limitar_lista(itens, max_entradas):
    max_entradas = max(0, int(max_entradas))
    if max_entradas == 0:
        return []
    return list(itens)[-max_entradas:]


def _timestamp_iso(valor):
    if not isinstance(valor, str) or not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def podar_cache(entradas, max_entradas=500, max_age_days=30, agora=None):
    """Remove expiradas primeiro e depois aplica LRU por ``ultimo_uso``."""
    agora = time.time() if agora is None else float(agora)
    max_age_days = max(0, int(max_age_days))
    if max_age_days:
        limite = agora - max_age_days * 86400
        for chave, entrada in list(entradas.items()):
            usado = _timestamp_iso((entrada or {}).get("ultimo_uso"))
            if usado is not None and usado < limite:
                del entradas[chave]

    max_entradas = max(0, int(max_entradas))
    if len(entradas) > max_entradas:
        ordenadas = sorted(
            entradas.items(), key=lambda item: (item[1] or {}).get("ultimo_uso", ""),
        )
        for chave, _ in ordenadas[:len(entradas) - max_entradas]:
            del entradas[chave]
    return entradas


def rotacionar_arquivo(caminho, max_files=5):
    """Move ``arquivo`` para ``arquivo.1`` e conserva no maximo N copias."""
    max_files = max(0, int(max_files))
    if not os.path.isfile(caminho) or os.path.getsize(caminho) == 0:
        return
    if max_files == 0:
        os.unlink(caminho)
        return
    ultimo = f"{caminho}.{max_files}"
    try:
        os.unlink(ultimo)
    except FileNotFoundError:
        pass
    for indice in range(max_files - 1, 0, -1):
        origem = f"{caminho}.{indice}"
        if os.path.exists(origem):
            os.replace(origem, f"{caminho}.{indice + 1}")
    os.replace(caminho, f"{caminho}.1")


def limpar_backups(
    diretorio, max_files=50, max_age_days=30, max_total_mb=256, agora=None,
):
    """Apaga backups antigos por idade, quantidade e total de bytes."""
    if not diretorio or not os.path.isdir(diretorio):
        return []
    agora = time.time() if agora is None else float(agora)
    arquivos = []
    with os.scandir(diretorio) as entradas:
        for entrada in entradas:
            if not entrada.name.endswith(".bak") or not entrada.is_file(follow_symlinks=False):
                continue
            try:
                info = entrada.stat(follow_symlinks=False)
            except OSError:
                continue
            arquivos.append([entrada.path, info.st_mtime, info.st_size])

    max_age_days = max(0, int(max_age_days))
    if max_age_days:
        limite = agora - max_age_days * 86400
        for item in list(arquivos):
            if item[1] < limite:
                try:
                    os.unlink(item[0])
                except FileNotFoundError:
                    pass
                arquivos.remove(item)

    arquivos.sort(key=lambda item: (item[1], item[0]), reverse=True)
    max_files = max(0, int(max_files))
    max_bytes = max(0, int(max_total_mb)) * 1024 * 1024
    total = 0
    mantidos = []
    for indice, item in enumerate(arquivos):
        cabe_quantidade = indice < max_files
        cabe_tamanho = max_bytes > 0 and total + item[2] <= max_bytes
        if cabe_quantidade and cabe_tamanho:
            mantidos.append(item[0])
            total += item[2]
            continue
        try:
            os.unlink(item[0])
        except FileNotFoundError:
            pass
    return mantidos
