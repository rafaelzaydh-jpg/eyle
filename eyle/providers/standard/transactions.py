"""Transactional multi-file patch dry-run, apply and rollback."""
from __future__ import annotations

import ast
import copy
import os
from typing import Any, Dict, List

from .security import _resolver_caminho_seguro
from .text_hash import hash_texto, hash_faixa
from .editing import _escrever_arquivo_atomico, _substituir_linhas
from .workspace_policy import build_protected_resource_index, is_protected_workspace_resource


def _normalize_patch(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Validate one canonical transaction patch shape."""
    if not isinstance(raw, dict):
        raise ValueError("each patch must be an object")
    operation = str(raw.get("operation") or "").strip().lower()
    if operation not in {"update", "replace", "create", "delete"}:
        raise ValueError(f"unsupported patch operation: {operation or 'missing'}")
    path = raw.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("patch path is required")
    patch = {"operation": operation, "path": path.strip().replace("\\", "/")}
    if operation == "update":
        required = ("line_start", "line_end", "new_code", "file_hash_expected", "range_hash_expected")
        missing = [key for key in required if key not in raw]
        if missing:
            raise ValueError("update patch missing: " + ", ".join(missing))
        patch.update({
            "line_start": int(raw["line_start"]),
            "line_end": int(raw["line_end"]),
            "new_code": str(raw["new_code"]),
            "file_hash_expected": str(raw["file_hash_expected"]),
            "range_hash_expected": str(raw["range_hash_expected"]),
        })
    elif operation in {"replace", "create"}:
        if "content" not in raw or not isinstance(raw.get("content"), str):
            raise ValueError(f"{operation} patch requires string content")
        patch["content"] = raw["content"]
        if operation == "replace":
            if "file_hash_expected" not in raw:
                raise ValueError("replace patch requires file_hash_expected")
            patch["file_hash_expected"] = str(raw["file_hash_expected"])
    else:
        if "file_hash_expected" not in raw:
            raise ValueError("delete patch requires file_hash_expected")
        patch["file_hash_expected"] = str(raw["file_hash_expected"])
    allowed = set(patch)
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError("unknown patch field(s): " + ", ".join(unknown))
    return patch


def _validate_python(path: str, content: str) -> None:
    if path.lower().endswith(".py"):
        ast.parse(content, filename=path)


def _prune_empty_parents(directory: str, project_root: str) -> List[str]:
    """Remove empty directories created only to hold deleted transaction files."""
    root = os.path.realpath(project_root)
    current = os.path.realpath(directory)
    removed: List[str] = []
    while current != root:
        try:
            if os.path.commonpath([root, current]) != root:
                break
        except ValueError:
            break
        try:
            os.rmdir(current)
        except OSError:
            break
        removed.append(os.path.relpath(current, root).replace("\\", "/"))
        current = os.path.dirname(current)
    return removed


def dry_run_patch_set(project_root: str, raw_patches: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(raw_patches, list) or not raw_patches:
        return {"ok": False, "error_code": "INVALID_ARGUMENT", "message": "patches must be a non-empty list"}
    prepared: List[Dict[str, Any]] = []
    seen = set()
    protected_index = build_protected_resource_index(project_root)
    try:
        for raw in raw_patches:
            patch = _normalize_patch(raw)
            path = patch["path"]
            if path in seen:
                raise ValueError(f"multiple patches for the same file are not supported in one transaction: {path}")
            seen.add(path)
            absolute = _resolver_caminho_seguro(project_root, path)
            if absolute is None:
                return {"ok": False, "error_code": "UNSAFE_PATH", "message": f"unsafe path: {path}"}
            operation = patch["operation"]
            exists = os.path.isfile(absolute)
            if operation != "create" and is_protected_workspace_resource(project_root, path, index=protected_index):
                return {
                    "ok": False, "error_code": "PROTECTED_RESOURCE_READ_BLOCKED",
                    "message": f"content access is restricted for protected resource: {path}",
                }
            if operation == "create":
                if exists:
                    return {"ok": False, "error_code": "FILE_ALREADY_EXISTS", "message": f"file already exists: {path}"}
                content = patch["content"]
                _validate_python(path, content)
                prepared.append({**patch, "absolute": absolute, "original_content": None, "result_content": content})
                continue
            if not exists:
                return {"ok": False, "error_code": "FILE_NOT_FOUND", "message": f"file not found: {path}"}
            with open(absolute, "r", encoding="utf-8", errors="replace") as handle:
                original = handle.read()
            current_hash = hash_texto(original)
            expected_file = patch.get("file_hash_expected")
            if expected_file and current_hash != expected_file:
                return {"ok": False, "error_code": "STALE_PATCH", "message": f"file changed since observation: {path}"}
            if operation == "delete":
                prepared.append({**patch, "absolute": absolute, "original_content": original, "result_content": None, "file_hash_expected": current_hash})
                continue
            if operation == "replace":
                content = patch["content"]
                _validate_python(path, content)
                prepared.append({
                    **patch, "absolute": absolute, "original_content": original,
                    "result_content": content, "file_hash_expected": current_hash,
                })
                continue
            start, end = patch["line_start"], patch["line_end"]
            if start < 1 or end < start:
                return {"ok": False, "error_code": "INVALID_RANGE", "message": f"invalid range for {path}"}
            current_range = hash_faixa(original, start, end)
            if patch.get("range_hash_expected") and current_range != patch["range_hash_expected"]:
                return {"ok": False, "error_code": "STALE_PATCH", "message": f"range changed since observation: {path}:{start}-{end}"}
            result = _substituir_linhas(original, start, end, patch["new_code"])
            if result is None:
                return {"ok": False, "error_code": "INVALID_RANGE", "message": f"invalid range for {path}:{start}-{end}"}
            _validate_python(path, result)
            prepared.append({
                **patch, "absolute": absolute, "original_content": original,
                "result_content": result, "file_hash_expected": current_hash,
                "range_hash_expected": current_range,
            })
    except (ValueError, TypeError, SyntaxError) as error:
        return {"ok": False, "error_code": "DRY_RUN_FAILED", "message": str(error)}
    for item in prepared:
        item["project_root"] = os.path.realpath(project_root)
    return {
        "ok": True,
        "message": f"dry-run approved for {len(prepared)} file(s)",
        "prepared_patches": prepared,
        "files": [item["path"] for item in prepared],
    }


def apply_patch_set(project_root: str, confirmed_patches: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply one canonical confirmed transaction after revalidating freshness."""
    check = dry_run_patch_set(project_root, confirmed_patches)
    if not check.get("ok"):
        return check
    prepared = check["prepared_patches"]
    applied: List[Dict[str, Any]] = []
    try:
        for patch in prepared:
            absolute = patch["absolute"]
            operation = patch["operation"]
            os.makedirs(os.path.dirname(absolute), exist_ok=True)
            if operation == "delete":
                os.remove(absolute)
                patch["pruned_directories"] = _prune_empty_parents(
                    os.path.dirname(absolute), project_root,
                )
            else:
                _escrever_arquivo_atomico(absolute, patch["result_content"])
            applied.append(patch)
    except Exception as error:
        rollback = rollback_patch_set(applied)
        return {
            "ok": False,
            "error_code": "PATCH_TRANSACTION_FAILED" if rollback.get("ok") else "PATCH_TRANSACTION_ROLLBACK_FAILED",
            "message": str(error),
            "rollback_confirmed": bool(rollback.get("ok")),
            "rollback": rollback,
            "applied_before_failure": [str(item.get("path") or "") for item in applied],
        }
    return {
        "ok": True,
        "message": f"transaction applied to {len(applied)} file(s)",
        "applied_patches": applied,
        "files": [item["path"] for item in applied],
    }


def rollback_patch_set(applied_patches: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Restore every member and verify that the transaction was fully undone."""
    failures = []
    restored = []
    for patch in reversed(list(applied_patches or [])):
        path = str(patch.get("path") or "")
        try:
            absolute = patch["absolute"]
            original = patch.get("original_content")
            if original is None:
                if os.path.exists(absolute):
                    os.remove(absolute)
                if os.path.exists(absolute):
                    raise OSError("arquivo criado continua presente apos rollback")
                project_root = patch.get("project_root")
                if project_root:
                    _prune_empty_parents(os.path.dirname(absolute), project_root)
            else:
                os.makedirs(os.path.dirname(absolute), exist_ok=True)
                _escrever_arquivo_atomico(absolute, original)
                with open(absolute, "r", encoding="utf-8", errors="replace") as handle:
                    restored_content = handle.read()
                if hash_texto(restored_content) != hash_texto(original):
                    raise OSError("conteudo restaurado diverge do snapshot original")
            restored.append(path)
        except Exception as error:
            failures.append(f"{path}: {error}")
    return {"ok": not failures, "failures": failures, "restored": restored}


# Canonical write-transaction state lives with the transaction mechanics.


