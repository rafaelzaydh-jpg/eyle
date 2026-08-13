#!/usr/bin/env python3
"""Verify release identity and artifact cleanliness for Eyle."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List


MIN_PYTHON = (3, 11)
_RUNTIME_DIRS = ("context", "memory", "workspace")
_FORBIDDEN_DIR_NAMES = {".git", ".pytest_cache", "__pycache__", "htmlcov"}
_FORBIDDEN_FILE_NAMES = {".coverage"}
_REMOVED_CORE_FILES = (
    "eyle/core/source_record.py",
    "eyle/core/evidence.py",
    "eyle/core/execution_trace.py",
    "eyle/core/prompt_accounting.py",
    "eyle/core/write_transaction.py",
    "eyle/core/operational_feedback.py",
)


class ReleaseIdentityError(ValueError):
    """The declared release identity or artifact shape is invalid."""


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


def _runtime_state_violations(base: Path) -> List[str]:
    violations: List[str] = []
    for dirname in _RUNTIME_DIRS:
        root = base / dirname
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.name != ".gitkeep":
                violations.append(str(path.relative_to(base)).replace("\\", "/"))
    return violations


def _generated_artifact_violations(base: Path) -> List[str]:
    violations: List[str] = []
    for path in base.rglob("*"):
        rel = str(path.relative_to(base)).replace("\\", "/")
        if path.is_dir() and path.name in _FORBIDDEN_DIR_NAMES:
            violations.append(rel + "/")
            continue
        if not path.is_file():
            continue
        if path.name in _FORBIDDEN_FILE_NAMES or path.suffix in {".pyc", ".pyo"}:
            violations.append(rel)
    return violations


def _removed_contract_violations(base: Path) -> List[str]:
    return [rel for rel in _REMOVED_CORE_FILES if (base / rel).exists()]


def _public_tool_violations(base: Path, manifesto: Dict[str, Any]) -> List[str]:
    # Import only after the Python floor has been checked. The registry itself is
    # the source of truth; the manifest must match it exactly and in order.
    base_text = str(base.resolve())
    inserted = False
    if base_text not in sys.path:
        sys.path.insert(0, base_text)
        inserted = True
    try:
        from eyle.core.tools import TOOLS
        registry = list(TOOLS.keys())
    finally:
        if inserted:
            try:
                sys.path.remove(base_text)
            except ValueError:
                pass
    declared = manifesto.get("public_tools")
    if declared != registry:
        return [
            "release_manifest.json:public_tools diverge do registry real "
            f"(manifest={declared!r}, registry={registry!r})"
        ]
    return []


def validar_artefato_release(base_dir: os.PathLike[str] | str, manifesto: Dict[str, Any] | None = None) -> None:
    base = Path(base_dir)
    manifest = manifesto if isinstance(manifesto, dict) else _carregar_json(base / "release_manifest.json")
    violations: List[str] = []
    violations.extend(f"estado Runtime proibido: {item}" for item in _runtime_state_violations(base))
    violations.extend(f"artefato gerado proibido: {item}" for item in _generated_artifact_violations(base))
    violations.extend(f"contrato removido reapareceu: {item}" for item in _removed_contract_violations(base))
    violations.extend(_public_tool_violations(base, manifest))

    publication = manifest.get("publication") if isinstance(manifest.get("publication"), dict) else {}
    if publication.get("requires_extracted_artifact_verification") is not True:
        violations.append(
            "release_manifest.json:publication.requires_extracted_artifact_verification precisa ser true"
        )

    if violations:
        raise ReleaseIdentityError(
            "artefato de release invalido:\n- " + "\n- ".join(sorted(set(violations)))
        )


def validar_identidade_release(base_dir: os.PathLike[str] | str) -> Dict[str, str]:
    """Validate identity, public registry and release-artifact cleanliness."""
    if sys.version_info < MIN_PYTHON:
        raise ReleaseIdentityError(
            f"Eyle 2.7.5 requer Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+; "
            f"runtime atual={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        )

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
    marker = (
        f"**Version:** {identidade['app_version']} · "
        f"**Schema:** {identidade['config_schema_version']} · "
        f"**Revision:** {identidade['revision']}"
    )
    if marker not in readme:
        divergencias.append(
            "README.md does not contain the canonical English release identity marker: " + marker
        )

    if divergencias:
        raise ReleaseIdentityError(
            "identidade de release divergente:\n- " + "\n- ".join(divergencias)
        )

    validar_artefato_release(base, manifesto)
    return identidade


def main() -> int:
    base = Path(__file__).resolve().parent.parent.parent
    identidade = validar_identidade_release(base)
    print(
        "release artifact ok: "
        f"app={identidade['app_version']} "
        f"schema={identidade['config_schema_version']} "
        f"revision={identidade['revision']} "
        f"python>={MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
