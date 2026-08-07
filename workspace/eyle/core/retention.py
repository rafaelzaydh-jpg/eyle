"""Retention policy required by safe editing backups."""
from __future__ import annotations

import os
import time

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
