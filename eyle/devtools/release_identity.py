#!/usr/bin/env python3
"""Release verifier for Eyle 2.7.5 Rev1.5.1.

The verifier protects the Capability Provider Architecture rather than old
workspace/tool-specific implementation details.
"""
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
_GENERIC_CORE_FILES = {
    "__init__.py", "agent.py", "continuation.py", "decision.py",
    "investigation.py", "session.py", "tasks.py", "token_budget.py",
    "validation.py",
}
_REQUIRED_PROVIDER_FILES = {
    "eyle/capabilities/registry.py",
    "eyle/contracts/capability.py", "eyle/contracts/observation.py",
    "eyle/host.py",
    "eyle/providers/standard.py", "eyle/providers/workspace_transaction.py",
    "eyle/providers/memory.py",
}
_REMOVED_CORE_DOMAIN_FILES = {
    "tools.py", "editing.py", "git_tools.py", "memory.py", "memory_navigation.py",
    "memory_store.py", "microsandbox_backend.py", "objective_scope.py", "post_write.py",
    "project_inspection.py", "sandbox.py", "security.py", "symbols.py", "text_hash.py",
    "transactions.py", "workspace.py", "workspace_io.py", "workspace_policy.py",
    "code_relations.py", "write_transaction.py", "claim_review.py", "evidence.py",
    "source_record.py", "execution_trace.py", "prompt_accounting.py",
}


class ReleaseIdentityError(ValueError):
    pass


