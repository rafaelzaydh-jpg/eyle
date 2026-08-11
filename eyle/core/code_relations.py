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


def _is_python_main_guard(node: ast.AST) -> bool:
    """Recognize the canonical module entry guard without semantic inference."""
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq) or len(node.comparators) != 1:
        return False
    left, right = node.left, node.comparators[0]

    def is_name(value: ast.AST) -> bool:
        return isinstance(value, ast.Name) and value.id == "__name__"

    def is_main(value: ast.AST) -> bool:
        return isinstance(value, ast.Constant) and value.value == "__main__"

    return (is_name(left) and is_main(right)) or (is_main(left) and is_name(right))


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

    def visit_If(self, node: ast.If) -> Any:
        if not self.scope and _is_python_main_guard(node.test):
            for child in node.body:
                for nested in ast.walk(child):
                    if not isinstance(nested, ast.Call):
                        continue
                    name = _call_name(nested.func)
                    if not name:
                        continue
                    self.references.append({
                        "file": self.path, "line": int(getattr(nested, "lineno", getattr(node, "lineno", 1))),
                        "kind": "python_main_guard", "name": name, "scope": self._scope_id(),
                        "expression": _expr_text(nested),
                        "target_definition": _node_id(self.path, name),
                    })
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
                "name": alias.name, "alias": alias.asname, "scope": self._scope_id(), "level": int(node.level or 0),
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
    path, _ = _bfs_path_meta(adjacency, starts, targets, max_depth)
    return path


