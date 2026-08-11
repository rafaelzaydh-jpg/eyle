#!/usr/bin/env python3
"""
seguranca.py
------------
Primitivas de seguranca compartilhadas pelos modulos que acessam o
projeto real no disco.

O caminho relativo pode vir de uma decisao da LLM. Por isso, nenhum
chamador deve montar caminhos com ``os.path.join`` e abrir o resultado
diretamente: use ``_resolver_caminho_seguro`` antes de qualquer leitura
ou escrita.
"""
import ntpath
import os


def _resolver_caminho_seguro(caminho_projeto, caminho_relativo):
    """
    Resolve ``caminho_relativo`` dentro de ``caminho_projeto``.

    Devolve o caminho absoluto real somente quando o alvo permanece
    dentro da raiz depois de resolver ``..`` e symlinks. Caminhos
    absolutos (inclusive no formato Windows), caminhos em outro drive e
    entradas invalidas sao rejeitados com ``None``.

    O arquivo nao precisa existir: os chamadores continuam responsaveis
    por decidir se esperam arquivo, diretorio ou um caminho novo.
    """
    if not isinstance(caminho_projeto, (str, bytes, os.PathLike)):
        return None
    if not isinstance(caminho_relativo, (str, bytes, os.PathLike)):
        return None

    try:
        relativo = os.fspath(caminho_relativo)
        if not relativo:
            return None

        # os.path.isabs segue o SO atual; ntpath cobre tambem entradas
        # como C:\\arquivo e \\\\servidor\\compartilhamento quando a Eyle
        # estiver rodando em Linux/macOS e receber um caminho da LLM.
        if os.path.isabs(relativo) or ntpath.isabs(relativo):
            return None
        drive, _ = ntpath.splitdrive(relativo)
        if drive:
            return None

        base = os.path.realpath(os.fspath(caminho_projeto))
        alvo = os.path.realpath(os.path.join(base, relativo))
        if os.path.commonpath([base, alvo]) != base:
            return None
        return alvo
    except (OSError, TypeError, ValueError):
        # Inclui drives incompatíveis no Windows, bytes/str misturados e
        # entradas malformadas. Falhar fechado e mais seguro que deixar a
        # excecao virar um acesso sem validacao em algum chamador.
        return None
