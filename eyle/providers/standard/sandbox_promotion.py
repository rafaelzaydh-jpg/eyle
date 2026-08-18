"""Promote a tested isolated sandbox snapshot into the real workspace.

The Main selects a sandbox subtree, target workspace subtree and merge/mirror
policy. Runtime snapshots that exact sandbox state to a durable inert ZIP,
computes a byte-level manifest/freshness precondition, asks once for user
confirmation, then applies exactly the staged bytes transactionally.

No Eyle Core/runtime source is writable through this capability: workspace
protected-resource policy is rechecked both at prepare and confirm time.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable

from eyle.contracts.capability import failure, physical_effect, result
from eyle.providers.standard.sandbox import ErroSandbox, export_active_sandbox_zip
from eyle.providers.standard.common import _standard_context


class SandboxPromotionRollbackError(RuntimeError):
    pass
from eyle.providers.standard.workspace_policy import (
    build_protected_resource_index, is_protected_workspace_resource,
)

_IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_STAGING_TTL_SECONDS = 2 * 60 * 60


def schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "sandbox_path": {"type": "string", "minLength": 1, "description": "Directory inside the active sandbox snapshot; default '.'."},
            "workspace_path": {"type": "string", "minLength": 1, "description": "Target directory inside the real workspace; default '.'."},
            "mode": {"type": "string", "enum": ["merge", "mirror"], "description": "merge copies/replaces staged files; mirror also removes target files absent from the staged subtree."},
        },
        "additionalProperties": False,
    }



def _safe_rel(value: Any) -> str:
    raw = str(value or ".").replace("\\", "/").strip() or "."
    if raw.startswith("/") or raw == ".." or raw.startswith("../") or "/../" in f"/{raw}/" or "\x00" in raw:
        raise ValueError("path must stay inside its declared root")
    norm = os.path.normpath(raw).replace("\\", "/")
    if norm in {"", "."}:
        return "."
    if norm == ".." or norm.startswith("../"):
        raise ValueError("path must stay inside its declared root")
    return norm


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _stage_dir(eyle_root: str) -> str:
    path = os.path.join(os.path.realpath(eyle_root), "context", "sandbox_promotions")
    os.makedirs(path, exist_ok=True)
    return path


def _gc_staging(path: str) -> None:
    cutoff = time.time() - _STAGING_TTL_SECONDS
    try:
        names = os.listdir(path)
    except OSError:
        return
    for name in names:
        if not name.startswith("promotion-"):
            continue
        item = os.path.join(path, name)
        try:
            if os.path.isfile(item) and os.path.getmtime(item) < cutoff:
                os.unlink(item)
        except OSError:
            pass


def _zip_members(archive: str, sandbox_path: str) -> tuple[Dict[str, Dict[str, Any]], bool]:
    base = "snapshot/"
    exact = base + ("" if sandbox_path == "." else sandbox_path)
    with zipfile.ZipFile(archive, "r") as zf:
        names = {info.filename.replace("\\", "/"): info for info in zf.infolist() if not info.is_dir()}
        # Single-file promotion: workspace_path denotes the destination file.
        if sandbox_path != "." and exact in names:
            data = zf.read(names[exact])
            return {os.path.basename(sandbox_path): {"sha256": _sha256_bytes(data), "bytes": len(data), "zip_member": exact}}, True
        prefix = base if sandbox_path == "." else exact.rstrip("/") + "/"
        files: Dict[str, Dict[str, Any]] = {}
        for name, info in names.items():
            if not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if not rel or rel.startswith("../") or "/../" in f"/{rel}/":
                continue
            parts = rel.split("/")
            if any(part in _IGNORED_DIRS for part in parts):
                continue
            data = zf.read(info)
            files[rel] = {"sha256": _sha256_bytes(data), "bytes": len(data), "zip_member": name}
    if not files:
        raise ValueError("selected sandbox path contains no promotable regular files")
    return files, False


def _iter_workspace_files(root: str, target_rel: str) -> Iterable[tuple[str, str]]:
    target = root if target_rel == "." else os.path.join(root, *target_rel.split("/"))
    if not os.path.isdir(target):
        return []
    out = []
    for current, dirs, files in os.walk(target, followlinks=False):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS and not os.path.islink(os.path.join(current, d))]
        for name in files:
            absolute = os.path.join(current, name)
            if os.path.islink(absolute) or not os.path.isfile(absolute):
                continue
            rel = os.path.relpath(absolute, target).replace(os.sep, "/")
            out.append((rel, absolute))
    return out


def _dest_rel(workspace_path: str, rel: str, *, single_file: bool = False) -> str:
    if single_file:
        return rel if workspace_path == "." else workspace_path
    return rel if workspace_path == "." else f"{workspace_path.rstrip('/')}/{rel}"


def _scan_plan(workspace_root: str, workspace_path: str, staged: Dict[str, Dict[str, Any]], mode: str, *, single_file: bool = False) -> Dict[str, Any]:
    protected = build_protected_resource_index(workspace_root)
    target_files = {} if single_file else {rel: abs_path for rel, abs_path in _iter_workspace_files(workspace_root, workspace_path)}
    creates, replaces, unchanged, deletes = [], [], [], []
    expected: Dict[str, str | None] = {}

    for rel, meta in sorted(staged.items()):
        dest_rel = _dest_rel(workspace_path, rel, single_file=single_file)
        if is_protected_workspace_resource(workspace_root, dest_rel, index=protected):
            raise ValueError(f"protected workspace resource cannot be promoted: {dest_rel}")
        existing = (os.path.join(workspace_root, *dest_rel.split("/")) if single_file and os.path.isfile(os.path.join(workspace_root, *dest_rel.split("/"))) else target_files.get(rel))
        if existing is None:
            creates.append(dest_rel)
            expected[dest_rel] = None
            continue
        current = _sha256_file(existing)
        expected[dest_rel] = current
        if current == meta["sha256"]:
            unchanged.append(dest_rel)
        else:
            replaces.append(dest_rel)

    if mode == "mirror" and not single_file:
        for rel, absolute in sorted(target_files.items()):
            if rel in staged:
                continue
            dest_rel = _dest_rel(workspace_path, rel)
            if is_protected_workspace_resource(workspace_root, dest_rel, index=protected):
                continue
            deletes.append(dest_rel)
            expected[dest_rel] = _sha256_file(absolute)

    return {
        "creates": creates, "replaces": replaces, "unchanged": unchanged, "deletes": deletes,
        "expected_workspace_hashes": expected,
    }


def _write_stage_manifest(path: str, payload: Dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".eyle-promotion-manifest-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if fd is not None:
            try: os.close(fd)
            except OSError: pass
        try:
            if os.path.exists(temp): os.unlink(temp)
        except OSError:
            pass
    return _sha256_bytes(data)


def _stage_path(eyle_root: str, rel: str) -> str:
    staging_root = os.path.realpath(os.path.join(eyle_root, "context", "sandbox_promotions"))
    candidate = os.path.realpath(os.path.join(eyle_root, *str(rel or "").split("/")))
    if os.path.dirname(candidate) != staging_root:
        raise ValueError("staging reference is outside sandbox_promotions")
    return candidate


def prepare(arguments: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    project = _standard_context(ctx)
    workspace_root = project.get("caminho_origem")
    eyle_root = project.get("eyle_root")
    if not workspace_root or not os.path.isdir(workspace_root) or not eyle_root or not os.path.isdir(eyle_root):
        return {"ok": False, "error": failure("SANDBOX_PROMOTION_UNAVAILABLE", "workspace/Eyle root unavailable")}
    archive = ""
    try:
        sandbox_path = _safe_rel(arguments.get("sandbox_path") or ".")
        workspace_path = _safe_rel(arguments.get("workspace_path") or ".")
        mode = str(arguments.get("mode") or "merge").strip().lower()
        if mode not in {"merge", "mirror"}:
            raise ValueError("mode must be merge or mirror")
        staging_dir = _stage_dir(eyle_root)
        _gc_staging(staging_dir)
        filename = f"promotion-{uuid.uuid4().hex}.zip"
        exported = export_active_sandbox_zip(staging_dir, filename, archive_root="snapshot", timeout_seconds=300)
        archive = os.path.join(staging_dir, filename)
        staged, single_file = _zip_members(archive, sandbox_path)
        plan = _scan_plan(workspace_root, workspace_path, staged, mode, single_file=single_file)
        archive_sha = _sha256_file(archive)
    except (ErroSandbox, ValueError, OSError, zipfile.BadZipFile) as exc:
        if archive:
            try: os.unlink(archive)
            except OSError: pass
        return {"ok": False, "error": failure("SANDBOX_PROMOTION_PREPARE_FAILED", str(exc), executed=False)}

    changed = len(plan["creates"]) + len(plan["replaces"]) + len(plan["deletes"])
    if changed == 0:
        try: os.unlink(archive)
        except OSError: pass
        return {"ok": False, "error": failure("SANDBOX_PROMOTION_NO_CHANGES", "sandbox snapshot already matches the target workspace", executed=True)}

    preview = [*plan["creates"], *plan["replaces"], *plan["deletes"]][:8]
    archive_rel = os.path.relpath(archive, os.path.realpath(eyle_root)).replace(os.sep, "/")
    stage_manifest_name = filename[:-4] + ".json"
    stage_manifest_path = os.path.join(staging_dir, stage_manifest_name)
    full_stage = {
        "stage_schema": "sandbox-promotion-v1",
        "archive": archive_rel,
        "archive_sha256": archive_sha,
        "sandbox_path": sandbox_path,
        "workspace_path": workspace_path,
        "mode": mode,
        "single_file": bool(single_file),
        "staged_files": staged,
        "expected_workspace_hashes": plan["expected_workspace_hashes"],
        "creates": plan["creates"], "replaces": plan["replaces"], "deletes": plan["deletes"],
    }
    try:
        stage_manifest_sha = _write_stage_manifest(stage_manifest_path, full_stage)
    except OSError as exc:
        try: os.unlink(archive)
        except OSError: pass
        return {"ok": False, "error": failure("SANDBOX_PROMOTION_PREPARE_FAILED", str(exc), executed=False)}
    state = {
        "stage_manifest": os.path.relpath(stage_manifest_path, os.path.realpath(eyle_root)).replace(os.sep, "/"),
        "stage_manifest_sha256": stage_manifest_sha,
        "sandbox_path": sandbox_path,
        "workspace_path": workspace_path,
        "mode": mode,
        "kind": "file" if single_file else "project",
        "summary": {
            "files": len(staged), "creates": len(plan["creates"]), "replaces": len(plan["replaces"]),
            "deletes": len(plan["deletes"]), "unchanged": len(plan["unchanged"]), "preview": preview,
        },
    }
    subject = "arquivo" if single_file else "projeto"
    question = (
        f"Promover o {subject} validado do sandbox para o workspace? "
        f"{len(plan['creates'])} novo(s), {len(plan['replaces'])} substituído(s), "
        f"{len(plan['deletes'])} removido(s), {len(plan['unchanged'])} já idêntico(s). "
        f"Modo: {mode}. " + ("Exemplos: " + ", ".join(preview) if preview else "")
    )
    return {"ok": True, "question": question, "state": state}


def _atomic_write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".eyle-promote-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if fd is not None:
            try: os.close(fd)
            except OSError: pass
        try:
            if os.path.exists(temp): os.unlink(temp)
        except OSError:
            pass


def cancel(state: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Delete private staged artifacts when the user rejects promotion."""
    project = _standard_context(ctx)
    eyle_root = os.path.realpath(str(project.get("eyle_root") or ""))
    if not os.path.isdir(eyle_root) or not isinstance(state, dict):
        return {"ok": True, "cleanup": "nothing_to_remove"}
    manifest_path = ""
    archive_path = ""
    try:
        manifest_path = _stage_path(eyle_root, str(state.get("stage_manifest") or ""))
        if os.path.isfile(manifest_path):
            try:
                manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
                if isinstance(manifest, dict):
                    archive_path = _stage_path(eyle_root, str(manifest.get("archive") or ""))
            except (ValueError, OSError, json.JSONDecodeError):
                archive_path = ""
    except ValueError:
        manifest_path = ""
    removed = []
    for staged_path in (archive_path, manifest_path):
        if staged_path and os.path.isfile(staged_path):
            try:
                os.unlink(staged_path); removed.append(os.path.basename(staged_path))
            except OSError:
                pass
    return {"ok": True, "cleanup": "completed", "removed": removed}


