"""Discover and open the live source workspace directly."""
from __future__ import annotations
import os

_PLACEHOLDER_FILES = {".gitkeep", ".keep", ".placeholder"}
_IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_PROJECT_MARKERS = ("app.py", "main.py", "pyproject.toml", "package.json", "setup.py", "Cargo.toml", "go.mod")


def _has_meaningful_content(path: str, scan_limit: int = 200) -> bool:
    """A placeholder directory is not an active project.

    The packaged Eyle ships ``workspace/.gitkeep``. Treating that directory as
    a project made every tool truthfully inspect an empty root instead of the
    real repository. Any real non-placeholder file is enough for a user
    workspace to win discovery; the walk is bounded.
    """
    observed = 0
    try:
        for current, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
            for name in files:
                observed += 1
                if name not in _PLACEHOLDER_FILES:
                    return True
                if observed >= scan_limit:
                    return False
    except OSError:
        return False
    return False


def _base_looks_like_project(path: str) -> bool:
    if any(os.path.isfile(os.path.join(path, name)) for name in _PROJECT_MARKERS):
        return True
    return any(os.path.isdir(os.path.join(path, name)) for name in ("eyle", "src", "lib", "tests"))


def discover_project(base_dir: str):
    workspace = os.path.join(base_dir, "workspace")
    if os.path.isdir(workspace) and _has_meaningful_content(workspace):
        path = workspace
    elif os.path.isdir(base_dir) and _base_looks_like_project(base_dir):
        path = base_dir
    else:
        return None
    real = os.path.realpath(path)
    return {
        "caminho_origem": real,
        "nome": os.path.basename(real),
        "auto_discovered": True,
        "discovery": "workspace" if path == workspace else "base_dir",
    }
