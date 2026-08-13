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

_REQUIRED_MEMORY_KERNEL_FILES = (
    "eyle/core/memory.py",
    "eyle/core/memory_store.py",
    "eyle/core/memory_navigation.py",
)
_REMOVED_MEMORY_CONTRACT_TERMS = {
    "eyle/core/memory.py": ("def search_memory(", "def store_memory(", "def _load(", "entries[-200:]"),
}

_REMOVED_CLAIM_FILES = ("eyle/core/claim_review.py",)
_REMOVED_CLAIM_CONTRACT_TERMS = {
    "eyle/core/agent.py": ("claim_review", "_run_claim_verification", "executar_verificador_claims"),
    "eyle/runtime/config.py": ("claims", "claim_config", "ClaimConfigError"),
    "eyle/runtime/history.py": ("claim_packet", "claim_review"),
    "llm/structured.py": ("claim_verifier", "CLAIM_REVIEW"),
    "llm/executar.py": ("PROMPT_CLAIM", "executar_verificador_claims", "claim_verifier"),
}



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



def _removed_claim_contract_violations(base: Path) -> List[str]:
    violations: List[str] = []
    for rel in _REMOVED_CLAIM_FILES:
        if (base / rel).exists():
            violations.append(f"arquivo removido reapareceu:{rel}")
    for rel, terms in _REMOVED_CLAIM_CONTRACT_TERMS.items():
        path = base / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for term in terms:
            if term in text:
                violations.append(f"{rel}:{term}")
    return violations

def _memory_kernel_contract_violations(base: Path) -> List[str]:
    violations: List[str] = []
    for rel in _REQUIRED_MEMORY_KERNEL_FILES:
        if not (base / rel).is_file():
            violations.append(f"Memory Kernel ausente: {rel}")
    for rel, terms in _REMOVED_MEMORY_CONTRACT_TERMS.items():
        path = base / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for term in terms:
            if term in text:
                violations.append(f"contrato de memoria legado reapareceu: {rel}:{term}")
    return violations


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



def _model_surface_violations(base: Path) -> List[str]:
    violations: List[str] = []
    base_text = str(base.resolve())
    inserted = False
    if base_text not in sys.path:
        sys.path.insert(0, base_text)
        inserted = True
    try:
        from llm.executar import PROMPT_AGENTE
        from llm.structured import contract_instruction
        from eyle.core.tools import TOOLS

        if len(PROMPT_AGENTE) >= 1700:
            violations.append(f"PROMPT_AGENTE excessivo:{len(PROMPT_AGENTE)} chars")
        for required in (
            "prior_conversation is retained context",
            "Memory is persistent prior cognition",
            "available_capabilities names invokable actions",
            "runtime_observations/current_material represent current physically observed state",
            "Investigation.conclusion states what grounding establishes about its goal",
            "Task.result states what was achieved against completion_criteria",
        ):
            if required not in PROMPT_AGENTE:
                violations.append(f"epistemic clarity ausente:{required}")
        instruction = contract_instruction("agent")
        if len(instruction) >= 220:
            violations.append(f"contract_instruction excessivo:{len(instruction)} chars")

        fixed = [PROMPT_AGENTE, instruction]
        def collect_descriptions(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "description" and isinstance(item, str):
                        fixed.append(item)
                    collect_descriptions(item)
            elif isinstance(value, list):
                for item in value:
                    collect_descriptions(item)

        for name, entry in TOOLS.items():
            description = str(entry.get("description") or "")
            returns = str(entry.get("returns") or "")
            caveats = [str(item) for item in (entry.get("caveats") or [])]
            if len(description) > 90:
                violations.append(f"tool description excessiva:{name}:{len(description)}")
            if len(returns) > 100:
                violations.append(f"tool returns excessivo:{name}:{len(returns)}")
            for caveat in caveats:
                if len(caveat) > 120:
                    violations.append(f"tool caveat excessiva:{name}:{len(caveat)}")
            fixed.append(description)
            fixed.append(returns)
            fixed.extend(caveats)
            collect_descriptions(entry.get("input_schema") or {})
        surface = "\n".join(fixed).lower()
        for phrase in (
            "not a prerequisite", "usually do not need", "do not create",
            "never use it merely", "choose one capability",
            "decide again from the unchanged",
        ):
            if phrase in surface:
                violations.append(f"model surface prescritiva:{phrase}")
    finally:
        if inserted:
            try:
                sys.path.remove(base_text)
            except ValueError:
                pass

    agent_path = base / "eyle/core/agent.py"
    if agent_path.is_file():
        agent_text = agent_path.read_text(encoding="utf-8")
        for term in ('"instruction"', "semantic_followup", "Choose one capability from capability_index"):
            if term in agent_text:
                violations.append(f"runtime feedback prescritivo:{term}")
    return violations

def validar_artefato_release(base_dir: os.PathLike[str] | str, manifesto: Dict[str, Any] | None = None) -> None:
    base = Path(base_dir)
    manifest = manifesto if isinstance(manifesto, dict) else _carregar_json(base / "release_manifest.json")
    violations: List[str] = []
    violations.extend(f"estado Runtime proibido: {item}" for item in _runtime_state_violations(base))
    violations.extend(f"artefato gerado proibido: {item}" for item in _generated_artifact_violations(base))
    violations.extend(f"contrato removido reapareceu: {item}" for item in _removed_contract_violations(base))
    violations.extend(f"contrato Claim removido reapareceu: {item}" for item in _removed_claim_contract_violations(base))
    violations.extend(_memory_kernel_contract_violations(base))
    violations.extend(_public_tool_violations(base, manifest))
    violations.extend(_model_surface_violations(base))

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