def _json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReleaseIdentityError(f"arquivo ausente: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise ReleaseIdentityError(f"JSON invalido em {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseIdentityError(f"{path.name} precisa conter objeto JSON")
    return value


def identidade_config(base_dir: os.PathLike[str] | str) -> Dict[str, str]:
    config = _json(Path(base_dir) / "config.json")
    result = {k: config.get(k) for k in ("app_version", "config_schema_version", "revision")}
    bad = [k for k, v in result.items() if not isinstance(v, str) or not v.strip()]
    if bad:
        raise ReleaseIdentityError("identidade invalida: " + ", ".join(bad))
    return {k: str(v).strip() for k, v in result.items()}


def _artifact_violations(base: Path) -> List[str]:
    out: List[str] = []
    for dirname in _RUNTIME_DIRS:
        root = base / dirname
        if root.exists():
            for path in root.rglob("*"):
                if path.is_file() and path.name != ".gitkeep":
                    out.append(f"estado Runtime proibido:{path.relative_to(base)}")
    for path in base.rglob("*"):
        rel = str(path.relative_to(base)).replace("\\", "/")
        if path.is_dir() and path.name in _FORBIDDEN_DIR_NAMES:
            out.append(f"artefato gerado proibido:{rel}/")
        elif path.is_file() and (path.name in _FORBIDDEN_FILE_NAMES or path.suffix in {".pyc", ".pyo"}):
            out.append(f"artefato gerado proibido:{rel}")
    return out


def _architecture_violations(base: Path, manifest: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    core = base / "eyle" / "core"
    actual_core = {p.name for p in core.glob("*.py")}
    unexpected = sorted(actual_core - _GENERIC_CORE_FILES)
    if unexpected:
        out.append("Core contem modulos de dominio/nao canonicos:" + ",".join(unexpected))
    returned = sorted(_REMOVED_CORE_DOMAIN_FILES & actual_core)
    if returned:
        out.append("modulos de dominio reapareceram no Core:" + ",".join(returned))
    for rel in _REQUIRED_PROVIDER_FILES:
        if not (base / rel).is_file():
            out.append(f"infraestrutura provider ausente:{rel}")

    base_text = str(base.resolve())
    inserted = False
    if base_text not in sys.path:
        sys.path.insert(0, base_text); inserted = True
    try:
        from eyle.contracts.capability import RESULT_FIELDS, physical_effect
        from eyle.host import build_bundled_host
        from llm.executar import PROMPT_AGENTE
        from llm.structured import schema_for_profile

        registry = build_bundled_host(str(base)).registry
        names = registry.names()
        declared = manifest.get("public_capabilities")
        if declared != names:
            out.append(f"public_capabilities diverge do registry: manifest={declared!r} registry={names!r}")
        if manifest.get("bundled_providers") != ["standard", "memory"]:
            out.append("bundled_providers deve declarar standard e memory")
        if "physical_effect" not in RESULT_FIELDS:
            out.append("resultado canonico nao possui physical_effect")
        effect = physical_effect("demo.resource", "demo", "persistent", changed=True)
        if set(effect) != {"resource", "operation", "persistence", "changed"}:
            out.append("physical_effect universal shape invalido")

        schema = schema_for_profile("agent")
        variants = schema["properties"]["action"]["anyOf"]
        kinds = [v["properties"]["kind"]["enum"][0] for v in variants]
        if kinds != ["capability_calls", "await_user", "complete"]:
            out.append(f"action kinds invalidos:{kinds!r}")
        schema_text = json.dumps(schema, ensure_ascii=False)
        for legacy in ('"patches"', '"tool_calls"', '"completion_mode"'):
            if legacy in schema_text:
                out.append(f"contrato estruturado legado:{legacy}")

        lower_prompt = PROMPT_AGENTE.lower()
        required = (
            "sole semantic authority", "independent providers", "capabilities are resources, not mandatory steps",
            "if you are unsure whether you possess enough information", "available capability is not evidence",
            "grounding_ids and effect_ids", "runtime validates coordinate existence and identity only",
        )
        for text in required:
            if text not in lower_prompt:
                out.append(f"conceito generico ausente no Main prompt:{text}")
        for domain_name in ("search_code", "read_file", "workspace_transaction", "run_tests", "petbot", "router.restart"):
            if domain_name in lower_prompt:
                out.append(f"Main prompt acoplado a dominio:{domain_name}")
        for forcing in ("if the request contains", "always use", "always investigate", "must create a task"):
            if forcing in lower_prompt:
                out.append(f"Main prompt prescritivo:{forcing}")
    finally:
        if inserted:
            try: sys.path.remove(base_text)
            except ValueError: pass

    config = _json(base / "config.json")
    if set((config.get("agent") or {}).keys()) != {"task_deadline_seconds"}:
        out.append("agent config deve conter apenas task_deadline_seconds")
    providers = config.get("providers")
    if not isinstance(providers, dict) or set(providers) != {"standard", "memory"}:
        out.append("config.providers deve declarar exatamente standard e memory no host bundled")
    for forbidden in ("codar", "tools", "workspace"):
        if forbidden in config:
            out.append(f"config top-level de dominio/legado:{forbidden}")

    agent_source = (base / "eyle/core/agent.py").read_text(encoding="utf-8").lower()
    for forbidden in ("from eyle.providers.standard", "import eyle.providers.standard", "workspace_transaction", "search_code", "read_file", "run_tests", "default_registry"):
        if forbidden in agent_source:
            out.append(f"Core agent acoplado ao provider standard/global:{forbidden}")

    service_source = (base / "eyle/runtime/service.py").read_text(encoding="utf-8").lower()
    for forbidden in ("providers.standard", "standard_impl", "discover_project"):
        if forbidden in service_source:
            out.append(f"runtime/service acoplado ao provider standard:{forbidden}")

    for root_name in ("eyle/providers", "eyle/capabilities"):
        root = base / root_name
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            if "from eyle.core" in text or "import eyle.core" in text:
                out.append(f"provider/capability importa Core:{path.relative_to(base)}")

    capability_init = (base / "eyle/capabilities/__init__.py").read_text(encoding="utf-8").lower()
    for legacy in ("default_registry", "register_provider", "reset_providers"):
        if legacy in capability_init:
            out.append(f"registry global legado reapareceu:{legacy}")

    if "request_context" not in (base / "eyle/core/session.py").read_text(encoding="utf-8"):
        out.append("AgentSession sem request_context autoritativo")
    return out


def validar_artefato_release(base_dir: os.PathLike[str] | str, manifesto: Dict[str, Any] | None = None) -> None:
    base = Path(base_dir)
    manifest = manifesto if isinstance(manifesto, dict) else _json(base / "release_manifest.json")
    violations = _artifact_violations(base) + _architecture_violations(base, manifest)
    publication = manifest.get("publication") if isinstance(manifest.get("publication"), dict) else {}
    if publication.get("requires_extracted_artifact_verification") is not True:
        violations.append("publication.requires_extracted_artifact_verification precisa ser true")
    if violations:
        raise ReleaseIdentityError("artefato de release invalido:\n- " + "\n- ".join(sorted(set(violations))))


def validar_identidade_release(base_dir: os.PathLike[str] | str) -> Dict[str, str]:
    if sys.version_info < MIN_PYTHON:
        raise ReleaseIdentityError(f"Eyle requer Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+")
    base = Path(base_dir)
    identity = identidade_config(base)
    manifest = _json(base / "release_manifest.json")
    errors = []
    for key, expected in identity.items():
        if manifest.get(key) != expected:
            errors.append(f"release_manifest.json:{key}={manifest.get(key)!r}; esperado {expected!r}")
    if manifest.get("release") != identity["app_version"]:
        errors.append("release precisa ser igual a app_version")
    readme = (base / "README.md").read_text(encoding="utf-8")
    marker = f"**Version:** {identity['app_version']} · **Schema:** {identity['config_schema_version']} · **Revision:** {identity['revision']}"
    if marker not in readme:
        errors.append("README sem marcador canonico: " + marker)
    if errors:
        raise ReleaseIdentityError("identidade de release divergente:\n- " + "\n- ".join(errors))
    validar_artefato_release(base, manifest)
    return identity


def main() -> int:
    base = Path(__file__).resolve().parent.parent.parent
    identity = validar_identidade_release(base)
    print(f"release artifact ok: app={identity['app_version']} schema={identity['config_schema_version']} revision={identity['revision']} python>={MIN_PYTHON[0]}.{MIN_PYTHON[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