def _bfs_path_meta(
    adjacency: Dict[str, List[str]], starts: List[str], targets: set[str], max_depth: int,
) -> Tuple[Optional[List[str]], List[str]]:
    """Return the shortest path plus objective nodes blocked by the depth boundary."""
    queue = deque((start, [start]) for start in starts)
    seen = set(starts)
    depth_frontier: List[str] = []
    while queue:
        node, path = queue.popleft()
        if node in targets:
            return path, depth_frontier
        depth = len(path) - 1
        if depth >= max_depth:
            if any(nxt not in seen for nxt in adjacency.get(node, [])):
                depth_frontier.append(node)
            continue
        for nxt in adjacency.get(node, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append((nxt, path + [nxt]))
    return None, depth_frontier


def _auto_entrypoint_nodes(references: List[Dict[str, Any]], module_nodes: set[str]) -> List[str]:
    """Detect objective Python entry roots without ranking their semantic importance."""
    roots = {
        str(item.get("scope") or "")
        for item in references
        if item.get("kind") == "python_main_guard" and str(item.get("scope") or "") in module_nodes
    }
    # Conventional module entrypoint filenames are objective structural signals.
    for node in module_nodes:
        path = node.split("::", 1)[0]
        base = os.path.basename(path).lower()
        if base in {"main.py", "__main__.py"}:
            roots.add(node)
    return sorted(root for root in roots if root)


def _path_edges(path: List[str], edge_lookup: Dict[Tuple[str, str], Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for left, right in zip(path, path[1:]):
        edge = edge_lookup.get((left, right))
        if edge:
            out.append(dict(edge))
    return out



def _module_paths(root: str, definitions: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Map importable Python module names to scanned source paths.

    This is structural resolution only: package/module names are derived from the
    scanned file tree and never from runtime import execution.
    """
    paths: Dict[str, List[str]] = defaultdict(list)
    files = sorted({str(item.get("file") or "") for item in definitions if str(item.get("file") or "").endswith((".py", ".pyi"))})
    for relpath in files:
        stem = relpath.rsplit(".", 1)[0].replace("/", ".")
        module = stem[:-9] if stem.endswith(".__init__") else stem
        if module:
            paths[module].append(relpath)
    return paths


def _resolve_relative_module(importing_file: str, module: str, level: int) -> str:
    if level <= 0:
        return str(module or "")
    parts = str(importing_file or "").replace("\\", "/").split("/")[:-1]
    package = parts[: max(0, len(parts) - (level - 1))]
    suffix = [part for part in str(module or "").split(".") if part]
    return ".".join(package + suffix)


def _import_bindings(py: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    """Return file-local imported-name -> definition-node candidates.

    Calls such as ``from llm.structured import parse_profile_response`` must not
    fall back to a project-wide name guess when the import itself gives an
    objective target. This is the missing edge that made directed reachability
    collapse into manual node walking in the live Rev5.7 benchmark.
    """
    definitions = list(py.get("definitions") or [])
    by_file_name: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for item in definitions:
        by_file_name[(str(item.get("file") or ""), str(item.get("name") or ""))].append(str(item.get("node_id") or ""))
    modules = _module_paths("", definitions)
    bindings: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
    for item in py.get("imports") or []:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file") or "")
        kind = str(item.get("kind") or "")
        if kind == "import_from":
            module = _resolve_relative_module(file, str(item.get("module") or ""), int(item.get("level") or 0))
            local_name = str(item.get("alias") or item.get("name") or "")
            imported_name = str(item.get("name") or "")
            for target_file in modules.get(module, []):
                bindings[file][local_name].extend(by_file_name.get((target_file, imported_name), []))
        elif kind == "import":
            module = str(item.get("module") or "")
            local_name = str(item.get("name") or module.split(".")[0])
            for target_file in modules.get(module, []):
                bindings[file][local_name].append(_node_id(target_file, "<module>"))
    return {file: {name: list(dict.fromkeys(nodes)) for name, nodes in names.items()} for file, names in bindings.items()}


def _call_candidates(call: Dict[str, Any], py: Dict[str, Any], defs_by_name: Dict[str, List[str]], import_bindings: Dict[str, Dict[str, List[str]]]) -> List[str]:
    """Resolve one call using local/import evidence before global name fallback."""
    name = str(call.get("name") or "")
    file = str(call.get("file") or "")
    expression = str(call.get("expression") or "")
    local_defs = [
        str(item.get("node_id") or "") for item in py.get("definitions") or []
        if str(item.get("file") or "") == file and str(item.get("name") or "") == name
    ]
    if len(local_defs) == 1:
        return local_defs
    direct_import = list((import_bindings.get(file) or {}).get(name) or [])
    if len(direct_import) == 1:
        return direct_import
    if "." in expression:
        prefix = expression.split(".", 1)[0]
        module_nodes = list((import_bindings.get(file) or {}).get(prefix) or [])
        target_files = [node.split("::", 1)[0] for node in module_nodes if node.endswith("::<module>")]
        qualified = [
            str(item.get("node_id") or "") for item in py.get("definitions") or []
            if str(item.get("file") or "") in target_files and str(item.get("name") or "") == name
        ]
        if len(qualified) == 1:
            return qualified
    return list(defs_by_name.get(name, []))


def _reachable_nodes(adjacency: Dict[str, List[str]], starts: List[str], max_depth: int) -> set[str]:
    seen = set(starts)
    queue = deque((node, 0) for node in starts)
    while queue:
        node, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for nxt in adjacency.get(node, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append((nxt, depth + 1))
    return seen

def analyze_symbol_relations(
    root: str, symbol: str, *, path: Optional[str] = None, roots: Optional[List[str]] = None,
    direction: str = "both", include_text_references: bool = False,
    max_depth: int = 6, max_edges: int = 120, max_files: int = 20000,
    max_file_bytes: int = 4 * 1024 * 1024, query: str = "relations",
) -> Dict[str, Any]:
    """Return objective structural observations for one symbol.

    ``query=relations`` preserves the local relation view from Rev5.7.
    ``query=reachability`` is a query-shaped observation: it materializes the
    shortest root-to-target path and only the frontiers that can block that
    objective. If roots are omitted, objective Python entrypoint signals are used.
    The tool never labels a symbol live/dead or decides semantic sufficiency.
    """
    root = os.path.realpath(os.path.abspath(root))
    symbol = str(symbol or "").strip()
    direction = str(direction or "both").strip().lower()
    query = str(query or "relations").strip().lower()
    if direction not in {"incoming", "outgoing", "both"}:
        raise ValueError("direction must be incoming, outgoing or both")
    if query not in {"relations", "reachability"}:
        raise ValueError("query must be relations or reachability")
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
    import_bindings = _import_bindings(py)

    for call in py["calls"]:
        candidates = _call_candidates(call, py, defs_by_name, import_bindings)
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
        elif len(candidates) > 1 or call.get("name") == symbol:
            unresolved.append({**call, "reason": "ambiguous_or_unresolved_target"})

    structural_refs = []
    structural_edges: List[Dict[str, Any]] = []
    all_definition_ids = {str(item.get("node_id") or "") for item in py["definitions"]}
    for ref in py["references"]:
        explicit_target = str(ref.get("target_definition") or "")
        candidates = [explicit_target] if explicit_target and explicit_target in all_definition_ids else defs_by_name.get(str(ref.get("name") or ""), [])
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
    edge_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for edge in all_edges:
        left, right = str(edge["from"]), str(edge["to"])
        adjacency[left].append(right)
        edge_lookup.setdefault((left, right), edge)

    requested_roots = [str(item) for item in (roots or []) if str(item).strip()]
    auto_roots = not requested_roots
    root_specs = requested_roots or _auto_entrypoint_nodes(py["references"], module_nodes)
    root_results: List[Dict[str, Any]] = []
    depth_boundary_nodes: List[str] = []
    for root_spec in root_specs:
        starts = [root_spec] if auto_roots and root_spec in module_nodes else _resolve_nodes(root_spec, py["definitions"], module_nodes)
        path_found, depth_frontier = _bfs_path_meta(adjacency, starts, target_ids, max_depth) if starts and target_ids else (None, [])
        depth_boundary_nodes.extend(depth_frontier)
        root_results.append({
            "root": str(root_spec), "resolved_nodes": starts,
            "reachable": bool(path_found), "path": path_found or [],
            **({"path_edges": _path_edges(path_found, edge_lookup)} if path_found else {}),
        })

    edge_truncated = len(incoming) > max_edges or len(outgoing) > max_edges
    all_unresolved = unresolved + py["dynamic"]
    resolved_root_nodes = [
        node for item in root_results for node in (item.get("resolved_nodes") or []) if str(node)
    ]
    reachable_from_roots = _reachable_nodes(adjacency, resolved_root_nodes, max_depth) if resolved_root_nodes else set()
    # Query-shaped uncertainty: only unresolved sites on the actually explored
    # root-side coverage can block root -> target reachability. Global dynamic
    # calls elsewhere in the project are not a frontier for this property.
    material_unresolved = [
        item for item in all_unresolved
        if str(item.get("scope") or "") in reachable_from_roots
    ]

    base_coverage = {
        "files_scanned": len(files), "python_files_scanned": py["python_files"], "secret_files_skipped": secret_skipped,
        "parse_errors": py["parse_errors"][:20],
        "file_scan_truncated": bool(file_truncated), "text_references_truncated": bool(text_truncated),
        "relation_edges_truncated": bool(edge_truncated),
        "static_resolution_complete": not bool(file_truncated or text_truncated or edge_truncated or py["parse_errors"] or all_unresolved),
    }

    if query == "reachability":
        reachable = [item for item in root_results if item.get("reachable")]
        shortest = min(reachable, key=lambda item: len(item.get("path") or [])) if reachable else None
        observations: List[Dict[str, Any]] = []
        frontiers: List[Dict[str, Any]] = []
        continuation_payloads: List[Dict[str, Any]] = []

        if not definitions:
            observations.append({
                "kind": "target_resolution", "symbol": symbol, "value": "definition_not_found",
                "path_filter": str(path).replace("\\", "/") if path else None,
            })
        elif shortest:
            observations.append({
                "kind": "structural_reachability", "value": "reachable",
                "root": shortest.get("root"), "target": (shortest.get("path") or [])[-1],
                "path": list(shortest.get("path") or []), "path_edges": list(shortest.get("path_edges") or []),
            })
        else:
            observations.append({
                "kind": "structural_reachability", "value": "not_found_in_resolved_graph",
                "target_nodes": sorted(target_ids), "roots_checked": [item.get("root") for item in root_results],
                "max_depth": int(max_depth),
            })

        # A positive path already discriminates the objective. Unrelated dynamic
        # sites must not become semantic bait for more exploration. If the target
        # definition itself was not resolved, only scan/parse boundaries that can
        # hide that definition keep the objective open.
        if not shortest and not definitions:
            if py["parse_errors"]:
                continuation_payloads.append({
                    "frontier_kind": "parse_errors", "items": py["parse_errors"],
                    "summary": {"count": len(py["parse_errors"])},
                })
                frontiers.append({
                    "kind": "parse_errors", "at": "project_scan",
                    "reason": "target definition was not found, but one or more source files could not be parsed structurally",
                    "continuation_index": len(continuation_payloads) - 1,
                    "count": len(py["parse_errors"]),
                })
            if file_truncated:
                frontiers.append({
                    "kind": "scan_boundary", "at": "project_scan",
                    "reason": "target definition was not found before the project source scan reached its physical file limit",
                })
        elif not shortest and definitions:
            if not root_specs:
                frontiers.append({
                    "kind": "entrypoint_roots_unresolved", "at": "project",
                    "reason": "no objective Python entrypoint root was detected",
                })
            if depth_boundary_nodes:
                unique_nodes = sorted(set(depth_boundary_nodes))
                continuation_payloads.append({
                    "frontier_kind": "depth_boundary", "items": unique_nodes,
                    "summary": {"count": len(unique_nodes), "max_depth": int(max_depth)},
                })
                frontiers.append({
                    "kind": "depth_boundary", "at": "structural_graph",
                    "reason": f"reachability search stopped at max_depth={int(max_depth)} with unresolved continuation nodes",
                    "continuation_index": len(continuation_payloads) - 1,
                    "count": len(unique_nodes),
                })
            if material_unresolved:
                continuation_payloads.append({
                    "frontier_kind": "unresolved_dynamic", "items": material_unresolved,
                    "summary": {"count": len(material_unresolved)},
                })
                frontiers.append({
                    "kind": "unresolved_dynamic", "at": "structural_graph",
                    "reason": "dynamic or ambiguous call sites remain on the root-side coverage of this reachability query",
                    "continuation_index": len(continuation_payloads) - 1,
                    "count": len(material_unresolved),
                })
            if py["parse_errors"]:
                continuation_payloads.append({
                    "frontier_kind": "parse_errors", "items": py["parse_errors"],
                    "summary": {"count": len(py["parse_errors"])},
                })
                frontiers.append({
                    "kind": "parse_errors", "at": "project_scan",
                    "reason": "one or more source files could not be parsed structurally",
                    "continuation_index": len(continuation_payloads) - 1,
                    "count": len(py["parse_errors"]),
                })
            if file_truncated:
                frontiers.append({
                    "kind": "scan_boundary", "at": "project_scan",
                    "reason": "project source scan reached its physical file limit",
                })

        objective_complete = bool(shortest) or (not definitions and not frontiers)
        if not shortest and definitions:
            objective_complete = not bool(frontiers)
        coverage = {
            **base_coverage,
            "query": "reachability",
            "root_mode": "auto_entrypoints" if auto_roots else "explicit",
            "roots_tested": len(root_results),
            "roots_resolved": sum(1 for item in root_results if item.get("resolved_nodes")),
            "target_definitions": len(definitions),
            "shortest_path_hops": (len(shortest.get("path") or []) - 1) if shortest else None,
            "objective_complete": bool(objective_complete),
            "objective_result": "reachable" if shortest else ("target_not_resolved" if not definitions else ("not_reachable_in_resolved_graph" if objective_complete else "inconclusive")),
        }
        # In directed mode, return only materialized path/coverage rather than all
        # local relation rows. continuation_payloads are Runtime-only until the
        # tool adapter converts them into opaque handles.
        return {
            "symbol": symbol,
            "path_filter": str(path).replace("\\", "/") if path else None,
            "query": "reachability",
            "direction": direction,
            "include_text_references": bool(include_text_references),
            "backend": "python_ast+text",
            "definitions": definitions[:min(max_edges, 8)],
            "incoming": [], "outgoing": [], "structural_references": [], "imports": [], "text_references": [],
            "root_reachability": ([{"root": shortest.get("root"), "reachable": True}] if shortest else [
                {"root": item.get("root"), "reachable": bool(item.get("reachable"))}
                for item in root_results[:min(max_edges, 12)]
            ]),
            "reachability_edge_kinds": ["call", "registry_binding", "assignment_binding", "callback_argument", "decorator", "inherits", "python_main_guard"],
            "unresolved_dynamic": [],
            "observations": observations,
            "coverage": coverage,
            "frontiers": frontiers,
            "continuation_payloads": continuation_payloads,
            "semantics": "structural_facts_only",
        }

    incoming = incoming[:max_edges] if direction in {"incoming", "both"} else []
    outgoing = outgoing[:max_edges] if direction in {"outgoing", "both"} else []
    structural_refs = structural_refs[:max_edges]
    imports = imports[:max_edges]
    unresolved_view = all_unresolved[:max_edges]

    return {
        "symbol": symbol,
        "path_filter": str(path).replace("\\", "/") if path else None,
        "query": "relations",
        "direction": direction, "include_text_references": bool(include_text_references),
        "backend": "python_ast+text",
        "definitions": definitions[:max_edges],
        "incoming": incoming,
        "outgoing": outgoing,
        "structural_references": structural_refs,
        "imports": imports,
        "text_references": text_refs,
        "root_reachability": root_results,
        "reachability_edge_kinds": ["call", "registry_binding", "assignment_binding", "callback_argument", "decorator", "inherits", "python_main_guard"],
        "unresolved_dynamic": unresolved_view,
        "observations": [], "frontiers": [], "continuation_payloads": [],
        "coverage": base_coverage,
        "semantics": "structural_facts_only",
    }
