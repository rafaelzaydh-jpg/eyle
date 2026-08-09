"""Read-only Git inspection helpers for Eyle.

These helpers expose observable repository state without mutating Git. They
return compact structured data suitable for the LLM while leaving user changes
untouched.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional

from .workspace_policy import _caminho_parece_segredo, _conteudo_parece_segredo


_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def _run_git(root: str, args: List[str], timeout: int = 20) -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "-C", root, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(timeout)),
            check=False,
            shell=False,
        )
    except FileNotFoundError:
        return {"ok": False, "error_code": "GIT_NOT_AVAILABLE", "detail": "git não está disponível no ambiente."}
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {"ok": False, "error_code": "GIT_EXECUTION_FAILED", "detail": str(error)}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
    }


def _ensure_repo(root: str) -> Optional[Dict[str, Any]]:
    result = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if not result.get("ok") or result.get("stdout", "").strip().lower() != "true":
        detail = (result.get("stderr") or result.get("detail") or "workspace não é um repositório Git").strip()
        return {"ok": False, "error_code": result.get("error_code") or "GIT_REPOSITORY_NOT_FOUND", "detail": detail[:500]}
    return None


def git_status(root: str, max_entries: int = 200) -> Dict[str, Any]:
    error = _ensure_repo(root)
    if error:
        return error

    branch_result = _run_git(root, ["branch", "--show-current"])
    branch = branch_result.get("stdout", "").strip() if branch_result.get("ok") else ""
    result = _run_git(root, ["status", "--porcelain=v1", "--untracked-files=all", "--", "."])
    if not result.get("ok"):
        return {
            "ok": False,
            "error_code": result.get("error_code") or "GIT_STATUS_FAILED",
            "detail": (result.get("stderr") or result.get("detail") or "git status falhou")[:500],
        }

    entries: List[Dict[str, str]] = []
    counts = {"modified": 0, "added": 0, "deleted": 0, "renamed": 0, "untracked": 0, "conflicted": 0, "other": 0}
    lines = [line for line in result.get("stdout", "").splitlines() if line]
    for raw in lines[: max(1, int(max_entries))]:
        code = raw[:2]
        path = raw[3:] if len(raw) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if code == "??":
            category = "untracked"
        elif "U" in code or code in {"AA", "DD"}:
            category = "conflicted"
        elif "R" in code:
            category = "renamed"
        elif "D" in code:
            category = "deleted"
        elif "A" in code:
            category = "added"
        elif "M" in code:
            category = "modified"
        else:
            category = "other"
        normalized = path.replace("\\", "/")
        if _caminho_parece_segredo(normalized):
            continue
        counts[category] += 1
        entries.append({"path": normalized, "status": code, "category": category})

    return {
        "ok": True,
        "branch": branch or None,
        "clean": not lines,
        "changed_count": len(lines),
        "returned_count": len(entries),
        "truncated": len(lines) > len(entries),
        "counts": counts,
        "entries": entries,
    }


def _parse_numstat(output: str) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        add_raw, remove_raw, path = parts
        added = int(add_raw) if add_raw.isdigit() else None
        removed = int(remove_raw) if remove_raw.isdigit() else None
        files.append({"path": path.replace("\\", "/"), "added": added, "removed": removed})
    return files


def git_diff(
    root: str,
    *,
    path: Optional[str] = None,
    staged: bool = False,
    context_lines: int = 3,
    max_chars: int = 6000,
) -> Dict[str, Any]:
    error = _ensure_repo(root)
    if error:
        return error

    normalized_path = None
    if path:
        normalized_path = str(path).strip().replace("\\", "/")
        if os.path.isabs(normalized_path) or normalized_path.startswith("../") or "/../" in f"/{normalized_path}/":
            return {"ok": False, "error_code": "UNSAFE_PATH", "detail": "git_diff aceita somente caminho relativo seguro."}
        if _caminho_parece_segredo(normalized_path):
            return {"ok": False, "error_code": "SECRET_PATH_BLOCKED", "detail": "git_diff bloqueou caminho protegido por segredo."}

    context_lines = max(0, min(10, int(context_lines)))
    max_chars = max(1000, min(12000, int(max_chars)))
    base = ["diff", "--no-ext-diff"]
    if staged:
        base.append("--cached")

    numstat_args = [*base, "--numstat"]
    diff_args = [*base, f"--unified={context_lines}"]
    if normalized_path:
        numstat_args += ["--", normalized_path]
        diff_args += ["--", normalized_path]

    numstat = _run_git(root, numstat_args)
    if not numstat.get("ok"):
        return {
            "ok": False,
            "error_code": numstat.get("error_code") or "GIT_DIFF_FAILED",
            "detail": (numstat.get("stderr") or numstat.get("detail") or "git diff falhou")[:500],
        }
    diff = _run_git(root, diff_args)
    if not diff.get("ok"):
        return {
            "ok": False,
            "error_code": diff.get("error_code") or "GIT_DIFF_FAILED",
            "detail": (diff.get("stderr") or diff.get("detail") or "git diff falhou")[:500],
        }

    files = _parse_numstat(numstat.get("stdout", ""))
    raw = diff.get("stdout", "")
    if any(_caminho_parece_segredo(str(item.get("path") or "")) for item in files):
        return {"ok": False, "error_code": "SECRET_PATH_BLOCKED", "detail": "git_diff bloqueou diff que contém caminho protegido por segredo."}
    if _conteudo_parece_segredo(raw):
        return {"ok": False, "error_code": "SECRET_CONTENT_BLOCKED", "detail": "git_diff bloqueou conteúdo que corresponde à política de segredos."}
    clipped = raw[:max_chars]
    total_added = sum(item["added"] or 0 for item in files)
    total_removed = sum(item["removed"] or 0 for item in files)
    return {
        "ok": True,
        "staged": bool(staged),
        "path": normalized_path,
        "files": files,
        "file_count": len(files),
        "added_lines": total_added,
        "removed_lines": total_removed,
        "diff": clipped,
        "truncated": len(raw) > len(clipped),
        "diff_characters": len(raw),
        "context_lines": context_lines,
    }
