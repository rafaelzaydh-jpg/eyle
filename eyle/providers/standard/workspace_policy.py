"""Canonical workspace visibility and protected-resource policy.

Eyle keeps a structural protected-resource boundary. Normal project files are
readable regardless of identifiers or literal content. Only resources whose
path identifies a credential/private-key store are protected, and every access
surface resolves aliases to the same physical resource identity before deciding
whether content may be exposed.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Optional, Set, Tuple

from .security import _resolver_caminho_seguro

EXTENSOES_TEXTO = {
    ".py", ".pyi", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rb", ".php", ".rs", ".swift", ".kt", ".sql", ".md", ".txt",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".sh", ".bat",
    ".pem", ".crt", ".cer", ".pub",
}
PASTAS_IGNORADAS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", "env", "dist", "build",
    ".idea", ".vscode", "target", ".mypy_cache", ".pytest_cache", "eyle-base",
}

PROTECTED_RESOURCE_NAMES = {
    ".env", ".envrc", ".npmrc", ".pypirc", ".netrc",
    "credentials.json", "credential.json", "secrets.json", "secret.json",
    "tokens.json", "token.json", "service-account.json", "service_account.json",
    "application_default_credentials.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "private_key.pem", "private-key.pem", "private.pem",
}
PROTECTED_RESOURCE_SUFFIXES = (
    ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx",
    ".private.pem", "-private.pem", "_private.pem",
)
PROTECTED_RESOURCE_PATH_SUFFIXES = (
    "/.aws/credentials",
    "/.docker/config.json",
)
READABLE_ENV_TEMPLATE_NAMES = {
    ".env.example", ".env.sample", ".env.template", ".env.dist",
    ".env.default", ".env.defaults",
}
READABLE_ENV_TEMPLATE_SUFFIXES = (
    ".example", ".sample", ".template", ".dist", ".default", ".defaults",
)


def _glob_gitignore_para_regex(padrao):
    result=[]; i=0
    while i < len(padrao):
        c=padrao[i]
        if c == "*":
            if i+1 < len(padrao) and padrao[i+1] == "*":
                i += 2
                if i < len(padrao) and padrao[i] == "/": result.append("(?:.*/)?"); i += 1
                else: result.append(".*")
                continue
            result.append("[^/]*")
        elif c == "?": result.append("[^/]")
        elif c == "[":
            end=padrao.find("]",i+1)
            if end == -1: result.append(r"\[")
            else:
                cls=padrao[i+1:end]
                if cls.startswith("!"): cls="^"+cls[1:]
                elif cls.startswith("^"): cls="\\"+cls
                result.append("["+cls+"]"); i=end
        else: result.append(re.escape(c))
        i += 1
    return "".join(result)


def _carregar_gitignore(caminho_projeto, diretorio_abs, diretorio_rel=""):
    rel=os.path.join(diretorio_rel,".gitignore") if diretorio_rel else ".gitignore"
    safe=_resolver_caminho_seguro(caminho_projeto,rel)
    if safe is None or not os.path.isfile(safe): return []
    try:
        with open(safe, "r", encoding="utf-8", errors="replace") as arquivo:
            lines = arquivo.read(1024 * 1024).splitlines()
    except OSError:
        return []
    rules=[]; base=diretorio_rel.replace(os.sep,"/").strip("/")
    for line in lines:
        line=line.rstrip()
        if not line: continue
        if line.startswith(r"\#"): line=line[1:]
        elif line.startswith("#"): continue
        neg=False
        if line.startswith(r"\!"): line=line[1:]
        elif line.startswith("!"): neg=True; line=line[1:]
        if not line: continue
        dir_only=line.endswith("/"); line=line.rstrip("/"); anchored=line.startswith("/"); line=line.lstrip("/")
        if not line: continue
        try: regex=re.compile("^"+_glob_gitignore_para_regex(line)+"$")
        except re.error: continue
        rules.append({"base":base,"negada":neg,"somente_diretorio":dir_only,"tem_barra":anchored or "/" in line,"regex":regex})
    return rules


def _ignorado_por_gitignore(caminho_relativo, diretorio, regras):
    rel=caminho_relativo.replace(os.sep,"/").strip("/"); ignored=False
    for rule in regras:
        base=rule["base"]
        if base:
            if rel == base: target=""
            elif rel.startswith(base+"/"): target=rel[len(base)+1:]
            else: continue
        else: target=rel
        if rule["somente_diretorio"] and not diretorio: continue
        candidate=target if rule["tem_barra"] else target.rsplit("/",1)[-1]
        if rule["regex"].match(candidate): ignored=not rule["negada"]
    return ignored


def _normalize_relative_path(relative_path: object) -> str:
    return str(relative_path or "").replace("\\", "/").strip("/").lower()




def is_readable_env_template_path(relative_path: object) -> bool:
    normalized = _normalize_relative_path(relative_path)
    if not normalized:
        return False
    name = normalized.rsplit("/", 1)[-1]
    return name in READABLE_ENV_TEMPLATE_NAMES or (
        name.startswith(".env.") and any(name.endswith(suffix) for suffix in READABLE_ENV_TEMPLATE_SUFFIXES)
    )


def _is_protected_resource_path(relative_path: object) -> bool:
    """Classify only explicit credential/private-key resource paths.

    No file-content inspection is performed. Environment templates are readable
    documentation/configuration examples rather than credential stores.
    """
    normalized = _normalize_relative_path(relative_path)
    if not normalized:
        return False
    name = normalized.rsplit("/", 1)[-1]
    if is_readable_env_template_path(normalized):
        return False
    if name in PROTECTED_RESOURCE_NAMES or name.startswith(".env."):
        return True
    if name.endswith(PROTECTED_RESOURCE_SUFFIXES):
        return True
    padded = "/" + normalized
    return any(padded.endswith(suffix) for suffix in PROTECTED_RESOURCE_PATH_SUFFIXES)


def _file_identity(path: str) -> Optional[Tuple[int, int]]:
    try:
        info = os.stat(path, follow_symlinks=True)
    except OSError:
        return None
    if not os.path.isfile(path):
        return None
    return int(info.st_dev), int(info.st_ino)


def build_protected_resource_index(project_root: object) -> Dict[str, object]:
    """Return path and physical identities for protected resources in a workspace.

    Physical identity closes both symlink and hard-link aliases. The index is a
    fresh physical observation; callers should build it per operation rather
    than cache it across workspace mutations.
    """
    root = os.path.realpath(os.fspath(project_root))
    protected_paths: Set[str] = set()
    identities: Set[Tuple[int, int]] = set()
    if not os.path.isdir(root):
        return {"root": root, "paths": protected_paths, "identities": identities}

    for current, dirs, names in os.walk(root, followlinks=False):
        # Git internals are not part of the user workspace content surface.
        dirs[:] = [name for name in dirs if name != ".git"]
        for name in names:
            absolute = os.path.join(current, name)
            relative = os.path.relpath(absolute, root).replace(os.sep, "/")
            if not _is_protected_resource_path(relative):
                continue
            protected_paths.add(relative)
            # A protected symlink restricts access through that path, but must
            # not make an otherwise normal target file globally protected.
            # Hard links are different: they share the same physical inode and
            # have no directional target, so their identity must be protected.
            if os.path.islink(absolute):
                continue
            resolved = _resolver_caminho_seguro(root, relative)
            if resolved is None:
                continue
            identity = _file_identity(resolved)
            if identity is not None:
                identities.add(identity)
    return {"root": root, "paths": protected_paths, "identities": identities}


def protected_resource_info(project_root: object, relative_path: object, *, index: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """Resolve whether a workspace path references a protected physical resource."""
    root = os.path.realpath(os.fspath(project_root))
    normalized = str(relative_path or "").replace("\\", "/").strip("/")
    if not normalized:
        return {"protected": False, "path": normalized, "reason": None, "resource_key": None}

    if _is_protected_resource_path(normalized):
        resolved = _resolver_caminho_seguro(root, normalized)
        identity = _file_identity(resolved) if resolved else None
        return {
            "protected": True, "path": normalized, "reason": "protected_path",
            "resource_key": f"inode:{identity[0]}:{identity[1]}" if identity else f"path:{_normalize_relative_path(normalized)}",
        }

    resolved = _resolver_caminho_seguro(root, normalized)
    if resolved is None:
        return {"protected": False, "path": normalized, "reason": None, "resource_key": None}

    try:
        resolved_relative = os.path.relpath(resolved, root).replace(os.sep, "/")
    except ValueError:
        resolved_relative = ""
    if resolved_relative and _is_protected_resource_path(resolved_relative):
        identity = _file_identity(resolved)
        return {
            "protected": True, "path": normalized, "reason": "protected_symlink_target",
            "resource_key": f"inode:{identity[0]}:{identity[1]}" if identity else f"path:{_normalize_relative_path(resolved_relative)}",
        }

    identity = _file_identity(resolved)
    if identity is None:
        return {"protected": False, "path": normalized, "reason": None, "resource_key": None}
    current_index = index if isinstance(index, dict) else build_protected_resource_index(root)
    protected_identities = current_index.get("identities") if isinstance(current_index, dict) else set()
    if identity in (protected_identities or set()):
        return {
            "protected": True, "path": normalized, "reason": "protected_hardlink_identity",
            "resource_key": f"inode:{identity[0]}:{identity[1]}",
        }
    return {"protected": False, "path": normalized, "reason": None, "resource_key": None}


def is_protected_workspace_resource(project_root: object, relative_path: object, *, index: Optional[Dict[str, object]] = None) -> bool:
    return bool(protected_resource_info(project_root, relative_path, index=index).get("protected"))


def validate_workspace_read(project_root: object, relative_path: object, *, index: Optional[Dict[str, object]] = None):
    """Return the canonical denial code for protected resource content access."""
    if is_protected_workspace_resource(project_root, relative_path, index=index):
        return "PROTECTED_RESOURCE_READ_BLOCKED"
    return None
