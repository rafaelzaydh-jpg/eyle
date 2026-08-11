#!/usr/bin/env python3
"""Atualizacao 35: dependencias diretas e transitivas ficam fixadas."""
import os
from pathlib import Path


BASE = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _linhas_pacotes(nome):
    return [
        linha.strip() for linha in (BASE / nome).read_text(encoding="utf-8").splitlines()
        if linha.strip() and not linha.lstrip().startswith(("#", "-r "))
    ]


def test_requirements_nao_tem_dependencia_sem_versao():
    for nome in (
        "requirements.txt", "requirements-dev.txt",
        "requirements.lock", "requirements-dev.lock",
    ):
        for linha in _linhas_pacotes(nome):
            pacote = linha.split(";", 1)[0].strip()
            assert "==" in pacote, f"dependencia sem versao exata em {nome}: {linha}"


def test_lock_de_runtime_contem_transitivas_do_flask():
    texto = (BASE / "requirements.lock").read_text(encoding="utf-8").lower()
    for pacote in (
        "flask==", "werkzeug==", "jinja2==", "markupsafe==",
        "itsdangerous==", "click==", "blinker==",
    ):
        assert pacote in texto
