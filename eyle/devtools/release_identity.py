#!/usr/bin/env python3
"""Fail-closed Rev3.7.2 release verifier.

The verifier checks the current architecture only. Historical upgrade contracts
belong to explicit migration tools and CHANGELOG, never to the runtime verifier.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

MIN_PYTHON = (3, 11)
CORE_FILES = {"__init__.py", "agent.py", "ecc.py", "evidence.py", "memory.py", "session.py"}
FORBIDDEN_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
FORBIDDEN_RUNTIME_FILES = {"agent_pendente.json", "telemetry.sqlite3", "fila.json", "conversa.json"}
FORBIDDEN_PATHS = {
    "eyle/providers/standard.py",
    "eyle/providers/standard_impl",
    "eyle/providers/workspace_transaction.py",
    "eyle/providers/sandbox_promotion.py",
}
REQUIRED_STANDARD_FILES = {
    "__init__.py", "registry.py", "common.py", "tools.py", "contracts.py",
    "workspace_transaction.py", "sandbox_promotion.py",
}
REMOVED_RUNTIME_MARKERS = {
    "generated_token_fuse",
    "generated_token_limit",
    "task_deadline_seconds",
    "deadline_monotonic",
    "deadline_remaining_seconds",
    "TASK_DEADLINE_EXCEEDED",
    "limite_snapshot",
    "project_memory_view",
    "automatic_temporary",
    "globals().setdefault",
    "eyle.providers.standard_impl",
    "LEGACY_AGENT_PENDENTE_PATH",
    "AGENT_PENDENTE_PATH",
    "_public_confirmation",
    "max_search_matches",
    "max_search_ranges",
    "max_file_read_lines",
    "temporary_graph_records",
    "DEEPSEEK_API_KEY",
    "DEFAULT_MODEL",
}


class ReleaseIdentityError(RuntimeError):
    pass


def _json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReleaseIdentityError(f"JSON object required:{path.name}")
    return value


def identidade_config(base: Path) -> Dict[str, str]:
    cfg = _json(base / "config.json")
    return {k: str(cfg.get(k) or "") for k in ("app_version", "config_schema_version", "revision")}


def _artifact_violations(base: Path) -> List[str]:
    out: List[str] = []
    for path in base.rglob("*"):
        rel = str(path.relative_to(base)).replace("\\", "/")
        if path.is_dir() and path.name in FORBIDDEN_DIRS:
            out.append(f"generated artifact:{rel}/")
        elif path.is_file() and (path.suffix in {".pyc", ".pyo"} or path.name in FORBIDDEN_RUNTIME_FILES):
            out.append(f"generated artifact:{rel}")
    return out


def _source_markers(base: Path) -> List[str]:
    out: List[str] = []
    roots = [base / "eyle", base / "llm", base / "server", base / "web"]
    excluded = {
        str((base / "eyle/devtools/migrate_memory_v11_to_v12.py").resolve()),
        str((base / "eyle/devtools/release_identity.py").resolve()),
    }
    for source_root in roots:
        if not source_root.exists():
            continue
        for path in source_root.rglob("*.py"):
            if str(path.resolve()) in excluded:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            rel = str(path.relative_to(base)).replace("\\", "/")
            for marker in REMOVED_RUNTIME_MARKERS:
                if marker in text:
                    out.append(f"removed runtime marker:{marker}:{rel}")
    return out


def _architecture_violations(base: Path, manifest: Dict[str, Any]) -> List[str]:
    out: List[str] = []

    core = base / "eyle/core"
    actual_core = {p.name for p in core.glob("*.py")}
    if actual_core != CORE_FILES:
        out.append(f"core files invalid:{sorted(actual_core)!r}")

    for rel in FORBIDDEN_PATHS:
        if (base / rel).exists():
            out.append(f"legacy path returned:{rel}")

    standard = base / "eyle/providers/standard"
    if not standard.is_dir():
        out.append("canonical standard provider package missing")
    else:
        actual = {p.name for p in standard.glob("*.py")}
        missing = REQUIRED_STANDARD_FILES - actual
        if missing:
            out.append(f"canonical standard provider files missing:{sorted(missing)!r}")

    out.extend(_source_markers(base))

    base_text = str(base.resolve())
    inserted = False
    if base_text not in sys.path:
        sys.path.insert(0, base_text)
        inserted = True
    try:
        from eyle import __revision__, __schema_version__, __version__
        from eyle.core.ecc import catalog as ecc_catalog
        from eyle.core.session import AgentSession, SESSION_SCHEMA_VERSION
        from eyle.host import build_bundled_host
        from eyle.runtime.continuation import PENDING_SCHEMA_VERSION
        from eyle.runtime.execution_context import ExecutionContext, EXECUTION_CONTINUITY_SCHEMA_VERSION
        from eyle.runtime.memory_graph import MEMORY_GRAPH_SCHEMA_VERSION
        from llm.executar import PROMPT_ECC
        from llm.structured import canonicalize_wire_response, schema_for_profile, wire_schema_for_profile

        cfg = _json(base / "config.json")
        expected_identity = {
            "app_version": __version__,
            "config_schema_version": __schema_version__,
            "revision": __revision__,
        }
        if {k: cfg.get(k) for k in expected_identity} != expected_identity:
            out.append("config identity != runtime identity")
        if "agent" in cfg:
            out.append("removed top-level agent config returned")
        llm_cfg = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
        if int(llm_cfg.get("provider_token_budget_per_message") or 0) != 150000:
            out.append("provider_token_budget_per_message must default to 150000")
        if int(llm_cfg.get("context_window_tokens") or 0) != 50000:
            out.append("context_window_tokens must default to 50000")

        host = build_bundled_host(str(base))
        registry = host.registry
        if manifest.get("public_capabilities") != registry.names():
            out.append("manifest public_capabilities != registry")

        memory_ctx = host.provider_context().get("core_memory") or {}
        surface = ecc_catalog(registry, cfg, registry.names(), memory_enabled=bool(memory_ctx))
        expected_ops = {
            "explorar": [item.get("operation") for item in surface.get("explorar") or []],
            "construir": [item.get("operation") for item in surface.get("construir") or []],
            "concluir": ["concluir"],
        }
        if manifest.get("ecc_operations") != expected_ops:
            out.append("manifest ecc_operations != ECC catalog")

        if len(PROMPT_ECC) > 6000:
            out.append(f"stable prompt too verbose:{len(PROMPT_ECC)}")
        surface_chars = len(json.dumps(surface, ensure_ascii=False, separators=(",", ":")))
        if surface_chars > 5000:
            out.append(f"ECC operation surface too verbose:{surface_chars}")

        schema = schema_for_profile("ecc")
        wire = wire_schema_for_profile("ecc")
        if wire == schema or wire.get("additionalProperties") is not True:
            out.append("wire/canonical ECC schema boundary invalid")
        if set(schema.get("properties") or {}) != {"decision", "memory_delta"}:
            out.append("canonical ECC envelope invalid")
        if canonicalize_wire_response({"type": "concluir", "answer": "ok"}) != {
            "decision": {"type": "concluir", "response": "ok"}, "memory_delta": []
        }:
            out.append("wire canonicalization invalid")

        session = AgentSession("verify")
        if set(session.memory_view) != {"node_ids", "coverage", "frontiers", "selector", "overview"}:
            out.append("Session Memory activation state invalid")

        execution = ExecutionContext.from_config(cfg, execution_id="verify")
        if hasattr(execution, "deadline_monotonic") or hasattr(execution, "generated_token_limit"):
            out.append("legacy execution fields returned")
        if EXECUTION_CONTINUITY_SCHEMA_VERSION != "execution-continuity-v3":
            out.append("execution continuity schema mismatch")
        if SESSION_SCHEMA_VERSION != "2.7.5-r3.7.2-ecc":
            out.append("session schema mismatch")
        if PENDING_SCHEMA_VERSION != "13-ecc":
            out.append("pending schema mismatch")
        if manifest.get("session_schema") != SESSION_SCHEMA_VERSION:
            out.append("manifest session_schema mismatch")
        if manifest.get("pending_schema") != PENDING_SCHEMA_VERSION:
            out.append("manifest pending_schema mismatch")

        symbol_spec = registry.spec("standard.symbol_relations")
        symbol_props = dict((symbol_spec.get("input_schema") or {}).get("properties") or {})
        if "page_size" not in symbol_props or "max_edges" in symbol_props:
            out.append("symbol_relations public paging contract is not canonical")

        if MEMORY_GRAPH_SCHEMA_VERSION != "2.7.5-r3.7.1-memory-graph-v12":
            out.append("Memory Graph v12 identity mismatch")
        if manifest.get("memory_graph_schema") != MEMORY_GRAPH_SCHEMA_VERSION:
            out.append("manifest memory_graph_schema mismatch")

        service_text = (base / "eyle/runtime/service.py").read_text(encoding="utf-8")
        if "def registrar_mensagem_com_snapshot(role, texto, metadata=None):" not in service_text:
            out.append("conversation registration contract is not canonical")
        memory_text = (base / "eyle/core/memory.py").read_text(encoding="utf-8")
        if "def materialize_explicit_memory_view(" not in memory_text:
            out.append("explicit Memory materializer missing")
        server_text = (base / "server/server.py").read_text(encoding="utf-8")
        if 'ADAPTER_VERSION = "2.7.5-rev3.7.2"' not in server_text:
            out.append("Adapter version identity mismatch")
        if manifest.get("adapter_version") != "2.7.5-rev3.7.2":
            out.append("manifest adapter_version mismatch")
        if "def prepare_upstream(" not in server_text or "def _prepare_upstream(" in server_text:
            out.append("Adapter upstream preparation path is not canonical")
        if 'if "max_tokens" in body:' not in server_text:
            out.append("Adapter does not reject removed max_tokens input alias")
        web_js = (base / "web/static/app.js").read_text(encoding="utf-8")
        if "data.confirmation" in web_js or "msg.confirmation" in web_js:
            out.append("removed confirmation UI alias returned")
    except Exception as exc:
        out.append(f"architecture verifier runtime failure:{type(exc).__name__}:{exc}")
    finally:
        if inserted:
            sys.path.remove(base_text)
    return out


def validar_artefato_release(base_dir: os.PathLike[str] | str, manifesto: Dict[str, Any] | None = None) -> None:
    base = Path(base_dir)
    manifest = manifesto if isinstance(manifesto, dict) else _json(base / "release_manifest.json")
    violations = _artifact_violations(base) + _architecture_violations(base, manifest)
    pub = manifest.get("publication") or {}
    if pub.get("requires_extracted_artifact_verification") is not True:
        violations.append("extracted artifact verification not required")
    if pub.get("experimental") is not False:
        violations.append("release must not be experimental")
    if pub.get("mainline_base") != "Rev3.7.1":
        violations.append("Rev3.7.2 mainline base mismatch")
    if violations:
        raise ReleaseIdentityError("invalid release artifact:\n- " + "\n- ".join(sorted(set(violations))))


def validar_identidade_release(base_dir: os.PathLike[str] | str) -> Dict[str, str]:
    if sys.version_info < MIN_PYTHON:
        raise ReleaseIdentityError("Python 3.11+ required")
    base = Path(base_dir)
    identity = identidade_config(base)
    manifest = _json(base / "release_manifest.json")
    errors: List[str] = []
    for key, value in identity.items():
        if str(manifest.get(key) or "") != value:
            errors.append(f"manifest {key} mismatch")
    if str(manifest.get("release")) != identity["app_version"]:
        errors.append("release != app_version")
    if errors:
        raise ReleaseIdentityError("release identity mismatch:\n- " + "\n- ".join(errors))
    validar_artefato_release(base, manifest)
    return identity


def main() -> int:
    base = Path(__file__).resolve().parent.parent.parent
    identity = validar_identidade_release(base)
    print(
        "release artifact ok: "
        f"app={identity['app_version']} schema={identity['config_schema_version']} "
        f"revision={identity['revision']} python>={MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
