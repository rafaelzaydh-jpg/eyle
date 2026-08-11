"""External project memory used only when the agent explicitly asks for it.

The memory is not injected into every prompt. Facts are stored outside the
workspace and can be searched through tools. Evidence-backed entries preserve
file hashes so stale facts are filtered when they are read again.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List


MEMORY_SCHEMA_VERSION = "5.7.5"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_key(project_root: str) -> str:
    normalized = os.path.realpath(str(project_root or ""))
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:24]


def _memory_path(base_dir: str, project_root: str) -> str:
    directory = os.path.join(base_dir, "agent_memory")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, f"{_project_key(project_root)}.json")


def _empty_memory(project_root: str = "") -> Dict[str, Any]:
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "project_root": os.path.realpath(project_root) if project_root else "",
        "entries": [],
    }


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return _empty_memory()
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("MEMORY_STORE_INVALID") from error
    if not isinstance(data, dict) or data.get("schema_version") != MEMORY_SCHEMA_VERSION:
        raise ValueError("MEMORY_SCHEMA_INCOMPATIBLE")
    if set(data) != {"schema_version", "project_root", "entries"}:
        raise ValueError("MEMORY_STORE_INVALID")
    if not isinstance(data["project_root"], str) or not isinstance(data["entries"], list):
        raise ValueError("MEMORY_STORE_INVALID")
    for entry in data["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"id", "kind", "text", "files", "created_at"}:
            raise ValueError("MEMORY_STORE_INVALID")
        if not all(isinstance(entry[key], str) for key in ("id", "kind", "text", "created_at")):
            raise ValueError("MEMORY_STORE_INVALID")
        if not isinstance(entry["files"], list):
            raise ValueError("MEMORY_STORE_INVALID")
        for file_ref in entry["files"]:
            if not isinstance(file_ref, dict) or set(file_ref) != {"path", "file_hash"}:
                raise ValueError("MEMORY_STORE_INVALID")
            if not isinstance(file_ref["path"], str) or not isinstance(file_ref["file_hash"], str):
                raise ValueError("MEMORY_STORE_INVALID")
    return data


def _save(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".eyle-memory-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _live_hash(project_root: str, relative_path: str) -> str | None:
    root = os.path.realpath(project_root)
    absolute = os.path.realpath(os.path.join(root, relative_path))
    if absolute != root and not absolute.startswith(root + os.sep):
        return None
    try:
        with open(absolute, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def search_memory(base_dir: str, project_root: str, query: str = "", limit: int = 8) -> List[Dict[str, Any]]:
    path = _memory_path(base_dir, project_root)
    data = _load(path)
    terms = [term.casefold() for term in str(query or "").split() if term.strip()]
    results: List[Dict[str, Any]] = []
    for entry in list(data.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        files = entry.get("files") or []
        stale = False
        for item in files:
            if not isinstance(item, dict):
                continue
            relative = str(item.get("path") or "")
            expected = str(item.get("file_hash") or "")
            if relative and expected and _live_hash(project_root, relative) != expected:
                stale = True
                break
        if stale:
            continue
        haystack = " ".join([
            str(entry.get("text") or ""),
            str(entry.get("kind") or ""),
            " ".join(str(item.get("path") or "") for item in files if isinstance(item, dict)),
        ]).casefold()
        if terms and not all(term in haystack for term in terms):
            continue
        results.append({
            "id": entry.get("id"),
            "kind": entry.get("kind"),
            "text": entry.get("text"),
            "files": files,
            "created_at": entry.get("created_at"),
        })
        if len(results) >= max(1, min(int(limit or 8), 20)):
            break
    return results


def store_memory(
    base_dir: str,
    project_root: str,
    text: str,
    kind: str = "fact",
    files: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        raise ValueError("memory text is empty")
    normalized_files = []
    for item in files or []:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("path") or "").replace("\\", "/")
        file_hash = str(item.get("file_hash") or "")
        if relative and file_hash:
            normalized_files.append({"path": relative, "file_hash": file_hash})
    path = _memory_path(base_dir, project_root)
    data = _load(path)
    entries = [entry for entry in data.get("entries") or [] if isinstance(entry, dict)]
    fingerprint = hashlib.sha256(
        json.dumps({"text": text, "kind": kind, "files": normalized_files}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    entry = {
        "id": f"mem-{fingerprint}",
        "kind": str(kind or "fact")[:40],
        "text": text[:2000],
        "files": normalized_files,
        "created_at": _utc_now(),
    }
    entries = [item for item in entries if item.get("id") != entry["id"]]
    entries.append(entry)
    data = {"schema_version": MEMORY_SCHEMA_VERSION, "project_root": os.path.realpath(project_root), "entries": entries[-200:]}
    _save(path, data)
    return entry
