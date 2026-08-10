"""General structural code-relation inspection.

This module reports observable relationships. It never decides whether a symbol
is live, dead, legacy, important, safe to remove, or semantically sufficient.
Python receives AST-aware relations; other source languages receive truthful
textual references so the public tool remains useful without pretending to have
semantic resolution it does not possess.
"""
from __future__ import annotations

import ast
import os
from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .workspace_policy import PASTAS_IGNORADAS, _caminho_parece_segredo, _conteudo_parece_segredo

_SOURCE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cc", ".cpp",
    ".h", ".hpp", ".cs", ".go", ".rb", ".php", ".rs", ".swift", ".kt", ".kts",
    ".scala", ".sh", ".bash", ".sql", ".vue", ".svelte",
}


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _source_files(root: str, *, max_files: int, max_bytes: int) -> Tuple[List[str], bool, int]:
    files: List[str] = []
    truncated = False
    secret_skipped = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in PASTAS_IGNORADAS and not d.startswith("."))
        for name in sorted(names):
            if os.path.splitext(name)[1].lower() not in _SOURCE_EXTENSIONS:
                continue
            path = os.path.join(current, name)
            relpath = _rel(root, path)
            if _caminho_parece_segredo(relpath):
                secret_skipped += 1
                continue
            try:
                if not os.path.isfile(path) or os.path.getsize(path) > max_bytes:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    sample = fh.read(512 * 1024)
                if _conteudo_parece_segredo(sample):
                    secret_skipped += 1
                    continue
            except OSError:
                continue
            files.append(path)
            if len(files) >= max_files:
                truncated = True
                return files, truncated, secret_skipped
    return files, truncated, secret_skipped


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _node_id(path: str, qualname: str) -> str:
    return f"{path}::{qualname}"


def _call_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _expr_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)[:240]
    except Exception:
        return type(node).__name__


