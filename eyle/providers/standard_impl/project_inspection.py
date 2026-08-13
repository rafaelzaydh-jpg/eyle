"""Deterministic project inspection helpers for Eyle.

These helpers measure and expose objective signals. They deliberately do not
rank files or decide what is "important" for a task; relevance remains an LLM
decision based on the user's goal.
"""
from __future__ import annotations

import ast
import json
import math
import os
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Dict, List, Tuple

from .security import _resolver_caminho_seguro
from .text_hash import hash_texto
from .workspace_io import ErroLeituraProjeto, listar_arvore_projeto
from .workspace_policy import build_protected_resource_index, is_protected_workspace_resource


LANGUAGE_BY_EXTENSION = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".jsx": "JavaScript/JSX", ".tsx": "TypeScript/TSX", ".java": "Java",
    ".c": "C", ".cpp": "C++", ".h": "C/C++ Header", ".hpp": "C++ Header",
    ".cs": "C#", ".go": "Go", ".rb": "Ruby", ".php": "PHP", ".rs": "Rust",
    ".swift": "Swift", ".kt": "Kotlin", ".sql": "SQL", ".html": "HTML",
    ".css": "CSS", ".sh": "Shell", ".bat": "Batch", ".json": "JSON",
    ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".ini": "INI",
    ".cfg": "Config", ".md": "Markdown", ".txt": "Text",
}

DEPENDENCY_MANIFESTS = {
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "poetry.lock",
    "pipfile", "pipfile.lock", "package.json", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "cargo.toml", "cargo.lock", "go.mod", "go.sum",
    "gemfile", "gemfile.lock", "composer.json", "composer.lock", "pom.xml",
    "build.gradle", "build.gradle.kts",
}
CONFIG_NAMES = {
    "pytest.ini", "tox.ini", "setup.cfg", "mypy.ini", "ruff.toml", ".editorconfig",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "makefile",
    "tsconfig.json", "vite.config.js", "vite.config.ts", "webpack.config.js",
}
TEST_NAME_RE = re.compile(r"(?:^|/)(?:tests?|specs?)(?:/|$)|(?:^|/)(?:test_.*|.*_test|.*\.spec|.*\.test)\.[^.]+$", re.I)

FRAMEWORK_IMPORTS = {
    "flask": "Flask", "django": "Django", "fastapi": "FastAPI", "starlette": "Starlette",
    "pytest": "pytest", "click": "Click", "typer": "Typer", "sqlalchemy": "SQLAlchemy",
    "pydantic": "Pydantic", "numpy": "NumPy", "pandas": "pandas",
}
JS_FRAMEWORK_PACKAGES = {
    "react": "React", "vue": "Vue", "svelte": "Svelte", "express": "Express",
    "next": "Next.js", "nuxt": "Nuxt", "vite": "Vite", "jest": "Jest",
    "vitest": "Vitest", "typescript": "TypeScript",
}


def _scan_limits(config: Dict[str, Any]) -> Tuple[int, int, int]:
    agent = ((((config or {}).get("providers") or {}).get("standard") or {}))
    return (
        max(100, int(agent.get("max_project_scan_entries", 20000) or 20000)),
        max(1, int(agent.get("max_project_scan_depth", 32) or 32)),
        max(1024, int(agent.get("max_project_file_bytes", 4 * 1024 * 1024) or 4 * 1024 * 1024)),
    )


def collect_project_inventory(root: str, config: Dict[str, Any]) -> Dict[str, Any]:
    max_entries, max_depth, _ = _scan_limits(config)
    inventory = listar_arvore_projeto(
        root,
        limite=max_entries,
        profundidade=max_depth,
        filtro=None,
    )
    files = [
        str(item.get("path")) for item in inventory.get("entries") or []
        if item.get("type") == "file" and item.get("path")
    ]
    directories = [
        str(item.get("path")) for item in inventory.get("entries") or []
        if item.get("type") == "diretorio" and item.get("path")
    ]
    return {"inventory": inventory, "files": files, "directories": directories}


