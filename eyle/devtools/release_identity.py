#!/usr/bin/env python3
"""Fail-closed Rev4.0.0 current-artifact verifier.

Historical release compatibility belongs to explicit migration tools/CHANGELOG.
This verifier checks only the current Eyle architecture and publication tree.
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
    "eyle/runtime/cognition_episode.py",
}
REQUIRED_STANDARD_FILES = {
    "__init__.py", "registry.py", "common.py", "tools.py", "contracts.py",
    "workspace_transaction.py", "sandbox_promotion.py",
}
REMOVED_RUNTIME_MARKERS = {
    "generated_token_fuse", "generated_token_limit", "task_deadline_seconds",
    "deadline_monotonic", "deadline_remaining_seconds", "TASK_DEADLINE_EXCEEDED",
    "limite_snapshot", "project_memory_view", "automatic_temporary",
    "globals().setdefault", "eyle.providers.standard_impl", "LEGACY_AGENT_PENDENTE_PATH",
    "AGENT_PENDENTE_PATH", "_public_confirmation", "max_search_matches",
    "max_search_ranges", "max_file_read_lines", "temporary_graph_records",
    "DEEPSEEK_API_KEY", "DEFAULT_MODEL", "ECC_PROTOCOL_RECOVERY",
    "ECC_PROTOCOL_UNRECOVERABLE", "CognitionEpisode",
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
    excluded = {
        str((base / "eyle/devtools/migrate_memory_v11_to_v12.py").resolve()),
        str((base / "eyle/devtools/release_identity.py").resolve()),
    }
    for source_root in (base / "eyle", base / "llm", base / "server", base / "web"):
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
        missing = REQUIRED_STANDARD_FILES - {p.name for p in standard.glob("*.py")}
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
        from eyle.runtime.execution_progress import NO_PROGRESS_REPEATS_AFTER_WARNING
        from eyle.runtime.memory_graph import MEMORY_GRAPH_SCHEMA_VERSION
        from llm.executar import ADAPTER_TRANSPORT_PROTOCOL, PROMPT_ECC
        from llm.protocol import CanonicalPrompt
        from llm.structured import canonicalize_wire_response, json_schema_response_format, schema_for_profile, wire_schema_for_profile

        cfg = _json(base / "config.json")
        expected = {"app_version": __version__, "config_schema_version": __schema_version__, "revision": __revision__}
        if {k: cfg.get(k) for k in expected} != expected:
            out.append("config identity != runtime identity")
        llm_cfg = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
        if "adapter_handshake_timeout_seconds" in llm_cfg or "adapter_status_timeout_seconds" not in llm_cfg:
            out.append("Adapter status timeout config is not current-only")
        if int(llm_cfg.get("provider_token_budget_per_message") or 0) != 150000:
            out.append("provider token budget default mismatch")
        if int(llm_cfg.get("context_window_tokens") or 0) != 50000:
            out.append("context window default mismatch")

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
        probe = CanonicalPrompt(
            stable={"ecc_navigation": {}, "runtime_environment": {}},
            dynamic={
                "current_request": "new request",
                "conversation": {
                    "conversation_id": "conv-verify",
                    "messages": [
                        {"role": "user", "content": "old question"},
                        {"role": "assistant", "content": "old answer"},
                    ],
                    "history_messages_materialized": 2,
                    "history_messages_omitted": 0,
                },
                "runtime_feedback": [],
            },
        ).messages("system")
        if probe[-1] != {"role": "user", "content": "new request"}:
            out.append("current_request is not final provider user message")
        if any("new request" in str(m.get("content") or "") for m in probe[:-1]):
            out.append("current_request duplicated before causal frontier")

        for profile in ("navigation", "explore", "build"):
            schema = schema_for_profile(profile)
            wire = wire_schema_for_profile(profile)
            fmt = json_schema_response_format(profile)
            if "oneOf" not in schema or "oneOf" not in wire:
                out.append(f"{profile} structured surface schema missing")
            if fmt.get("json_schema", {}).get("strict") is not True:
                out.append(f"{profile} strict provider wire schema missing")
        try:
            schema_for_profile("ecc")
            out.append("legacy monolithic ecc profile still accepted")
        except Exception:
            pass
        try:
            canonicalize_wire_response(
                {"decision": {"type": "concluir", "response": "old"}, "memory_delta": []},
                "navigation",
            )
            out.append("legacy decision wrapper still accepted")
        except Exception:
            pass
        current = canonicalize_wire_response(
            {"type": "concluir", "response": "ok", "memory_delta": []},
            "navigation",
        )
        if current.get("primary") != {"type": "concluir", "response": "ok"} or current.get("memory_delta") != []:
            out.append("current navigation wire canonicalization invalid")

        session = AgentSession("verify")
        if set(session.memory_view) != {"node_ids", "coverage", "frontiers", "selector", "overview"}:
            out.append("Session Memory activation state invalid")
        execution = ExecutionContext.from_config(cfg, execution_id="verify")
        usage = execution.usage_view()
        if "number_of_wire_retries" not in usage or "wire_retry_tokens" not in usage:
            out.append("wire retry telemetry missing")
        if "number_of_protocol_repairs" in usage or "protocol_recovery_tokens" in usage:
            out.append("retired protocol repair telemetry returned")
        if EXECUTION_CONTINUITY_SCHEMA_VERSION != "execution-continuity-v6":
            out.append("execution continuity schema mismatch")
        if SESSION_SCHEMA_VERSION != "2.7.5-r4.0.0-ecc":
            out.append("session schema mismatch")
        if NO_PROGRESS_REPEATS_AFTER_WARNING != 2:
            out.append("fixed-point bound mismatch")
        if PENDING_SCHEMA_VERSION != "16-ecc":
            out.append("pending schema mismatch")
        if MEMORY_GRAPH_SCHEMA_VERSION != "2.7.5-r3.7.1-memory-graph-v12":
            out.append("Memory Graph v12 identity mismatch")

        agent_text = (base / "eyle/core/agent.py").read_text(encoding="utf-8")
        if "ExecutionProgress.from_dict(session.execution_progress)" not in agent_text or "progress_tracker.observe(" not in agent_text:
            out.append("Eyle execution fixed-point tracker missing")
        if "wire_retry_surface: Optional[str] = None" not in agent_text or "ECC_WIRE_RETRY" not in agent_text:
            out.append("one fresh Eyle decision after wire failure missing")
        if "ECC_PROTOCOL_RECOVERY" in agent_text or "CognitionEpisode" in agent_text:
            out.append("provider protocol recovery leaked into Core")
        if '"cognition_reason": "wire_retry" if wire_retry else' not in agent_text:
            out.append("wire retry cognition telemetry classification missing")

        server_text = (base / "server/server.py").read_text(encoding="utf-8")
        for required in (
            'ADAPTER_VERSION = "2.7.5-rev3.7.6"',
            'ADAPTER_TRANSPORT_PROTOCOL = "eyle-adapter-transport-v2"',
            "MAX_UPSTREAM_ATTEMPTS_PER_LOGICAL_CALL = 2",
            "def normalize_structured(",
            "def _schema_instruction(",
            "def _repair_messages(",
            '@app.get("/ready")',
        ):
            if required not in server_text:
                out.append(f"simple Adapter contract missing:{required}")
        for forbidden in (
            "ADAPTER_HANDSHAKE_SCHEMA", "def handshake(", "_example_from_schema",
            "ast.literal_eval", "provider_token_budget_remaining", '@app.get("/v1/models")',
        ):
            if forbidden in server_text:
                out.append(f"Adapter responsibility leak:{forbidden}")
        executar_text = (base / "llm/executar.py").read_text(encoding="utf-8")
        if "_ensure_adapter_handshake" in executar_text or "ADAPTER_HANDSHAKE_SCHEMA" in executar_text:
            out.append("capability handshake returned")
        if "_ensure_adapter_ready(config)" not in executar_text:
            out.append("simple Adapter readiness preflight missing")
        if "provider_token_budget_remaining" in executar_text:
            out.append("global Eyle token budget leaked into Adapter payload")
        if "contract_instruction(perfil)" in executar_text:
            out.append("duplicated provider wire contract returned to Eyle prompt")
        structured_text = (base / "llm/structured.py").read_text(encoding="utf-8")
        if "def contract_instruction(" in structured_text:
            out.append("dead textual wire contract returned")
        if "_attach_schema_instruction(messages, schema)" not in server_text:
            out.append("caller JSON Schema is not delivered to provider")
        if '_repair_messages(schema, repair_candidate or "", repair_errors or [])' not in server_text:
            out.append("isolated schema/candidate/error repair missing")
        if '"adapter_output_truncated"' not in server_text or '_finish_reason(first.data) == "length"' not in server_text:
            out.append("provider truncation handling missing")

        if manifest.get("session_schema") != SESSION_SCHEMA_VERSION:
            out.append("manifest session_schema mismatch")
        if manifest.get("pending_schema") != PENDING_SCHEMA_VERSION:
            out.append("manifest pending_schema mismatch")
        if manifest.get("memory_graph_schema") != MEMORY_GRAPH_SCHEMA_VERSION:
            out.append("manifest memory_graph_schema mismatch")
        if manifest.get("adapter_protocol") != ADAPTER_TRANSPORT_PROTOCOL:
            out.append("manifest adapter protocol mismatch")
        if manifest.get("adapter_version") != "2.7.5-rev3.7.6":
            out.append("manifest adapter version mismatch")
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
    if pub.get("mainline_base") != "Rev3.7.8":
        violations.append("Rev4.0.0 mainline base mismatch")
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