class _PythonCollector(ast.NodeVisitor):
    def __init__(self, relpath: str):
        self.path = relpath
        self.scope: List[str] = []
        self.scope_kinds: List[str] = []
        self.definitions: List[Dict[str, Any]] = []
        self.calls: List[Dict[str, Any]] = []
        self.references: List[Dict[str, Any]] = []
        self.imports: List[Dict[str, Any]] = []
        self.dynamic: List[Dict[str, Any]] = []

    def _qual(self, name: str) -> str:
        return ".".join(self.scope + [name]) if self.scope else name

    def _scope_id(self) -> str:
        return _node_id(self.path, ".".join(self.scope) if self.scope else "<module>")

    def _binding(self, node: ast.AST, value: ast.AST, kind: str, *, expression: Optional[str] = None) -> None:
        name = _call_name(value)
        if not name:
            return
        self.references.append({
            "file": self.path, "line": int(getattr(node, "lineno", 1)),
            "kind": kind, "name": name, "scope": self._scope_id(),
            "expression": (expression or _expr_text(node))[:240],
        })

    def _definition(self, node: ast.AST, name: str, kind: str) -> None:
        decorators = getattr(node, "decorator_list", None) or []
        start = min([int(getattr(node, "lineno", 1))] + [int(getattr(d, "lineno", 1)) for d in decorators])
        end = int(getattr(node, "end_lineno", None) or getattr(node, "lineno", start))
        qualname = self._qual(name)
        self.definitions.append({
            "node_id": _node_id(self.path, qualname), "file": self.path, "qualname": qualname,
            "name": name, "kind": kind, "line_start": start, "line_end": end,
        })
        for dec in decorators:
            dec_name = _call_name(dec.func if isinstance(dec, ast.Call) else dec)
            if dec_name:
                self.references.append({
                    "file": self.path, "line": int(getattr(dec, "lineno", start)),
                    "kind": "decorator", "name": dec_name, "scope": self._scope_id(),
                    "target_definition": _node_id(self.path, qualname),
                })

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._definition(node, node.name, "function")
        self.scope.append(node.name); self.scope_kinds.append("function")
        self.generic_visit(node)
        self.scope_kinds.pop(); self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._definition(node, node.name, "async_function")
        self.scope.append(node.name); self.scope_kinds.append("function")
        self.generic_visit(node)
        self.scope_kinds.pop(); self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self._definition(node, node.name, "class")
        class_id = _node_id(self.path, self._qual(node.name))
        for base in node.bases:
            name = _call_name(base)
            if name:
                self.references.append({"file": self.path, "line": int(node.lineno), "kind": "inherits", "name": name, "scope": class_id})
        self.scope.append(node.name); self.scope_kinds.append("class")
        self.generic_visit(node)
        self.scope_kinds.pop(); self.scope.pop()

    def visit_Dict(self, node: ast.Dict) -> Any:
        for key, value in zip(node.keys, node.values):
            if value is None:
                continue
            key_text = _expr_text(key) if key is not None else "<dict>"
            self._binding(node, value, "registry_binding", expression=f"{key_text}: {_expr_text(value)}")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> Any:
        targets = ", ".join(_expr_text(target) for target in node.targets)
        self._binding(node, node.value, "assignment_binding", expression=f"{targets} = {_expr_text(node.value)}")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        if node.value is not None:
            self._binding(node, node.value, "assignment_binding", expression=f"{_expr_text(node.target)} = {_expr_text(node.value)}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        name = _call_name(node.func)
        if name:
            self.calls.append({
                "file": self.path, "line": int(getattr(node, "lineno", 1)), "kind": "call",
                "name": name, "scope": self._scope_id(), "expression": _expr_text(node.func),
            })
            if name in {"getattr", "setattr", "__import__", "import_module"}:
                self.dynamic.append({
                    "file": self.path, "line": int(getattr(node, "lineno", 1)),
                    "scope": self._scope_id(), "expression": _expr_text(node.func), "reason": "dynamic_dispatch_or_import",
                })
        else:
            self.dynamic.append({
                "file": self.path, "line": int(getattr(node, "lineno", 1)),
                "scope": self._scope_id(), "expression": _expr_text(node.func), "reason": "unresolved_call_expression",
            })
        for arg in node.args:
            self._binding(node, arg, "callback_argument", expression=_expr_text(node))
        for keyword in node.keywords:
            self._binding(node, keyword.value, "callback_argument", expression=_expr_text(node))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            self.imports.append({
                "file": self.path, "line": int(node.lineno), "kind": "import", "module": alias.name,
                "name": alias.asname or alias.name.split(".")[-1], "scope": self._scope_id(),
            })
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        module = node.module or ""
        for alias in node.names:
            self.imports.append({
                "file": self.path, "line": int(node.lineno), "kind": "import_from", "module": module,
                "name": alias.name, "alias": alias.asname, "scope": self._scope_id(),
            })
        self.generic_visit(node)


def _python_index(root: str, files: Iterable[str]) -> Dict[str, Any]:
    definitions: List[Dict[str, Any]] = []
    calls: List[Dict[str, Any]] = []
    references: List[Dict[str, Any]] = []
    imports: List[Dict[str, Any]] = []
    dynamic: List[Dict[str, Any]] = []
    parse_errors: List[Dict[str, Any]] = []
    python_files = 0
    for path in files:
        if os.path.splitext(path)[1].lower() not in {".py", ".pyi"}:
            continue
        python_files += 1
        relpath = _rel(root, path)
        try:
            tree = ast.parse(_read(path), filename=relpath)
        except (OSError, SyntaxError, ValueError) as exc:
            parse_errors.append({"file": relpath, "error": type(exc).__name__})
            continue
        collector = _PythonCollector(relpath)
        collector.visit(tree)
        definitions.extend(collector.definitions)
        calls.extend(collector.calls)
        references.extend(collector.references)
        imports.extend(collector.imports)
        dynamic.extend(collector.dynamic)
    return {
        "definitions": definitions, "calls": calls, "references": references,
        "imports": imports, "dynamic": dynamic, "parse_errors": parse_errors,
        "python_files": python_files,
    }


def _text_references(root: str, files: Iterable[str], symbol: str, *, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    out: List[Dict[str, Any]] = []
    truncated = False
    for path in files:
        try:
            text = _read(path)
        except OSError:
            continue
        relpath = _rel(root, path)
        for line_no, line in enumerate(text.splitlines(), 1):
            start = 0
            while True:
                col = line.find(symbol, start)
                if col < 0:
                    break
                out.append({"file": relpath, "line": line_no, "column": col + 1, "kind": "text_reference"})
                if len(out) >= limit:
                    return out, True
                start = col + max(1, len(symbol))
    return out, truncated


def _resolve_nodes(spec: str, definitions: List[Dict[str, Any]], module_nodes: set[str]) -> List[str]:
    raw = str(spec or "").strip().replace("\\", "/")
    if not raw:
        return []
    if raw in module_nodes:
        return [raw]
    if "::" in raw:
        return [raw] if raw in module_nodes or any(d["node_id"] == raw for d in definitions) else []
    # A path denotes its module execution root.
    if "/" in raw or raw.endswith((".py", ".pyi")):
        candidate = _node_id(raw, "<module>")
        return [candidate] if candidate in module_nodes else []
    matched = [d["node_id"] for d in definitions if d.get("name") == raw or d.get("qualname") == raw]
    return matched


def _bfs_paths(adjacency: Dict[str, List[str]], starts: List[str], targets: set[str], max_depth: int) -> Optional[List[str]]:
    queue = deque((start, [start]) for start in starts)
    seen = set(starts)
    while queue:
        node, path = queue.popleft()
        if node in targets:
            return path
        if len(path) - 1 >= max_depth:
            continue
        for nxt in adjacency.get(node, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append((nxt, path + [nxt]))
    return None


def analyze_symbol_relations(
    root: str, symbol: str, *, path: Optional[str] = None, roots: Optional[List[str]] = None,
    direction: str = "both", include_text_references: bool = False,
    max_depth: int = 6, max_edges: int = 120, max_files: int = 20000,
    max_file_bytes: int = 4 * 1024 * 1024,
) -> Dict[str, Any]:
    """Return structural facts about one symbol without semantic liveness labels."""
    root = os.path.realpath(os.path.abspath(root))
    symbol = str(symbol or "").strip()
    direction = str(direction or "both").strip().lower()
    if direction not in {"incoming", "outgoing", "both"}:
        raise ValueError("direction must be incoming, outgoing or both")
    files, file_truncated, secret_skipped = _source_files(root, max_files=max_files, max_bytes=max_file_bytes)
    py = _python_index(root, files)
    definitions = [d for d in py["definitions"] if d.get("name") == symbol or d.get("qualname") == symbol]
    if path:
        normalized = str(path).replace("\\", "/").lstrip("./")
        definitions = [d for d in definitions if d.get("file") == normalized]

    defs_by_name: Dict[str, List[str]] = defaultdict(list)
    for definition in py["definitions"]:
        defs_by_name[str(definition.get("name") or "")].append(definition["node_id"])

    target_ids = {d["node_id"] for d in definitions}
    incoming: List[Dict[str, Any]] = []
    outgoing: List[Dict[str, Any]] = []
    all_edges: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []

    for call in py["calls"]:
        candidates = defs_by_name.get(str(call.get("name") or ""), [])
        if len(candidates) == 1:
            edge = {
                "from": call.get("scope"), "to": candidates[0], "kind": "call",
                "file": call.get("file"), "line": call.get("line"), "expression": call.get("expression"),
            }
            all_edges.append(edge)
            if edge["to"] in target_ids:
                incoming.append(edge)
            if edge["from"] in target_ids:
                outgoing.append(edge)
        elif call.get("name") == symbol or call.get("scope") in target_ids:
            unresolved.append({**call, "reason": "ambiguous_or_unresolved_target"})

    # Structural bindings are observable edges, not liveness verdicts. Registry,
    # assignment and callback edges are important in dispatch-heavy frameworks.
    structural_refs = []
    structural_edges: List[Dict[str, Any]] = []
    for ref in py["references"]:
        candidates = defs_by_name.get(str(ref.get("name") or ""), [])
        if len(candidates) == 1:
            edge = {
                "from": ref.get("scope"), "to": candidates[0], "kind": ref.get("kind"),
                "file": ref.get("file"), "line": ref.get("line"), "expression": ref.get("expression"),
            }
            structural_edges.append(edge)
            all_edges.append(edge)
            if edge["to"] in target_ids:
                incoming.append(edge)
            if edge["from"] in target_ids:
                outgoing.append(edge)
        if ref.get("name") == symbol or ref.get("scope") in target_ids:
            structural_refs.append(ref)

    imports = [item for item in py["imports"] if item.get("name") == symbol or item.get("alias") == symbol]
    if include_text_references:
        text_refs, text_truncated = _text_references(root, files, symbol, limit=max_edges * 2)
    else:
        text_refs, text_truncated = [], False

    module_nodes = {_node_id(_rel(root, path_value), "<module>") for path_value in files if os.path.splitext(path_value)[1].lower() in {".py", ".pyi"}}
    adjacency: Dict[str, List[str]] = defaultdict(list)
    for edge in all_edges:
        adjacency[str(edge["from"])].append(str(edge["to"]))

    root_results: List[Dict[str, Any]] = []
    for root_spec in roots or []:
        starts = _resolve_nodes(str(root_spec), py["definitions"], module_nodes)
        path_found = _bfs_paths(adjacency, starts, target_ids, max_depth) if starts and target_ids else None
        root_results.append({
            "root": str(root_spec), "resolved_nodes": starts,
            "reachable": bool(path_found), "path": path_found or [],
        })

    edge_truncated = len(incoming) > max_edges or len(outgoing) > max_edges
    incoming = incoming[:max_edges] if direction in {"incoming", "both"} else []
    outgoing = outgoing[:max_edges] if direction in {"outgoing", "both"} else []
    structural_refs = structural_refs[:max_edges]
    imports = imports[:max_edges]
    unresolved = (unresolved + py["dynamic"])[:max_edges]

    return {
        "symbol": symbol,
        "path_filter": str(path).replace("\\", "/") if path else None,
        "direction": direction, "include_text_references": bool(include_text_references),
        "backend": "python_ast+text",
        "definitions": definitions[:max_edges],
        "incoming": incoming,
        "outgoing": outgoing,
        "structural_references": structural_refs,
        "imports": imports,
        "text_references": text_refs,
        "root_reachability": root_results,
        "reachability_edge_kinds": ["call", "registry_binding", "assignment_binding", "callback_argument", "decorator", "inherits"],
        "unresolved_dynamic": unresolved,
        "coverage": {
            "files_scanned": len(files), "python_files_scanned": py["python_files"], "secret_files_skipped": secret_skipped,
            "parse_errors": py["parse_errors"][:20],
            "file_scan_truncated": bool(file_truncated), "text_references_truncated": bool(text_truncated),
            "relation_edges_truncated": bool(edge_truncated),
            "static_resolution_complete": not bool(file_truncated or text_truncated or edge_truncated or py["parse_errors"] or unresolved),
        },
        "semantics": "structural_facts_only",
    }
