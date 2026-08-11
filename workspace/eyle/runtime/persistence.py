#!/usr/bin/env python3
"""Primitivas de persistencia atomica usadas pela memoria da Eyle."""
import json
import os
import stat
import tempfile


def _publicar_atomico(caminho, escrever):
    caminho = os.fspath(caminho)
    diretorio = os.path.dirname(os.path.abspath(caminho))
    os.makedirs(diretorio, exist_ok=True)
    modo_anterior = None
    try:
        modo_anterior = stat.S_IMODE(os.stat(caminho).st_mode)
    except FileNotFoundError:
        pass

    descritor, temporario = tempfile.mkstemp(
        prefix=f".{os.path.basename(caminho)}.", suffix=".tmp", dir=diretorio,
    )
    try:
        with os.fdopen(descritor, "w", encoding="utf-8", newline="") as arquivo:
            escrever(arquivo)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        if modo_anterior is not None:
            os.chmod(temporario, modo_anterior)
        os.replace(temporario, caminho)
        temporario = None
        try:
            fd_diretorio = os.open(diretorio, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd_diretorio)
        finally:
            os.close(fd_diretorio)
    finally:
        if temporario is not None:
            try:
                os.unlink(temporario)
            except FileNotFoundError:
                pass


def salvar_json_atomico(caminho, dados, *, indent=2):
    """Publica JSON completo; falha durante dump preserva o destino anterior."""
    _publicar_atomico(
        caminho,
        lambda arquivo: json.dump(
            dados, arquivo, ensure_ascii=False, indent=indent,
        ),
    )
