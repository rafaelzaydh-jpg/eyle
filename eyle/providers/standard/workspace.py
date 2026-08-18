"""Discover Eyle's user work plane without conflating it with Eyle itself."""
from __future__ import annotations
import os

_PLACEHOLDER_FILES = {".gitkeep", ".keep", ".placeholder"}
_IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _has_meaningful_content(path: str, scan_limit: int = 200) -> bool:
    """Return whether the dedicated workspace contains non-placeholder content."""
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


def discover_project(base_dir: str):
    """Return the dedicated user workspace and a private Eyle self-source root.

    The workspace boundary closes the old fallback where an empty ``workspace/`` caused the
    installation directory itself to become the writable project root.  Empty is
    a valid workspace state.  Eyle's installation remains available separately
    as a read-only/self-sandbox source, never as the real write target.
    """
    root = os.path.realpath(base_dir)
    workspace = os.path.realpath(os.path.join(root, "workspace"))
    workspace_available = os.path.isdir(workspace)
    return {
        "caminho_origem": workspace if workspace_available else None,
        "nome": os.path.basename(workspace) or "workspace",
        "auto_discovered": True,
        "discovery": "workspace" if workspace_available else "workspace_unavailable",
        "content_state": ("nonempty" if _has_meaningful_content(workspace) else "empty") if workspace_available else "unavailable",
        # Runtime-private physical source. _project_descriptor never exposes the
        # host path; observational capabilities can select it as source=eyle.
        "eyle_root": root,
    }