def _safe_read(root: str, rel: str, max_bytes: int, *, protected_index=None) -> Tuple[str | None, int, str | None]:
    if is_protected_workspace_resource(root, rel, index=protected_index):
        return None, 0, "protected_resource"
    safe = _resolver_caminho_seguro(root, rel)
    if safe is None or not os.path.isfile(safe):
        return None, 0, "unavailable"
    try:
        size = os.path.getsize(safe)
    except OSError:
        return None, 0, "stat_error"
    if size > max_bytes:
        return None, size, "file_too_large"
    try:
        with open(safe, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(), size, None
    except OSError:
        return None, size, "read_error"


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def project_stats(root: str, config: Dict[str, Any]) -> Dict[str, Any]:
    collected = collect_project_inventory(root, config)
    inventory = collected["inventory"]
    _, _, max_bytes = _scan_limits(config)
    extensions: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    total_lines = total_chars = total_bytes = 0
    measured_files = 0
    skipped_large = skipped_read = skipped_protected = 0
    protected_index = build_protected_resource_index(root)

    for rel in collected["files"]:
        ext = os.path.splitext(rel)[1].lower() or "[no_extension]"
        extensions[ext] += 1
        languages[LANGUAGE_BY_EXTENSION.get(ext, "Other text")] += 1
        text, size, error = _safe_read(root, rel, max_bytes, protected_index=protected_index)
        if error == "file_too_large":
            skipped_large += 1
            total_bytes += size
            continue
        if error == "protected_resource":
            skipped_protected += 1
            continue
        if error:
            skipped_read += 1
            continue
        measured_files += 1
        total_bytes += size
        total_chars += len(text or "")
        total_lines += _line_count(text or "")

    canonical = {
        "inventory_hash": inventory.get("inventory_hash"),
        "files": len(collected["files"]),
        "directories": len(collected["directories"]),
        "measured_files": measured_files,
        "lines": total_lines,
        "characters": total_chars,
        "bytes": total_bytes,
        "extensions": dict(sorted(extensions.items())),
    }
    return {
        "schema_version": 1,
        "scan_complete": bool(inventory.get("varredura_completa")) and not skipped_read and not skipped_large,
        "coverage_scope": "all_workspace_files" if not skipped_protected else "readable_workspace_files",
        "protected_resources_excluded": skipped_protected,
        "inventory_hash": inventory.get("inventory_hash"),
        "scan_hash": hash_texto(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        "files": len(collected["files"]),
        "directories": len(collected["directories"]),
        "measured_files": measured_files,
        "lines": total_lines,
        "characters": total_chars,
        "bytes": total_bytes,
        "by_extension": dict(sorted(extensions.items())),
        "by_language": dict(sorted(languages.items(), key=lambda item: (-item[1], item[0]))),
        "skipped": {"file_too_large": skipped_large, "protected_resource": skipped_protected, "read_error": skipped_read},
        "ignored_by_reason": inventory.get("ignorados_por_motivo") or {},
    }


def _files_for_requested_path(root: str, config: Dict[str, Any], requested: str | None) -> Tuple[List[str], str]:
    collected = collect_project_inventory(root, config)
    files = collected["files"]
    raw = str(requested or ".").strip().replace("\\", "/")
    if raw in {"", ".", "./"}:
        return files, "."
    normalized = raw.strip("/")
    safe = _resolver_caminho_seguro(root, normalized)
    if safe is None:
        raise ErroLeituraProjeto("UNSAFE_PATH", f"caminho inseguro rejeitado: '{raw}'")
    if os.path.isfile(safe):
        if is_protected_workspace_resource(root, normalized, index=build_protected_resource_index(root)):
            raise ErroLeituraProjeto("PROTECTED_RESOURCE_READ_BLOCKED", "content access is restricted for this protected resource")
        if normalized not in files:
            raise ErroLeituraProjeto("UNSUPPORTED_OR_IGNORED_FILE", f"arquivo '{normalized}' nao pertence ao conjunto de texto seguro")
        return [normalized], normalized
    if os.path.isdir(safe):
        prefix = normalized.rstrip("/") + "/"
        return [path for path in files if path == normalized or path.startswith(prefix)], normalized
    raise ErroLeituraProjeto("PATH_NOT_FOUND", f"caminho '{normalized}' nao encontrado")


def count_tokens(root: str, config: Dict[str, Any], path: str | None = None, tokenizer: str | None = None) -> Dict[str, Any]:
    files, normalized_path = _files_for_requested_path(root, config, path)
    _, _, max_bytes = _scan_limits(config)
    context_engine = (config or {}).get("context_engine") or {}
    chars_per_token = float(context_engine.get("chars_per_token_fallback", 3) or 3)
    if chars_per_token <= 0:
        chars_per_token = 3.0

    chars = lines = bytes_total = measured = skipped_large = skipped_read = skipped_protected = 0
    protected_index = build_protected_resource_index(root)
    for rel in files:
        text, size, error = _safe_read(root, rel, max_bytes, protected_index=protected_index)
        if error == "file_too_large":
            skipped_large += 1
            continue
        if error == "protected_resource":
            skipped_protected += 1
            continue
        if error:
            skipped_read += 1
            continue
        text = text or ""
        measured += 1
        file_chars = len(text)
        estimate = int(math.ceil(file_chars / chars_per_token)) if file_chars else 0
        chars += file_chars
        lines += _line_count(text)
        bytes_total += size

    estimated = int(math.ceil(chars / chars_per_token)) if chars else 0
    requested_tokenizer = str(tokenizer or ((config or {}).get("llm") or {}).get("model") or "configured").strip()
    canonical = json.dumps({
        "path": normalized_path, "files": files, "characters": chars,
        "chars_per_token": chars_per_token, "estimated_tokens": estimated,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": 1,
        "path": normalized_path,
        "tokenizer_requested": requested_tokenizer,
        "tokenizer_used": "heuristic:characters_per_token",
        "exact": False,
        "estimated_tokens": estimated,
        "characters_per_token": chars_per_token,
        "files_considered": len(files),
        "files_measured": measured,
        "protected_resources_skipped": skipped_protected,
        "characters": chars,
        "lines": lines,
        "bytes": bytes_total,
        "skipped": {"file_too_large": skipped_large, "protected_resource": skipped_protected, "read_error": skipped_read},
        "measurement_hash": hash_texto(canonical),
        "note": "No exact model tokenizer is bundled; this is a measured character count converted with the configured fallback ratio.",
    }


def _module_name(rel: str) -> str:
    no_ext = rel[:-3] if rel.endswith(".py") else rel
    parts = [part for part in no_ext.replace("\\", "/").split("/") if part]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _python_signals(path: str, text: str) -> Dict[str, Any]:
    signals: Dict[str, Any] = {
        "imports": [], "local_imports": [], "functions": 0, "classes": 0,
        "route_decorators": 0, "has_main_guard": False,
    }
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as exc:
        signals["syntax_error"] = {"line": exc.lineno, "message": exc.msg}
        return signals

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            signals["functions"] += 1
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute) and target.attr in {"route", "get", "post", "put", "delete", "patch"}:
                    signals["route_decorators"] += 1
        elif isinstance(node, ast.ClassDef):
            signals["classes"] += 1
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
        elif isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                and test.left.id == "__name__" and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == "__main__"
            ):
                signals["has_main_guard"] = True
    signals["imports"] = sorted(set(imports))
    return signals


def _known_ci_files(root: str) -> List[str]:
    found: List[str] = []
    candidates = [".gitlab-ci.yml", ".gitlab-ci.yaml", "azure-pipelines.yml", "Jenkinsfile"]
    for rel in candidates:
        safe = _resolver_caminho_seguro(root, rel)
        if safe and os.path.isfile(safe):
            found.append(rel)
    workflows = _resolver_caminho_seguro(root, ".github/workflows")
    if workflows and os.path.isdir(workflows):
        try:
            for item in sorted(os.scandir(workflows), key=lambda x: x.name.lower()):
                if item.is_file(follow_symlinks=False) and item.name.lower().endswith((".yml", ".yaml")):
                    found.append(f".github/workflows/{item.name}")
                    if len(found) >= 50:
                        break
        except OSError:
            pass
    return found


def inspect_project(root: str, config: Dict[str, Any]) -> Dict[str, Any]:
    collected = collect_project_inventory(root, config)
    inventory = collected["inventory"]
    _, _, max_bytes = _scan_limits(config)
    files = collected["files"]
    languages = Counter()
    manifests = []
    configs = []
    tests = []
    python_sources: Dict[str, Dict[str, Any]] = {}
    framework_sources: Dict[str, set[str]] = defaultdict(set)

    for rel in files:
        ext = os.path.splitext(rel)[1].lower()
        languages[LANGUAGE_BY_EXTENSION.get(ext, "Other text")] += 1
        base = os.path.basename(rel).lower()
        if base in DEPENDENCY_MANIFESTS:
            manifests.append(rel)
        if base in CONFIG_NAMES:
            configs.append(rel)
        if TEST_NAME_RE.search(rel):
            tests.append(rel)
        if ext != ".py":
            continue
        text, _, error = _safe_read(root, rel, max_bytes)
        if error:
            continue
        sig = _python_signals(rel, text or "")
        python_sources[rel] = sig
        for imported in sig.get("imports") or []:
            root_import = imported.split(".", 1)[0].lower()
            framework = FRAMEWORK_IMPORTS.get(root_import)
            if framework:
                framework_sources[framework].add(rel)

    module_to_path = {}
    for rel in python_sources:
        module = _module_name(rel)
        if module:
            module_to_path[module] = rel
            module_to_path.setdefault(module.split(".")[-1], rel)

    imported_by: Dict[str, set[str]] = defaultdict(set)
    for rel, sig in python_sources.items():
        local_imports = set()
        for imported in sig.get("imports") or []:
            candidates = [imported, imported.split(".", 1)[0], imported.split(".")[-1]]
            target = next((module_to_path.get(candidate) for candidate in candidates if module_to_path.get(candidate)), None)
            if target and target != rel:
                local_imports.add(target)
                imported_by[target].add(rel)
        sig["local_imports"] = sorted(local_imports)

    package_json_path = next((rel for rel in files if rel.lower() == "package.json"), None)
    if package_json_path:
        text, _, error = _safe_read(root, package_json_path, max_bytes)
        if not error:
            try:
                package = json.loads(text or "{}")
            except json.JSONDecodeError:
                package = {}
            deps = {}
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                if isinstance(package.get(key), dict):
                    deps.update(package[key])
            for name, framework in JS_FRAMEWORK_PACKAGES.items():
                if name in deps:
                    framework_sources[framework].add(package_json_path)

    entrypoints = []
    local_import_edges = []
    route_files = []
    syntax_error_files = []
    for rel, sig in sorted(python_sources.items()):
        entry_signals = []
        if sig.get("has_main_guard"):
            entry_signals.append("python_main_guard")
        if os.path.basename(rel).lower() in {"main.py", "app.py", "manage.py", "wsgi.py", "asgi.py"}:
            entry_signals.append("conventional_entrypoint_filename")
        if entry_signals:
            entrypoints.append({"path": rel, "signals": entry_signals})
        if int(sig.get("route_decorators") or 0) > 0:
            route_files.append({"path": rel, "route_decorator_count": int(sig.get("route_decorators") or 0)})
        if sig.get("syntax_error"):
            syntax_error_files.append({"path": rel, "syntax_error": sig["syntax_error"]})
        for target in sig.get("local_imports") or []:
            local_import_edges.append({"from": rel, "to": target})

    ci_files = _known_ci_files(root)
    imported_counts = [
        {"path": rel, "imported_by_count": len(sources)}
        for rel, sources in imported_by.items() if sources
    ]
    imported_counts.sort(key=lambda item: (-item["imported_by_count"], item["path"]))
    max_relations = max(10, int((config or {}).get("agent", {}).get("max_inspect_relation_edges", 60) or 60))
    local_import_edges.sort(key=lambda item: (item["from"], item["to"]))
    structural = {
        "files": len(files), "directories": len(collected["directories"]),
        "languages": dict(sorted(languages.items(), key=lambda item: (-item[1], item[0]))),
        "entrypoints": entrypoints, "manifests": sorted(manifests), "configs": sorted(configs),
        "tests": sorted(tests), "ci": ci_files,
        "frameworks": {name: sorted(paths) for name, paths in sorted(framework_sources.items())},
        "local_import_edges": local_import_edges,
        "route_files": route_files,
        "syntax_error_files": syntax_error_files,
        "imported_counts": imported_counts,
    }
    canonical = json.dumps(structural, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": 1,
        "scan_complete": bool(inventory.get("varredura_completa")),
        "inventory_hash": inventory.get("inventory_hash"),
        "inspection_hash": hash_texto(canonical),
        "file_count": len(files),
        "directory_count": len(collected["directories"]),
        "languages": structural["languages"],
        "entrypoint_signals": entrypoints,
        "dependency_manifests": sorted(manifests),
        "config_files": sorted(configs),
        "test_signals": {"has_tests": bool(tests), "files": sorted(tests)[:40], "count": len(tests)},
        "ci_signals": {"has_ci": bool(ci_files), "files": ci_files},
        "framework_signals": [
            {"name": name, "sources": sorted(paths)} for name, paths in sorted(framework_sources.items())
        ],
        "relation_signals": {
            "local_import_edge_count": len(local_import_edges),
            "local_import_edges": local_import_edges[:max_relations],
            "local_import_edges_truncated": len(local_import_edges) > max_relations,
            "most_imported_files": imported_counts[:20],
            "route_files": route_files[:40],
            "syntax_error_files": syntax_error_files[:20],
        },
        "ignored_by_reason": inventory.get("ignorados_por_motivo") or {},
        "policy": "Objective signals only; counts/order are measurements, not an importance or relevance decision.",
    }


_ALLOWED_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean values are not numbers")
    return Decimal(str(value))


def calculate(expression: str) -> Dict[str, Any]:
    raw = str(expression or "").strip()
    if not raw:
        raise ValueError("expression is empty")
    if len(raw) > 500:
        raise ValueError("expression is too long")
    tree = ast.parse(raw, mode="eval")

    approximation_reasons = set()

    def evaluate(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return _decimal(node.value)
        if isinstance(node, ast.Name):
            constants = {"pi": Decimal(str(math.pi)), "e": Decimal(str(math.e))}
            if node.id in constants:
                approximation_reasons.add(f"constant:{node.id}")
                return constants[node.id]
            raise ValueError(f"unknown name: {node.id}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINARY):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.Div):
                approximation_reasons.add("division")
                return left / right
            if isinstance(node.op, ast.FloorDiv): return left // right
            if isinstance(node.op, ast.Mod): return left % right
            if isinstance(node.op, ast.Pow):
                if right != right.to_integral_value() or abs(right) > 1000:
                    raise ValueError("power exponent must be an integer between -1000 and 1000")
                if right < 0:
                    approximation_reasons.add("negative_power")
                return left ** int(right)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            values = [evaluate(arg) for arg in node.args]
            if name == "abs" and len(values) == 1: return abs(values[0])
            if name == "min" and values: return min(values)
            if name == "max" and values: return max(values)
            if name == "sqrt" and len(values) == 1:
                if values[0] < 0: raise ValueError("sqrt requires a non-negative number")
                approximation_reasons.add("sqrt")
                return values[0].sqrt()
            if name == "round" and len(values) in {1, 2}:
                digits = int(values[1]) if len(values) == 2 else 0
                if len(values) == 2 and values[1] != values[1].to_integral_value():
                    raise ValueError("round digits must be an integer")
                quantum = Decimal(1).scaleb(-digits)
                return values[0].quantize(quantum)
            if name == "ceil" and len(values) == 1:
                return values[0].to_integral_value(rounding="ROUND_CEILING")
            if name == "floor" and len(values) == 1:
                return values[0].to_integral_value(rounding="ROUND_FLOOR")
            raise ValueError(f"unsupported function or arguments: {name}")
        raise ValueError(f"unsupported expression element: {type(node).__name__}")

    try:
        with localcontext() as ctx:
            ctx.prec = 50
            result = evaluate(tree)
    except (InvalidOperation, ZeroDivisionError, OverflowError) as exc:
        raise ValueError(str(exc) or exc.__class__.__name__) from exc
    if not result.is_finite():
        raise ValueError("result is not finite")
    normalized = format(result.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    if normalized in {"-0", ""}:
        normalized = "0"
    return {
        "expression": raw,
        "result": normalized,
        "exact": not approximation_reasons,
        "calculation_mode": "decimal_50_digit",
        "precision_digits": 50,
        "approximation_reasons": sorted(approximation_reasons),
    }
