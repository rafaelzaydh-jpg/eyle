#!/usr/bin/env python3
"""Fonte de verificação da identidade de release da Eyle.

O arquivo de configuração continua sendo a fonte primária. O manifesto e o
README precisam declarar os mesmos valores antes de um pacote ser publicado.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


class ReleaseIdentityError(ValueError):
    """A identidade declarada nos artefatos de release divergiu."""


def _carregar_json(caminho: Path) -> Dict[str, Any]:
    try:
        conteudo = json.loads(caminho.read_text(encoding="utf-8"))
    except FileNotFoundError as erro:
        raise ReleaseIdentityError(f"arquivo ausente: {caminho.name}") from erro
    except json.JSONDecodeError as erro:
        raise ReleaseIdentityError(
            f"JSON invalido em {caminho.name}: linha {erro.lineno}, coluna {erro.colno}"
        ) from erro
    if not isinstance(conteudo, dict):
        raise ReleaseIdentityError(f"{caminho.name} precisa conter um objeto JSON")
    return conteudo


def identidade_config(base_dir: os.PathLike[str] | str) -> Dict[str, str]:
    base = Path(base_dir)
    config = _carregar_json(base / "config.json")
    identidade = {
        "app_version": config.get("app_version"),
        "config_schema_version": config.get("config_schema_version"),
        "revision": config.get("revision"),
    }
    invalidos = [
        chave for chave, valor in identidade.items()
        if not isinstance(valor, str) or not valor.strip()
    ]
    if invalidos:
        raise ReleaseIdentityError(
            "config.json nao define identidade valida: " + ", ".join(invalidos)
        )
    return {chave: valor.strip() for chave, valor in identidade.items()}


def validar_identidade_release(base_dir: os.PathLike[str] | str) -> Dict[str, str]:
    """Valida config.json, release_manifest.json e marcador do README."""
    base = Path(base_dir)
    identidade = identidade_config(base)
    manifesto = _carregar_json(base / "release_manifest.json")

    divergencias = []
    for chave, esperado in identidade.items():
        recebido = manifesto.get(chave)
        if recebido != esperado:
            divergencias.append(
                f"release_manifest.json:{chave}={recebido!r}; esperado {esperado!r}"
            )
    if manifesto.get("release") != identidade["app_version"]:
        divergencias.append(
            "release_manifest.json:release precisa ser igual a app_version"
        )

    try:
        readme = (base / "README.md").read_text(encoding="utf-8")
    except FileNotFoundError as erro:
        raise ReleaseIdentityError("arquivo ausente: README.md") from erro
    marcador = (
        f"**Versão:** {identidade['app_version']} · "
        f"**Schema:** {identidade['config_schema_version']} · "
        f"**Revisão:** {identidade['revision']}"
    )
    if marcador not in readme:
        divergencias.append(f"README.md nao contem o marcador: {marcador}")

    if divergencias:
        raise ReleaseIdentityError(
            "identidade de release divergente:\n- " + "\n- ".join(divergencias)
        )
    return identidade


def main() -> int:
    base = Path(__file__).resolve().parent.parent
    identidade = validar_identidade_release(base)
    print(
        "release identity ok: "
        f"app={identidade['app_version']} "
        f"schema={identidade['config_schema_version']} "
        f"revision={identidade['revision']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