def confirm(state: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    project = _standard_context(ctx)
    workspace_root = os.path.realpath(str(project.get("caminho_origem") or ""))
    eyle_root = os.path.realpath(str(project.get("eyle_root") or ""))
    if not os.path.isdir(workspace_root) or not os.path.isdir(eyle_root) or not isinstance(state, dict):
        return failure("SANDBOX_PROMOTION_STATE_INVALID", "promotion state/workspace unavailable")
    manifest_rel = str(state.get("stage_manifest") or "")
    manifest_path = ""
    archive = ""
    try:
        manifest_path = _stage_path(eyle_root, manifest_rel)
        if not os.path.isfile(manifest_path):
            return failure("SANDBOX_PROMOTION_STAGE_MISSING", "staged promotion manifest is unavailable")
        if _sha256_file(manifest_path) != str(state.get("stage_manifest_sha256") or ""):
            raise ValueError("staged promotion manifest changed after confirmation was prepared")
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("stage_schema") != "sandbox-promotion-v1":
            raise ValueError("staged promotion manifest schema invalid")
        for bound in ("sandbox_path", "workspace_path", "mode"):
            if str(manifest.get(bound) or "") != str(state.get(bound) or ""):
                raise ValueError(f"staged promotion binding changed: {bound}")
        archive = _stage_path(eyle_root, str(manifest.get("archive") or ""))
        if not os.path.isfile(archive):
            raise ValueError("staged sandbox snapshot is unavailable")
        if _sha256_file(archive) != manifest.get("archive_sha256"):
            raise ValueError("staged archive changed after confirmation was prepared")
        staged = manifest.get("staged_files") or {}
        expected = manifest.get("expected_workspace_hashes") or {}
        protected = build_protected_resource_index(workspace_root)
        # Freshness: the user confirms a specific merge against a specific workspace state.
        for dest_rel, expected_hash in expected.items():
            if is_protected_workspace_resource(workspace_root, dest_rel, index=protected):
                raise ValueError(f"protected workspace resource: {dest_rel}")
            path = os.path.realpath(os.path.join(workspace_root, *str(dest_rel).split("/")))
            if path != workspace_root and not path.startswith(workspace_root + os.sep):
                raise ValueError(f"unsafe destination: {dest_rel}")
            current = _sha256_file(path) if os.path.isfile(path) else None
            if current != expected_hash:
                raise ValueError(f"workspace changed since promotion was prepared: {dest_rel}")

        backup_dir = tempfile.mkdtemp(prefix="eyle-promote-backup-")
        created: list[str] = []
        backed_up: Dict[str, str] = {}
        touched: list[str] = []
        try:
            # Backup all existing paths that may be replaced/deleted.
            for dest_rel in [*manifest.get("replaces", []), *manifest.get("deletes", [])]:
                src = os.path.join(workspace_root, *dest_rel.split("/"))
                if os.path.isfile(src):
                    backup = os.path.join(backup_dir, *dest_rel.split("/"))
                    os.makedirs(os.path.dirname(backup), exist_ok=True)
                    shutil.copy2(src, backup)
                    backed_up[dest_rel] = backup
            with zipfile.ZipFile(archive, "r") as zf:
                for rel, meta in sorted(staged.items()):
                    dest_rel = _dest_rel(str(manifest.get("workspace_path") or "."), rel, single_file=bool(manifest.get("single_file")))
                    if dest_rel not in manifest.get("creates", []) and dest_rel not in manifest.get("replaces", []):
                        continue
                    data = zf.read(str(meta.get("zip_member") or ""))
                    if _sha256_bytes(data) != meta.get("sha256"):
                        raise ValueError(f"staged member hash mismatch: {rel}")
                    dest = os.path.join(workspace_root, *dest_rel.split("/"))
                    if not os.path.exists(dest): created.append(dest_rel)
                    _atomic_write_bytes(dest, data)
                    touched.append(dest_rel)
                for dest_rel in manifest.get("deletes", []):
                    dest = os.path.join(workspace_root, *dest_rel.split("/"))
                    if os.path.isfile(dest): os.remove(dest)
                    touched.append(dest_rel)

            # Exact post-promotion byte verification.
            for rel, meta in staged.items():
                dest_rel = _dest_rel(str(manifest.get("workspace_path") or "."), rel, single_file=bool(manifest.get("single_file")))
                dest = os.path.join(workspace_root, *dest_rel.split("/"))
                if not os.path.isfile(dest) or _sha256_file(dest) != meta.get("sha256"):
                    raise ValueError(f"post-promotion verification failed: {dest_rel}")
            for dest_rel in manifest.get("deletes", []):
                if os.path.exists(os.path.join(workspace_root, *dest_rel.split("/"))):
                    raise ValueError(f"post-promotion delete verification failed: {dest_rel}")
        except Exception as original_exc:
            # Restore exact pre-confirmation workspace state. A failed rollback is
            # materially different from a failed promotion and must be surfaced.
            rollback_errors: list[str] = []
            for dest_rel in created:
                path = os.path.join(workspace_root, *dest_rel.split("/"))
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError as exc:
                    rollback_errors.append(f"remove {dest_rel}: {exc}")
            for dest_rel, backup in backed_up.items():
                try:
                    _atomic_write_bytes(os.path.join(workspace_root, *dest_rel.split("/")), Path(backup).read_bytes())
                except OSError as exc:
                    rollback_errors.append(f"restore {dest_rel}: {exc}")
            if rollback_errors:
                raise SandboxPromotionRollbackError(
                    f"promotion failed ({type(original_exc).__name__}); rollback incomplete: " + "; ".join(rollback_errors[:8])
                ) from original_exc
            raise
        finally:
            shutil.rmtree(backup_dir, ignore_errors=True)
    except SandboxPromotionRollbackError as exc:
        return failure("SANDBOX_PROMOTION_ROLLBACK_FAILED", str(exc), executed=True, retryable=False)
    except (ValueError, OSError, zipfile.BadZipFile, KeyError) as exc:
        return failure("SANDBOX_PROMOTION_FAILED", str(exc), executed=True, retryable=False)
    finally:
        for staged_path in (archive, manifest_path):
            if not staged_path:
                continue
            try: os.unlink(staged_path)
            except OSError: pass

    return result(
        "success", True, True, changed=True,
        detail={
            "files": touched,
            "created": len(manifest.get("creates", [])),
            "replaced": len(manifest.get("replaces", [])),
            "deleted": len(manifest.get("deletes", [])),
            "verification_state": "promoted_exact",
            "sandbox_path": state.get("sandbox_path"),
            "workspace_path": state.get("workspace_path"),
            "mode": state.get("mode"),
        },
        physical_effect_value=physical_effect("workspace", "sandbox_promotion", "persistent", changed=True),
    )
