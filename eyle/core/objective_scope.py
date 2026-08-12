"""Canonical physical path-scope semantics for objective capabilities.

The Main LLM may declare where a capability should observe. Runtime resolves
that declaration mechanically. A literal project-relative file means exactly
that file; a literal directory means its recursive subtree; selectors that
contain glob metacharacters remain explicit full-path glob patterns.

This module never decides semantic relevance.
"""
from __future__ import annotations

import fnmatch
import os
import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .security import _resolver_caminho_seguro

_GLOB_MAGIC = "*?["
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class ObjectiveScopeError(ValueError):
    def __init__(self, code: str, detail: str, *, selector: str | None = None):
        super().__init__(detail)
        self.code = str(code)
        self.detail = str(detail)
        self.selector = selector


def normalize_scope_selector(value: Any) -> str:
    """Return one canonical project-relative selector without changing meaning."""
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if text.endswith("/") and text != "/":
        text = text.rstrip("/")
    if text == ".":
        return "."
    return text


def normalize_scope_selectors(values: Iterable[Any] | None) -> List[str]:
    result: List[str] = []
    for value in values or []:
        selector = normalize_scope_selector(value)
        if selector and selector not in result:
            result.append(selector)
    return result


def selector_has_glob(selector: str) -> bool:
    return any(char in str(selector or "") for char in _GLOB_MAGIC)


def _unsafe_selector(selector: str) -> bool:
    value = str(selector or "")
    if not value:
        return True
    if value.startswith("/") or value.startswith("\\") or _WINDOWS_DRIVE.match(value):
        return True
    return any(part == ".." for part in value.replace("\\", "/").split("/"))


def _classify_literal(root: str, selector: str) -> Tuple[str, str | None]:
    if selector == ".":
        return "directory", os.path.realpath(root)
    absolute = _resolver_caminho_seguro(root, selector)
    if absolute is None:
        raise ObjectiveScopeError(
            "SEARCH_SCOPE_PATH_UNSAFE",
            f"objective search scope path is unsafe: {selector}",
            selector=selector,
        )
    if os.path.isfile(absolute):
        return "file", absolute
    if os.path.isdir(absolute):
        return "directory", absolute
    if os.path.lexists(absolute):
        return "special", absolute
    return "missing", absolute


def _literal_match(relpath: str, selector: str, kind: str) -> bool:
    rel = str(relpath or "").replace("\\", "/")
    if selector == "." and kind == "directory":
        return True
    if kind == "file":
        return rel == selector
    if kind == "directory":
        prefix = selector.rstrip("/") + "/"
        return rel.startswith(prefix)
    return False


def _resolve_selector(root: str, selector: str, files: Sequence[str], *, role: str) -> Dict[str, Any]:
    if _unsafe_selector(selector):
        raise ObjectiveScopeError(
            "SEARCH_SCOPE_PATH_UNSAFE",
            f"objective search scope selector is unsafe: {selector}",
            selector=selector,
        )

    if selector_has_glob(selector):
        matches = [path for path in files if fnmatch.fnmatchcase(path, selector)]
        return {
            "selector": selector,
            "kind": "glob",
            "status": "matched" if matches else "empty",
            "matched_files": len(matches),
            "files": matches,
        }

    kind, _ = _classify_literal(root, selector)
    if kind == "missing":
        if role == "exclude":
            return {
                "selector": selector,
                "kind": "literal",
                "status": "missing_no_effect",
                "matched_files": 0,
                "files": [],
            }
        raise ObjectiveScopeError(
            "SEARCH_SCOPE_PATH_NOT_FOUND",
            f"objective search include path does not exist: {selector}",
            selector=selector,
        )
    if kind == "special":
        raise ObjectiveScopeError(
            "SEARCH_SCOPE_PATH_UNSUPPORTED",
            f"objective search scope path is not a regular file or directory: {selector}",
            selector=selector,
        )
    matches = [path for path in files if _literal_match(path, selector, kind)]
    return {
        "selector": selector,
        "kind": kind,
        "status": "resolved",
        "matched_files": len(matches),
        "files": matches,
    }


def resolve_objective_file_scope(
    root: str,
    files: Sequence[str],
    *,
    include_paths: Iterable[Any] | None = None,
    exclude_paths: Iterable[Any] | None = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Resolve a Main-declared file scope over a capability's physical universe.

    ``files`` is the capability-owned project-relative file universe before
    protected-content filtering. Scope resolution happens first; later stages
    decide which resolved resources are physically readable.
    """
    include = normalize_scope_selectors(include_paths)
    exclude = normalize_scope_selectors(exclude_paths)
    universe = sorted(dict.fromkeys(str(path).replace("\\", "/") for path in files if str(path)))

    include_resolution: List[Dict[str, Any]] = []
    exclude_resolution: List[Dict[str, Any]] = []

    if include:
        selected = set()
        for selector in include:
            item = _resolve_selector(root, selector, universe, role="include")
            selected.update(item.pop("files"))
            include_resolution.append(item)
    else:
        selected = set(universe)

    selected_before_exclusions = len(selected)
    for selector in exclude:
        item = _resolve_selector(root, selector, sorted(selected), role="exclude")
        selected.difference_update(item.pop("files"))
        exclude_resolution.append(item)

    resolved = sorted(selected)
    metadata = {
        "include_paths": include,
        "exclude_paths": exclude,
        "resolution_complete": True,
        "capability_universe_files": len(universe),
        "files_before_exclusions": selected_before_exclusions,
        "files_resolved": len(resolved),
        "include_resolution": include_resolution,
        "exclude_resolution": exclude_resolution,
    }
    return resolved, metadata
