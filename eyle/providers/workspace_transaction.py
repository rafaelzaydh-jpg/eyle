"""Transactional workspace mutation capability.

All code/file-specific write policy lives here, outside Eyle Core.  The generic
capability Runtime only knows that this capability requires confirmation.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from eyle.contracts.capability import failure, physical_effect, result
from eyle.runtime.observation import freshest_material_for_locator
from eyle.providers.standard_impl.post_write import expected_outputs_from_patches, run_compileall_for_changes, verify_expected_outputs
from eyle.providers.standard_impl.security import _resolver_caminho_seguro
from eyle.providers.standard_impl.text_hash import hash_faixa
from eyle.providers.standard_impl.transactions import apply_patch_set, dry_run_patch_set, rollback_patch_set


def schema() -> Dict[str, Any]:
    patch = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["replace", "create", "delete", "update"]},
            "path": {"type": "string", "minLength": 1},
            "content": {"type": "string"},
            "line_start": {"type": "integer", "minimum": 1},
            "line_end": {"type": "integer", "minimum": 1},
            "new_code": {"type": "string"},
        },
        "required": ["operation", "path"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"patches": {"type": "array", "minItems": 1, "items": patch}},
        "required": ["patches"],
        "additionalProperties": False,
    }


def _standard_context(ctx):
    provider_context = (ctx or {}).get("provider_context") or {}
    value = provider_context.get("standard") or {} if isinstance(provider_context, dict) else {}
    return value if isinstance(value, dict) else {}


def _standard_tests_config(config):
    providers = (config or {}).get("providers") or {}
    standard = providers.get("standard") or {} if isinstance(providers, dict) else {}
    tests = standard.get("tests") or {} if isinstance(standard, dict) else {}
    return tests if isinstance(tests, dict) else {}


def _enrich(arguments: Dict[str, Any], ctx: Dict[str, Any]) -> Tuple[Dict[str, Any] | None, str | None]:
    session = (ctx or {}).get("session")
    project = _standard_context(ctx)
    root = project.get("caminho_origem")
    if session is None or not root:
        return None, "workspace transaction requires an active session and workspace"
    raw_patches = arguments.get("patches")
    if not isinstance(raw_patches, list) or not raw_patches:
        return None, "patches must be a non-empty list"
    enriched: List[Dict[str, Any]] = []
    for raw in raw_patches:
        if not isinstance(raw, dict):
            return None, "each patch must be an object"
        patch = dict(raw)
        path = str(patch.get("path") or "").strip().replace("\\", "/")
        if not path:
            return None, "each patch needs a path"
        absolute = _resolver_caminho_seguro(root, path)
        if absolute is None:
            return None, f"unsafe patch path: {path}"
        operation = str(patch.get("operation") or "").strip().lower()
        if operation not in {"replace", "create", "delete", "update"}:
            return None, f"invalid patch operation: {operation}"
        patch["path"] = path
        patch["operation"] = operation
        exists = os.path.isfile(absolute)
        material = freshest_material_for_locator(
            session.observation_ledger, {"kind": "file", "source": "workspace", "path": path},
            match_fields=("kind", "source", "path"),
        )
        locator = dict(material.get("locator") or {}) if isinstance(material, dict) else {}
        if operation in {"replace", "create"} and not isinstance(patch.get("content"), str):
            return None, f"{operation} needs string content: {path}"
        if operation == "create":
            if exists:
                return None, f"create conflicts with existing file: {path}"
        else:
            if not exists:
                return None, f"{operation} requires an existing file: {path}"
            if not material or locator.get("kind") != "file" or not material.get("source_version"):
                return None, f"observe the existing file before {operation}: {path}"
            patch["file_hash_expected"] = material["source_version"]
        if operation == "replace":
            whole = int(locator.get("line_start") or 0) == 1 and int(locator.get("line_end") or 0) == int(locator.get("total_lines") or -1)
            if not whole:
                return None, f"replace requires a fresh whole-file observation: {path}"
        if operation == "update":
            if not isinstance(patch.get("new_code"), str):
                return None, f"update needs string new_code: {path}"
            try:
                start, end = int(patch.get("line_start")), int(patch.get("line_end"))
            except (TypeError, ValueError):
                return None, f"update needs line_start and line_end: {path}"
            if start < 1 or end < start:
                return None, f"invalid update range: {path}:{start}-{end}"
            patch["line_start"], patch["line_end"] = start, end
            if int(locator.get("line_start") or 0) == start and int(locator.get("line_end") or 0) == end:
                patch["range_hash_expected"] = material.get("content_hash")
            elif isinstance(material.get("content"), str) and int(locator.get("line_start") or 0) == 1 and int(locator.get("line_end") or 0) == int(locator.get("total_lines") or -1):
                patch["range_hash_expected"] = hash_faixa(material["content"], start, end)
            if not patch.get("range_hash_expected"):
                return None, f"observe the exact range before update: {path}:{start}-{end}"
        allowed = {"operation", "path"}
        if operation == "replace": allowed |= {"content", "file_hash_expected"}
        elif operation == "create": allowed |= {"content"}
        elif operation == "delete": allowed |= {"file_hash_expected"}
        elif operation == "update": allowed |= {"line_start", "line_end", "new_code", "file_hash_expected", "range_hash_expected"}
        unknown = sorted(set(patch) - allowed)
        if unknown:
            return None, f"unknown patch fields for {path}: {', '.join(unknown)}"
        enriched.append(patch)
    return {"patches": enriched}, None


def _tx_result(raw: Dict[str, Any], *, changed: bool = False) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    ok = raw.get("ok") is True
    detail = {k: raw.get(k) for k in ("message", "prepared_patches", "applied_patches", "files") if raw.get(k) is not None}
    return result(
        "success" if ok else "failed", ok, True,
        changed=bool(ok and changed), error_code=None if ok else str(raw.get("error_code") or "WORKSPACE_TRANSACTION_FAILED"),
        detail=detail if ok else str(raw.get("message") or "workspace transaction failed"),
        physical_effect_value=physical_effect("workspace", "transaction", "persistent", changed=bool(ok and changed)),
    )


def prepare(arguments: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    enriched, error = _enrich(arguments, ctx)
    if error:
        return {"ok": False, "error": failure("WORKSPACE_TRANSACTION_INVALID", error)}
    root = (_standard_context(ctx)).get("caminho_origem")
    dry = dry_run_patch_set(root, enriched["patches"])
    if dry.get("ok") is not True:
        return {"ok": False, "error": failure(str(dry.get("error_code") or "DRY_RUN_FAILED"), dry.get("message") or "dry-run failed", executed=True)}
    files = [str(p.get("path") or "") for p in enriched["patches"]]
    # Persist only the canonical patch intent. Dry-run snapshots may contain
    # full pre-edit source and are deliberately not serialized into pending UI/state.
    state = {
        "patches": copy.deepcopy(enriched["patches"]),
        "files": files,
    }
    question = (
        f"Proposta transacional pronta para confirmação: {len(files)} arquivo(s): {', '.join(files)}. "
        "Dry-run aprovado para o conjunto completo. A aplicação exige confirmação do usuário."
    )
    return {"ok": True, "question": question, "state": state}


def _run_tests(ctx: Dict[str, Any]) -> Dict[str, Any]:
    config = (ctx or {}).get("config") or {}
    enabled = bool(_standard_tests_config(config).get("enabled", False))
    if not enabled:
        return {"status": "skipped", "ok": True, "executed": False, "error_code": "TESTS_DISABLED", "detail": "Execução de testes desativada explicitamente."}
    registry = (ctx or {}).get("registry")
    capability = "standard.run_tests"
    if registry is None or capability not in registry.names():
        return {"status": "skipped", "ok": True, "executed": False, "error_code": "TESTS_NOT_FOUND", "detail": "Capability de testes indisponível."}
    return registry.execute(capability, {"source": "workspace"}, ctx)


def confirm(state: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    root = (_standard_context(ctx)).get("caminho_origem")
    config = (ctx or {}).get("config") or {}
    patches = state.get("patches") if isinstance(state, dict) else None
    if not root or not isinstance(patches, list) or not patches:
        return failure("WORKSPACE_TRANSACTION_STATE_INVALID", "confirmed transaction state is invalid")
    raw = apply_patch_set(root, patches)
    applied = _tx_result(raw, changed=bool(raw.get("ok")))
    if applied.get("ok") is not True:
        return applied
    applied_patches = (applied.get("detail") or {}).get("applied_patches") or []
    paths = [str(p.get("path") or "") for p in applied_patches]
    timeout = int(_standard_tests_config(config).get("timeout_seconds", 60) or 60)
    compile_result = run_compileall_for_changes(root, paths, timeout_seconds=timeout)
    if compile_result.get("ok") is not True:
        rollback = rollback_patch_set(applied_patches)
        return failure(
            str(compile_result.get("error_code") or "COMPILEALL_FAILED"),
            {"stage": "compileall", "detail": compile_result.get("detail"), "rollback": rollback, "files": paths},
            executed=True, retryable=False,
        )
    tests = _run_tests(ctx)
    if tests.get("ok") is not True:
        rollback = rollback_patch_set(applied_patches)
        return failure(
            str(tests.get("error_code") or "TESTS_FAILED"),
            {"stage": "tests", "detail": tests.get("detail"), "rollback": rollback, "files": paths},
            executed=True, retryable=False,
        )
    reread = verify_expected_outputs(root, expected_outputs_from_patches(applied_patches))
    if reread.get("ok") is not True:
        rollback = rollback_patch_set(applied_patches)
        return failure(
            "POST_WRITE_READ_FAILED",
            {"stage": "reread", "detail": reread.get("detail"), "rollback": rollback, "files": paths},
            executed=True, retryable=False,
        )
    fully_verified = bool(tests.get("executed") and tests.get("ok") is True)
    limitations: List[str] = []
    if not tests.get("executed"):
        limitations.append(str(tests.get("detail") or "Tests were not executed."))
    detail = {
        "files": paths,
        "applied_patches": applied_patches,
        "compile": compile_result,
        "tests": tests,
        "reread": reread,
        "verification_state": "verified" if fully_verified else "applied_partial",
        "limitations": limitations,
    }
    return result(
        "success", True, True, changed=True, detail=detail,
        physical_effect_value=physical_effect("workspace", "transaction", "persistent", changed=True),
    )
